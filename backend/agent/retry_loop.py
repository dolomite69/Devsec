from __future__ import annotations

import asyncio
import os
import pathlib
import shutil

from agent.docker_runner import build_image, remove_image, run_container
from agent.dockerfile_gen import generate_dockerfile
from agent.repo_scanner import scan_repo
from job_store import append_log, update_job, update_step
from models import JobStatus, StepStatus

_MAX_ATTEMPTS = 3

# Inside Docker the volume is mounted at /work; locally fall back to <project>/tmp
_WORK_DIR = os.environ.get(
    "DOCKERFORGE_WORK_DIR",
    str(pathlib.Path(__file__).resolve().parent.parent.parent / "tmp"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _log(job_id: str, message: str) -> None:
    await append_log(job_id, message)


async def _step(job_id: str, step_id: int, status: StepStatus, message: str = "") -> None:
    await update_step(job_id, step_id, status, message)


async def _fail(job_id: str, step_id: int, error: str) -> None:
    await _step(job_id, step_id, StepStatus.ERROR, error)
    await update_job(job_id, status=JobStatus.FAILED, error=error)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_forge_agent(job_id: str, github_url: str) -> None:
    await update_job(job_id, status=JobStatus.RUNNING)
    await _log(job_id, f"DockerForge job started for: {github_url}")

    repo_path: str | None = None
    job_failed = False

    try:
        # ------------------------------------------------------------------
        # Step 1: Validate URL
        # ------------------------------------------------------------------
        await _step(job_id, 1, StepStatus.RUNNING, "Validating GitHub URL...")
        if not github_url.startswith("https://github.com/"):
            await _fail(job_id, 1, f"Invalid GitHub URL: {github_url!r}")
            return
        await _step(job_id, 1, StepStatus.DONE, "URL validated")
        await _log(job_id, "Step 1 complete: URL validated")

        # ------------------------------------------------------------------
        # Step 2: Clone Repository
        # ------------------------------------------------------------------
        await _step(job_id, 2, StepStatus.RUNNING, "Cloning repository...")
        await _log(job_id, f"Cloning {github_url} into {_WORK_DIR}/{job_id}...")
        try:
            scan_result = await scan_repo(github_url, _WORK_DIR, job_id)
            repo_path = scan_result["local_path"]
        except Exception as exc:
            await _fail(job_id, 2, f"Clone failed: {exc}")
            return
        await _step(job_id, 2, StepStatus.DONE, "Repository cloned")
        await _log(job_id, "Step 2 complete: Repository cloned")

        # ------------------------------------------------------------------
        # Step 3: Analyze Codebase
        # ------------------------------------------------------------------
        await _step(job_id, 3, StepStatus.RUNNING, "Analyzing codebase...")
        language = scan_result.get("detected_language", "Unknown")
        file_count = len(scan_result.get("file_tree", []))
        has_df = scan_result.get("has_existing_dockerfile", False)
        await _log(job_id, f"Detected language: {language}")
        await _log(job_id, f"Files found: {file_count}")
        await _log(job_id, f"Existing Dockerfile: {has_df}")
        await _step(job_id, 3, StepStatus.DONE, f"Language: {language}, {file_count} files")
        await _log(job_id, "Step 3 complete: Codebase analyzed")

        # ------------------------------------------------------------------
        # Step 4: Generate Dockerfile
        # ------------------------------------------------------------------
        await _step(job_id, 4, StepStatus.RUNNING, "Generating Dockerfile with LLM...")
        try:
            dockerfile = await generate_dockerfile(scan_result)
        except Exception as exc:
            await _fail(job_id, 4, f"Dockerfile generation failed: {exc}")
            return
        await _step(job_id, 4, StepStatus.DONE, "Dockerfile generated")
        await _log(job_id, "Step 4 complete: Dockerfile generated")

        # ------------------------------------------------------------------
        # Steps 5 + 6: Build & Run (with retry)
        # ------------------------------------------------------------------
        previous_error: str | None = None
        success = False

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            await _log(job_id, f"Attempt {attempt}/{_MAX_ATTEMPTS}...")

            # -- Regenerate on retry --
            if attempt > 1:
                await _step(job_id, 4, StepStatus.RUNNING, f"Regenerating Dockerfile (attempt {attempt})...")
                try:
                    dockerfile = await generate_dockerfile(
                        scan_result,
                        previous_error=previous_error,
                        previous_dockerfile=dockerfile,
                    )
                except Exception as exc:
                    await _fail(job_id, 4, f"Dockerfile regeneration failed: {exc}")
                    job_failed = True
                    return
                await _step(job_id, 4, StepStatus.DONE, f"Dockerfile regenerated (attempt {attempt})")
                await _log(job_id, "Dockerfile regenerated")

            # -- Build --
            await _step(job_id, 5, StepStatus.RUNNING, f"Building image (attempt {attempt})...")
            await _log(job_id, "Running docker build...")
            try:
                build_ok, build_log = await build_image(dockerfile, repo_path, job_id)
            except Exception as exc:
                build_ok = False
                build_log = f"Build crashed: {type(exc).__name__}: {exc}"
            await _log(job_id, build_log)

            if not build_ok:
                await _log(job_id, f"Build failed on attempt {attempt}")
                previous_error = build_log
                if attempt == _MAX_ATTEMPTS:
                    await _fail(job_id, 5, f"Image build failed after {_MAX_ATTEMPTS} attempts")
                    job_failed = True
                    return
                await _step(job_id, 5, StepStatus.ERROR, "Build failed — retrying")
                continue

            await _step(job_id, 5, StepStatus.DONE, "Image built successfully")
            await _log(job_id, "Step 5 complete: Docker image built")

            # -- Run --
            await _step(job_id, 6, StepStatus.RUNNING, "Running container...")
            await _log(job_id, "Running docker container...")
            try:
                run_ok, run_log = await run_container(job_id)
            except Exception as exc:
                run_ok = False
                run_log = f"Container run crashed: {type(exc).__name__}: {exc}"
            await _log(job_id, run_log)

            if not run_ok:
                await _log(job_id, f"Container run failed on attempt {attempt}")
                previous_error = run_log
                if attempt == _MAX_ATTEMPTS:
                    await _fail(job_id, 6, f"Container run failed after {_MAX_ATTEMPTS} attempts")
                    job_failed = True
                    return
                await _step(job_id, 6, StepStatus.ERROR, "Run failed — retrying")
                continue

            # -- Success --
            await _step(job_id, 6, StepStatus.DONE, "Container ran successfully")
            await _log(job_id, "Step 6 complete: Container ran successfully")
            await _step(job_id, 7, StepStatus.DONE, "All steps complete")
            await update_job(job_id, status=JobStatus.SUCCESS, dockerfile=dockerfile)
            await _log(job_id, "DockerForge job completed successfully!")
            success = True
            break

        if not success and not job_failed:
            await _fail(job_id, 7, "Job exhausted all attempts without success")

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        await _log(job_id, f"Unexpected error: {type(exc).__name__}: {exc}")
        await _log(job_id, tb)
        await update_job(job_id, status=JobStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        job_failed = True

    finally:
        # ------------------------------------------------------------------
        # Cleanup — errors are logged but never crash the function
        # ------------------------------------------------------------------
        if repo_path:
            try:
                shutil.rmtree(repo_path, ignore_errors=True)
                await _log(job_id, "Cloned repository removed")
            except Exception as exc:
                await _log(job_id, f"Warning: repo cleanup failed: {exc}")

        # Always remove the built image to avoid disk bloat
        try:
            await remove_image(job_id)
            await _log(job_id, "Docker image cleaned up")
        except Exception as exc:
            await _log(job_id, f"Warning: image cleanup failed: {exc}")
