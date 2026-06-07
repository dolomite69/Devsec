from __future__ import annotations

import asyncio
import os
import pathlib
import re
import shutil
import stat

from core.store import append_log, update_build, update_stage
from core.schemas import BuildStatus, StageStatus, ProjectFile
from pipeline.executor import (
    build_image,
    remove_image,
    run_container,
    stop_preview,
    build_and_run_stack,
    compose_down,
)
from pipeline.generator import generate_dockerfile, generate_stack, is_multi_service
from pipeline.inspector import inspect_repo
from pipeline.source_fixer import fix_import_casing

_MAX_ATTEMPTS = 3

# Tracks the single most-recent successful preview so a new build can tear it
# down (stop container/stack, remove image, delete clone) before starting its own.
_LAST_PREVIEW: dict[str, str] = {
    "mode": "",            # "single" | "compose"
    "container_name": "",
    "build_id": "",
    "repo_path": "",
}


def _force_rmtree(path: str) -> None:
    """Recursively delete a directory, even read-only files.

    Git marks files inside .git as read-only on Windows, which makes a plain
    shutil.rmtree fail. The error handler clears the read-only bit and retries
    so cloned repos are always removed and never pile up under tmp/.
    """
    def _on_error(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            pass

    shutil.rmtree(path, onerror=_on_error)


def sweep_work_dir() -> int:
    """Remove leftover clones, preview containers and images from prior runs.

    The build store is in-memory, so after a restart nothing can reference an
    earlier preview. Remove orphaned `dockerforge-run-*` containers and
    `dockerforge-*` images, then delete all clone folders. Returns folders removed.
    """
    import subprocess

    # Remove orphaned preview containers — both single-run and compose stacks.
    try:
        ids = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=dockerforge-"],
            capture_output=True, text=True, timeout=30,
        ).stdout.split()
        if ids:
            subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, timeout=60)
    except Exception:
        pass

    # Remove orphaned compose networks (named <project>_default).
    try:
        nets = subprocess.run(
            ["docker", "network", "ls", "-q", "--filter", "name=dockerforge-"],
            capture_output=True, text=True, timeout=30,
        ).stdout.split()
        if nets:
            subprocess.run(["docker", "network", "rm", *nets], capture_output=True, timeout=60)
    except Exception:
        pass

    # Remove orphaned build images.
    try:
        imgs = subprocess.run(
            ["docker", "images", "-q", "dockerforge-*"],
            capture_output=True, text=True, timeout=30,
        ).stdout.split()
        if imgs:
            subprocess.run(["docker", "rmi", "-f", *imgs], capture_output=True, timeout=120)
    except Exception:
        pass

    removed = 0
    if not os.path.isdir(_WORK_DIR):
        return removed
    for name in os.listdir(_WORK_DIR):
        target = os.path.join(_WORK_DIR, name)
        if not os.path.isdir(target):
            continue
        try:
            _force_rmtree(target)
            removed += 1
        except Exception:
            pass
    return removed

