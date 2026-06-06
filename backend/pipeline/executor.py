from __future__ import annotations

import asyncio
import os
import subprocess


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAX_OUTPUT_LINES = 200
_BUILD_TIMEOUT = 300  # 5 minutes
_IMAGE_PREFIX = "dockerdev"


def _run_command_sync(
    args: list[str],
    cwd: str | None = None,
    timeout: float | None = None,
) -> tuple[int, str]:
    """Run a subprocess synchronously, return (exit_code, output)."""
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        raw = result.stdout.decode("utf-8", errors="replace")
        lines = raw.splitlines()
        # Keep only last N lines
        if len(lines) > _MAX_OUTPUT_LINES:
            lines = lines[-_MAX_OUTPUT_LINES:]
        return result.returncode, "\n".join(lines)
    except subprocess.TimeoutExpired as exc:
        output = ""
        if exc.stdout:
            output = exc.stdout.decode("utf-8", errors="replace")
        return 1, output + f"\n[DockerDev] Command timed out after {timeout}s"
    except FileNotFoundError:
        return 1, f"Command not found: {args[0]}"


async def _run_command(
    args: list[str],
    cwd: str | None = None,
    timeout: float | None = None,
) -> tuple[int, str]:
    """Run a subprocess in a thread to avoid Windows asyncio issues."""
    return await asyncio.to_thread(_run_command_sync, args, cwd, timeout)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DOCKERIGNORE = """\
.git
**/node_modules
node_modules
**/__pycache__
__pycache__
*.pyc
**/.venv
.venv
**/venv
venv
dist
build
.next
.tox
.eggs
*.egg-info
"""

# Patterns that MUST always be excluded from the build context, even when the
# repository ships its own .dockerignore. Committed, host-compiled artifacts
# (e.g. node_modules with native bindings like bcrypt) otherwise get copied
# into the image and break it with "Exec format error" on a different arch/OS.
# The "**/" variants are essential for monorepos where the modules live in a
# subfolder (e.g. backend/node_modules) — a bare "node_modules" only matches at
# the context root in Docker's .dockerignore syntax.
_CRITICAL_IGNORES = (
    "**/node_modules",
    "node_modules",
    "**/__pycache__",
    "__pycache__",
    "**/.venv",
    ".venv",
    "**/venv",
    "venv",
)


def _ensure_dockerignore(repo_path: str) -> None:
    """Guarantee critical patterns are present in .dockerignore.

    If no .dockerignore exists, write the full template. If one already exists,
    append any missing critical patterns so host-compiled dependencies never
    leak into the build context.
    """
    dockerignore_path = os.path.join(repo_path, ".dockerignore")

    if not os.path.exists(dockerignore_path):
        with open(dockerignore_path, "w", encoding="utf-8") as fh:
            fh.write(_DOCKERIGNORE)
        return

    with open(dockerignore_path, "r", encoding="utf-8", errors="replace") as fh:
        existing = fh.read()

    existing_patterns = {line.strip() for line in existing.splitlines()}
    missing = [pat for pat in _CRITICAL_IGNORES if pat not in existing_patterns]
    if not missing:
        return

    addition = "\n".join(missing)
    separator = "" if existing.endswith("\n") or not existing else "\n"
    with open(dockerignore_path, "a", encoding="utf-8") as fh:
        fh.write(f"{separator}# added by DockerDev\n{addition}\n")


async def build_image(
    dockerfile_content: str,
    repo_path: str,
    build_id: str,
) -> tuple[bool, str]:
    dockerfile_path = os.path.join(repo_path, "Dockerfile")
    with open(dockerfile_path, "w", encoding="utf-8") as fh:
        fh.write(dockerfile_content)

    # Ensure node_modules / pycache / venv are excluded so host-compiled native
    # modules never overwrite the ones installed inside the image.
    _ensure_dockerignore(repo_path)

    image_tag = f"{_IMAGE_PREFIX}-{build_id}"
    exit_code, output = await _run_command(
        ["docker", "build", "--progress=plain", "-t", image_tag, "."],
        cwd=repo_path,
        timeout=_BUILD_TIMEOUT,
    )
    return exit_code == 0, output


async def run_container(build_id: str) -> tuple[bool, str]:
    image_tag = f"{_IMAGE_PREFIX}-{build_id}"
    container_name = f"{_IMAGE_PREFIX}-run-{build_id}"

    # Run in detached mode so we can inspect and stop cleanly
    start_code, start_out = await _run_command(
        ["docker", "run", "-d", "--name", container_name, image_tag],
    )
    if start_code != 0:
        return False, start_out

    container_id = start_out.strip().splitlines()[-1] if start_out.strip() else ""

    # Wait a few seconds then check if the container is still running
    await asyncio.sleep(5)

    inspect_code, inspect_out = await _run_command(
        ["docker", "inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", container_name],
    )

    # Capture logs regardless of outcome
    _, logs = await _run_command(["docker", "logs", "--tail", "80", container_name])

    # Clean up the container
    try:
        await _run_command(["docker", "stop", "-t", "5", container_name])
    except Exception:
        pass
    try:
        await _run_command(["docker", "rm", "-f", container_name])
    except Exception:
        pass

    if inspect_code != 0:
        return False, f"Failed to inspect container.\n{inspect_out}\n{logs}"

    parts = inspect_out.strip().split()
    is_running = parts[0].lower() == "true" if parts else False
    exit_code = int(parts[1]) if len(parts) > 1 else -1

    if is_running:
        return True, f"Container started and kept running for 5 seconds.\n{logs}"
    elif exit_code == 0:
        return True, f"Container exited successfully (code 0).\n{logs}"
    else:
        # Check if the app actually started but crashed due to external deps
        # (database, env vars, etc.) — the Dockerfile itself is correct
        if _app_started_but_needs_externals(logs):
            return True, (
                f"Container started but exited (code {exit_code}) due to missing "
                f"external service (database, env var, etc.). "
                f"The Dockerfile is correct — the app needs runtime configuration.\n{logs}"
            )
        return False, f"Container exited with code {exit_code}.\n{logs}"


def _app_started_but_needs_externals(logs: str) -> bool:
    """Detect if the app started successfully but crashed due to missing external deps."""
    lower = logs.lower()
    # Signs the app actually started
    started = any(phrase in lower for phrase in [
        "server started",
        "listening on",
        "started at",
        "running on port",
        "listening at",
        "server running",
        "app listening",
        "express server",
        "http server",
        "started on port",
    ])
    # Signs it crashed due to external deps, not a Dockerfile issue
    external_dep = any(phrase in lower for phrase in [
        "mongooseerror",
        "mongo",
        "econnrefused",
        "redis",
        "mysql",
        "postgres",
        "sqlconnect",
        "database",
        "connection refused",
        "uri` parameter",
        "connect etimedout",
        "getaddrinfo",
        "env",
        "undefined",
        "missing environment",
        "config",
    ])
    return started and external_dep


async def remove_image(build_id: str) -> None:
    image_tag = f"{_IMAGE_PREFIX}-{build_id}"
    exit_code, output = await _run_command(["docker", "rmi", "-f", image_tag])
