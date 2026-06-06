from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import stat

from core.store import append_log, update_build, update_stage
from core.schemas import BuildStatus, StageStatus
from pipeline.executor import build_image, remove_image, run_container
from pipeline.generator import generate_dockerfile
from pipeline.inspector import inspect_repo

_MAX_ATTEMPTS = 3


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
    """Remove leftover clone folders from interrupted builds. Returns count removed."""
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

# Inside Docker the volume is mounted at /work; locally fall back to <project>/tmp
_WORK_DIR = os.environ.get(
    "DOCKERDEV_WORK_DIR",
    str(pathlib.Path(__file__).resolve().parent.parent.parent / "tmp"),
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


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_build_pipeline(build_id: str, repo_url: str) -> None:
    await update_build(build_id, status=BuildStatus.RUNNING)
    await _log(build_id, f"DockerDev build started for: {repo_url}")

    repo_path: str | None = None
    build_failed = False

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
        except Exception as exc:
            await _fail(build_id, 2, f"Clone failed: {exc}")
            return
        await _stage(build_id, 2, StageStatus.DONE, "Repository cloned")
        await _log(build_id, "Stage 2 complete: Repository cloned")

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
        await _stage(build_id, 3, StageStatus.DONE, f"Language: {language}, {file_count} files")
        await _log(build_id, "Stage 3 complete: Codebase analyzed")

        # ------------------------------------------------------------------
        # Stage 4: Generate Dockerfile
        # ------------------------------------------------------------------
        await _stage(build_id, 4, StageStatus.RUNNING, "Generating Dockerfile with LLM...")
        try:
            dockerfile = await generate_dockerfile(scan_result)
        except Exception as exc:
            await _fail(build_id, 4, f"Dockerfile generation failed: {exc}")
            return
        await _stage(build_id, 4, StageStatus.DONE, "Dockerfile generated")
        await _log(build_id, "Stage 4 complete: Dockerfile generated")

        # ------------------------------------------------------------------
        # Stages 5 + 6: Build & Run (with retry)
        # ------------------------------------------------------------------
        previous_error: str | None = None
        success = False

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
                    await _fail(build_id, 4, f"Dockerfile regeneration failed: {exc}")
                    build_failed = True
                    return
                await _stage(build_id, 4, StageStatus.DONE, f"Dockerfile regenerated (attempt {attempt})")
                await _log(build_id, "Dockerfile regenerated")

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
            try:
                run_ok, run_log = await run_container(build_id)
            except Exception as exc:
                run_ok = False
                run_log = f"Container run crashed: {type(exc).__name__}: {exc}"
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

            # -- Success --
            await _stage(build_id, 6, StageStatus.DONE, "Container ran successfully")
            await _log(build_id, "Stage 6 complete: Container ran successfully")
            await _stage(build_id, 7, StageStatus.DONE, "All stages complete")
            await update_build(build_id, status=BuildStatus.SUCCESS, dockerfile=dockerfile)
            await _log(build_id, "DockerDev build completed successfully!")
            success = True
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
        # Cleanup — errors are logged but never crash the function
        # ------------------------------------------------------------------
        if repo_path:
            try:
                _force_rmtree(repo_path)
                await _log(build_id, "Cloned repository removed")
            except Exception as exc:
                await _log(build_id, f"Warning: repo cleanup failed: {exc}")

        # Always remove the built image to avoid disk bloat
        try:
            await remove_image(build_id)
            await _log(build_id, "Docker image cleaned up")
        except Exception as exc:
            await _log(build_id, f"Warning: image cleanup failed: {exc}")