# Inside Docker the volume is mounted at /work; locally fall back to backend/tmp
_WORK_DIR = os.environ.get(
    "DOCKERFORGE_WORK_DIR",
    str(pathlib.Path(__file__).resolve().parent.parent / "tmp"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _log(build_id: str, message: str) -> None:
    await append_log(build_id, message)


async def _stage(build_id: str, stage_id: int, status: StageStatus, message: str = "") -> None:
    await update_stage(build_id, stage_id, status, message)


async def _fail(build_id: str, stage_id: int, error: str) -> None:
    await _stage(build_id, stage_id, StageStatus.ERROR, error)
    await update_build(build_id, status=BuildStatus.FAILED, error=error)


def _friendly_llm_error(exc: Exception) -> str:
    """Turn a raw LLM exception into a clear, user-facing message.

    Rate-limit (HTTP 429) errors are the most common; surface the wait time and
    that it's a quota issue, not a crash.
    """
    text = str(exc)
    if "429" in text or "rate_limit" in text or "Rate limit" in text:
        wait = ""
        m = re.search(r"try again in ([0-9hm.\s]+?s)", text)
        if m:
            wait = f" Try again in ~{m.group(1).strip()}."
        daily = "per day" in text or "TPD" in text
        scope = "daily token limit" if daily else "rate limit"
        return (
            f"Groq {scope} reached — the free tier quota is exhausted.{wait} "
            "Switch GROQ_MODEL (e.g. llama-3.1-8b-instant) or wait for the quota to reset."
        )
    return text


def _extract_expose_port(dockerfile: str, default: int = 3000) -> int:
    """Read the EXPOSE port from a Dockerfile, falling back to a sane default."""
    match = re.search(r"^\s*EXPOSE\s+(\d{2,5})", dockerfile, re.IGNORECASE | re.MULTILINE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return default


def _to_project_files(files: dict[str, str]) -> list[ProjectFile]:
    """Turn a path->content dict into ordered ProjectFile records for the UI."""
    items: list[ProjectFile] = []
    for path, content in files.items():
        name = path.rsplit("/", 1)[-1].lower()
        if name in ("docker-compose.yml", "docker-compose.yaml"):
            lang = "yaml"
        elif name.endswith(".conf") or name == "nginx.conf":
            lang = "nginx"
        else:
            lang = "docker"
        items.append(ProjectFile(path=path, content=content, language=lang))

    def _rank(pf: ProjectFile) -> tuple[int, str]:
        n = pf.path.rsplit("/", 1)[-1].lower()
        if n.startswith("docker-compose"):
            return (0, pf.path)
        if n == "dockerfile":
            return (1, pf.path)
        return (2, pf.path)

    return sorted(items, key=_rank)


async def _teardown_previous_preview(build_id: str) -> None:
    """Stop the previous preview (container or compose stack), remove its image,
    and delete its clone. Keeps only the latest successful build alive.
    """
    prev_mode = _LAST_PREVIEW.get("mode", "")
    prev_container = _LAST_PREVIEW.get("container_name", "")
    prev_build_id = _LAST_PREVIEW.get("build_id", "")
    prev_repo = _LAST_PREVIEW.get("repo_path", "")

    if prev_build_id == build_id or not prev_build_id:
        return  # nothing older to remove

    if prev_mode == "compose":
        try:
            await compose_down(prev_build_id, prev_repo)
        except Exception:
            pass
    else:
        if prev_container:
            try:
                await stop_preview(prev_container)
            except Exception:
                pass
        try:
            await remove_image(prev_build_id)
        except Exception:
            pass

    if prev_repo and os.path.isdir(prev_repo):
        try:
            _force_rmtree(prev_repo)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_build_pipeline(build_id: str, repo_url: str) -> None:
    await update_build(build_id, status=BuildStatus.RUNNING)
    await _log(build_id, f"DockerForge build started for: {repo_url}")

    repo_path: str | None = None
    build_failed = False
    success = False
    host_port: int | None = None
    project_name = ""

    try:
        # ------------------------------------------------------------------
        # Stage 1: Validate URL
        # ------------------------------------------------------------------
        await _stage(build_id, 1, StageStatus.RUNNING, "Validating GitHub URL...")
        if not repo_url.startswith("https://github.com/"):
            await _fail(build_id, 1, f"Invalid GitHub URL: {repo_url!r}")
            return
        await _stage(build_id, 1, StageStatus.DONE, "URL validated")
        await _log(build_id, "Stage 1 complete: URL validated")

        # ------------------------------------------------------------------
        # Stage 2: Clone Repository
        # ------------------------------------------------------------------
        await _stage(build_id, 2, StageStatus.RUNNING, "Cloning repository...")
        await _log(build_id, f"Cloning {repo_url} into {_WORK_DIR}/{build_id}...")
        try:
            scan_result = await inspect_repo(repo_url, _WORK_DIR, build_id)
            repo_path = scan_result["local_path"]
            project_name = scan_result.get("project_name", "")
        except Exception as exc:
            await _fail(build_id, 2, f"Clone failed: {exc}")
            return
        await _stage(build_id, 2, StageStatus.DONE, "Repository cloned")
        await _log(build_id, "Stage 2 complete: Repository cloned")
        await update_build(build_id, project_name=project_name, work_dir=repo_path)

        # ------------------------------------------------------------------
        # Stage 3: Analyze Codebase
        # ------------------------------------------------------------------
        await _stage(build_id, 3, StageStatus.RUNNING, "Analyzing codebase...")
        language = scan_result.get("detected_language", "Unknown")
        file_count = len(scan_result.get("file_tree", []))
        has_df = scan_result.get("has_existing_dockerfile", False)
        await _log(build_id, f"Detected language: {language}")
        await _log(build_id, f"Files found: {file_count}")
        await _log(build_id, f"Existing Dockerfile: {has_df}")

        # Fix case-mismatched relative imports so the source builds inside a
        # case-sensitive Linux container (it may build fine on Windows/macOS).
        try:
            casing_fixes = await asyncio.to_thread(fix_import_casing, repo_path)
        except Exception as exc:
            casing_fixes = []
            await _log(build_id, f"Import-casing scan skipped: {exc}")
        if casing_fixes:
            await _log(build_id, f"Fixed {len(casing_fixes)} case-mismatched import(s):")
            for fix in casing_fixes[:20]:
                await _log(build_id, f"  {fix}")

        await _stage(build_id, 3, StageStatus.DONE, f"Language: {language}, {file_count} files")
        await _log(build_id, "Stage 3 complete: Codebase analyzed")

        # ------------------------------------------------------------------
        # Decide: single-service Dockerfile  vs  full-stack docker compose
        # ------------------------------------------------------------------
        use_stack = is_multi_service(scan_result)
        files_list: list[ProjectFile] = []

        async def _register_success(
            primary: str,
            preview_url: str,
            host_port_val: int | None,
            mode: str,
            container_name: str,
        ) -> None:
            nonlocal success, host_port
            await _stage(build_id, 7, StageStatus.DONE, "All stages complete")
            # Tear down the PREVIOUS preview so only the latest stays alive.
            await _teardown_previous_preview(build_id)
            await update_build(
                build_id,
                status=BuildStatus.SUCCESS,
                dockerfile=primary,
                files=files_list,
                is_stack=(mode == "compose"),
                preview_url=preview_url,
                container_name=container_name,
                project_name=project_name,
                work_dir=repo_path or "",
            )
            _LAST_PREVIEW.update(
                mode=mode,
                container_name=container_name,
                build_id=build_id,
                repo_path=repo_path or "",
            )
            host_port = host_port_val
            success = True
            if preview_url:
                await _log(build_id, f"App is live at {preview_url} (kept running)")
                await _log(build_id, f"Project files kept at: {repo_path}")
            await _log(build_id, "DockerForge build completed successfully!")

        if use_stack:
            # ==============================================================
            # FULL-STACK PATH — generate Dockerfiles + docker-compose, then
            # `docker compose up --build`. Preview points at the FRONTEND.
            # ==============================================================
            await _log(build_id, "Multi-service repo detected — generating a full-stack deployment")
            previous_error: str | None = None
            prev_files: dict[str, str] | None = None
            stack_files: dict[str, str] = {}

            for attempt in range(1, _MAX_ATTEMPTS + 1):
                await _log(build_id, f"Attempt {attempt}/{_MAX_ATTEMPTS}...")

                label = "Generating stack" if attempt == 1 else f"Regenerating stack (attempt {attempt})"
                await _stage(build_id, 4, StageStatus.RUNNING, f"{label} with LLM...")
                try:
                    stack_files = await generate_stack(scan_result, previous_error, prev_files)
                except Exception as exc:
                    msg = _friendly_llm_error(exc)
                    await _log(build_id, f"Stack generation failed: {msg}")
                    await _fail(build_id, 4, msg)
                    build_failed = True
                    return
                files_list[:] = _to_project_files(stack_files)
                await _stage(build_id, 4, StageStatus.DONE, "Dockerfiles + docker-compose generated")
                await _log(build_id, "Stage 4 complete: " + ", ".join(sorted(stack_files.keys())))

                await _stage(build_id, 5, StageStatus.RUNNING, f"Building stack (attempt {attempt})...")
                await _log(build_id, "Running docker compose up --build...")
                try:
                    run_ok, run_log, host_port = await build_and_run_stack(
                        stack_files, repo_path, build_id,
                    )
                except Exception as exc:
                    run_ok = False
                    run_log = f"Stack build/run crashed: {type(exc).__name__}: {exc}"
                    host_port = None
                await _log(build_id, run_log)

                if not run_ok:
                    await _log(build_id, f"Stack failed on attempt {attempt}")
                    previous_error = run_log
                    prev_files = stack_files
                    if attempt == _MAX_ATTEMPTS:
                        await _fail(build_id, 6, f"Stack failed after {_MAX_ATTEMPTS} attempts")
                        build_failed = True
                        return
                    await _stage(build_id, 5, StageStatus.ERROR, "Stack failed — retrying")
                    await _stage(build_id, 6, StageStatus.ERROR, "Stack failed — retrying")
                    continue

                await _stage(build_id, 5, StageStatus.DONE, "Stack images built")
                await _stage(build_id, 6, StageStatus.DONE, "Stack is running")
                await _log(build_id, "Stage 6 complete: full stack is running")

                preview_url = f"http://localhost:{host_port}" if host_port else ""
                await _register_success(
                    stack_files.get("docker-compose.yml", ""),
                    preview_url, host_port, "compose", "",
                )
                break

        else:
            # ==============================================================
            # SINGLE-SERVICE PATH — one Dockerfile, build + run one container.
            # ==============================================================
            await _stage(build_id, 4, StageStatus.RUNNING, "Generating Dockerfile with LLM...")
            try:
                dockerfile = await generate_dockerfile(scan_result)
            except Exception as exc:
                msg = _friendly_llm_error(exc)
                await _log(build_id, f"Dockerfile generation failed: {msg}")
                await _fail(build_id, 4, msg)
                return
            await _stage(build_id, 4, StageStatus.DONE, "Dockerfile generated")
            await _log(build_id, "Stage 4 complete: Dockerfile generated")
            files_list[:] = _to_project_files({"Dockerfile": dockerfile})

            previous_error = None

            for attempt in range(1, _MAX_ATTEMPTS + 1):
                await _log(build_id, f"Attempt {attempt}/{_MAX_ATTEMPTS}...")

                # -- Regenerate on retry --
                if attempt > 1:
                    await _stage(build_id, 4, StageStatus.RUNNING, f"Regenerating Dockerfile (attempt {attempt})...")
                    try:
                        dockerfile = await generate_dockerfile(
                            scan_result,
                            previous_error=previous_error,
                            previous_dockerfile=dockerfile,
                        )
                    except Exception as exc:
                        msg = _friendly_llm_error(exc)
                        await _log(build_id, f"Dockerfile regeneration failed: {msg}")
                        await _fail(build_id, 4, msg)
                        build_failed = True
                        return
                    await _stage(build_id, 4, StageStatus.DONE, f"Dockerfile regenerated (attempt {attempt})")
                    await _log(build_id, "Dockerfile regenerated")
                    files_list[:] = _to_project_files({"Dockerfile": dockerfile})

                # -- Build --
                await _stage(build_id, 5, StageStatus.RUNNING, f"Building image (attempt {attempt})...")
                await _log(build_id, "Running docker build...")
                try:
                    build_ok, build_log = await build_image(dockerfile, repo_path, build_id)
                except Exception as exc:
                    build_ok = False
                    build_log = f"Build crashed: {type(exc).__name__}: {exc}"
                await _log(build_id, build_log)

                if not build_ok:
                    await _log(build_id, f"Build failed on attempt {attempt}")
                    previous_error = build_log
                    if attempt == _MAX_ATTEMPTS:
                        await _fail(build_id, 5, f"Image build failed after {_MAX_ATTEMPTS} attempts")
                        build_failed = True
                        return
                    await _stage(build_id, 5, StageStatus.ERROR, "Build failed — retrying")
                    continue

                await _stage(build_id, 5, StageStatus.DONE, "Image built successfully")
                await _log(build_id, "Stage 5 complete: Docker image built")

                # -- Run --
                await _stage(build_id, 6, StageStatus.RUNNING, "Running container...")
                await _log(build_id, "Running docker container...")
                container_port = _extract_expose_port(dockerfile)
                try:
                    run_ok, run_log, host_port = await run_container(build_id, container_port)
                except Exception as exc:
                    run_ok = False
                    run_log = f"Container run crashed: {type(exc).__name__}: {exc}"
                    host_port = None
                await _log(build_id, run_log)

                if not run_ok:
                    await _log(build_id, f"Container run failed on attempt {attempt}")
                    previous_error = run_log
                    if attempt == _MAX_ATTEMPTS:
                        await _fail(build_id, 6, f"Container run failed after {_MAX_ATTEMPTS} attempts")
                        build_failed = True
                        return
                    await _stage(build_id, 6, StageStatus.ERROR, "Run failed — retrying")
                    continue

                await _stage(build_id, 6, StageStatus.DONE, "Container ran successfully")
                await _log(build_id, "Stage 6 complete: Container ran successfully")

                container_name = f"dockerforge-run-{build_id}"
                preview_url = f"http://localhost:{host_port}" if host_port else ""
                if not host_port:
                    await _log(build_id, "Build succeeded but no live preview (container did not stay up)")
                await _register_success(
                    dockerfile, preview_url, host_port,
                    "single", container_name if host_port else "",
                )
                break

        if not success and not build_failed:
            await _fail(build_id, 7, "Build exhausted all attempts without success")

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        await _log(build_id, f"Unexpected error: {type(exc).__name__}: {exc}")
        await _log(build_id, tb)
        await update_build(build_id, status=BuildStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        build_failed = True

    finally:
        # ------------------------------------------------------------------
        # Cleanup — errors are logged but never crash the function.
        # On a successful build with a live preview we KEEP the clone, the
        # image and the running container so the app stays viewable. Only
        # failed (or preview-less) builds are cleaned up here.
        # ------------------------------------------------------------------
        kept_alive = success and bool(host_port)

        if kept_alive:
            return

        if repo_path:
            try:
                _force_rmtree(repo_path)
                await _log(build_id, "Cloned repository removed")
            except Exception as exc:
                await _log(build_id, f"Warning: repo cleanup failed: {exc}")

        # Remove the built image to avoid disk bloat
        try:
            await remove_image(build_id)
            await _log(build_id, "Docker image cleaned up")
        except Exception as exc:
            await _log(build_id, f"Warning: image cleanup failed: {exc}")
