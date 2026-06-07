from __future__ import annotations

import asyncio
import os
import subprocess


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAX_OUTPUT_LINES = 200
_BUILD_TIMEOUT = 300  # 5 minutes
_IMAGE_PREFIX = "dockerforge"


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
        return 1, output + f"\n[DockerForge] Command timed out after {timeout}s"
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
        fh.write(f"{separator}# added by DockerForge\n{addition}\n")


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


async def run_container(
    build_id: str,
    container_port: int = 3000,
) -> tuple[bool, str, int | None]:
    """Start the built image and verify it stays up.

    On success the container is LEFT RUNNING (so the app can be previewed in a
    browser) and the auto-assigned host port is returned. On failure the
    container is stopped and removed. Returns (ok, log, host_port).
    """
    image_tag = f"{_IMAGE_PREFIX}-{build_id}"
    container_name = f"{_IMAGE_PREFIX}-run-{build_id}"

    # Publish the app's port on a random free host port, bound to localhost.
    start_code, start_out = await _run_command(
        [
            "docker", "run", "-d",
            "--name", container_name,
            "-p", f"127.0.0.1:0:{container_port}",
            image_tag,
        ],
    )
    if start_code != 0:
        await _force_remove_container(container_name)
        return False, start_out, None

    # Wait a few seconds then check if the container is still running
    await asyncio.sleep(5)

    inspect_code, inspect_out = await _run_command(
        ["docker", "inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", container_name],
    )

    # Capture logs regardless of outcome
    _, logs = await _run_command(["docker", "logs", "--tail", "80", container_name])

    if inspect_code != 0:
        await _force_remove_container(container_name)
        return False, f"Failed to inspect container.\n{inspect_out}\n{logs}", None

    parts = inspect_out.strip().split()
    is_running = parts[0].lower() == "true" if parts else False
    exit_code = int(parts[1]) if len(parts) > 1 else -1

    if is_running:
        # Healthy & still up — read the published host port and KEEP it running.
        host_port = await _read_host_port(container_name, container_port)
        return True, f"Container started and is running.\n{logs}", host_port

    # Not running anymore — tear it down, no live preview possible.
    await _force_remove_container(container_name)

    if exit_code == 0:
        return True, f"Container exited successfully (code 0).\n{logs}", None

    # Check if the app actually started but crashed due to external deps
    # (database, env vars, etc.) — the Dockerfile itself is correct
    if _app_started_but_needs_externals(logs):
        return True, (
            f"Container started but exited (code {exit_code}) due to missing "
            f"external service (database, env var, etc.). "
            f"The Dockerfile is correct — the app needs runtime configuration.\n{logs}"
        ), None
    return False, f"Container exited with code {exit_code}.\n{logs}", None


async def _read_host_port(container_name: str, container_port: int) -> int | None:
    """Read the host port Docker mapped to the container's exposed port."""
    code, out = await _run_command(
        ["docker", "port", container_name, f"{container_port}/tcp"],
    )
    if code != 0 or not out.strip():
        return None
    # Output looks like "127.0.0.1:54321" (possibly multiple lines)
    last = out.strip().splitlines()[-1].strip()
    if ":" in last:
        try:
            return int(last.rsplit(":", 1)[1])
        except ValueError:
            return None
    return None


async def _force_remove_container(container_name: str) -> None:
    try:
        await _run_command(["docker", "stop", "-t", "5", container_name])
    except Exception:
        pass
    try:
        await _run_command(["docker", "rm", "-f", container_name])
    except Exception:
        pass


async def stop_preview(container_name: str) -> None:
    """Public helper to stop & remove a running preview container."""
    if container_name:
        await _force_remove_container(container_name)


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


# ===========================================================================
# Full-stack (docker compose) build & run
# ===========================================================================

_COMPOSE_TIMEOUT = 600  # 10 minutes — frontend builds can be slow


def _compose_project(build_id: str) -> str:
    """A valid compose project name (lowercase, alnum + dash)."""
    return f"{_IMAGE_PREFIX}-{build_id}".lower()


def write_stack_files(files: dict[str, str], repo_path: str) -> list[str]:
    """Write generated stack files into the repo and add .dockerignore per service.

    Returns the list of written relative paths.
    """
    written: list[str] = []
    for rel_path, content in files.items():
        # Guard against path traversal in LLM output.
        safe_rel = rel_path.replace("\\", "/").lstrip("/")
        if ".." in safe_rel.split("/"):
            continue
        abs_path = os.path.join(repo_path, *safe_rel.split("/"))
        os.makedirs(os.path.dirname(abs_path) or repo_path, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        written.append(safe_rel)

        # Drop a .dockerignore next to every Dockerfile so committed
        # node_modules / venvs never leak into a service build context.
        if safe_rel.rsplit("/", 1)[-1] == "Dockerfile":
            service_dir = os.path.dirname(abs_path) or repo_path
            _ensure_dockerignore(service_dir)

    return written


async def build_and_run_stack(
    files: dict[str, str],
    repo_path: str,
    build_id: str,
    frontend_service: str = "frontend",
    frontend_port: int = 80,
) -> tuple[bool, str, int | None]:
    """Write stack files, `docker compose up -d --build`, verify, read frontend port.

    On success the whole stack is LEFT RUNNING and the frontend's host port is
    returned. On failure everything is torn down. Returns (ok, log, host_port).
    """
    write_stack_files(files, repo_path)
    project = _compose_project(build_id)

    build_code, build_out = await _run_command(
        ["docker", "compose", "-p", project, "up", "-d", "--build"],
        cwd=repo_path,
        timeout=_COMPOSE_TIMEOUT,
    )
    if build_code != 0:
        await compose_down(build_id, repo_path)
        return False, build_out, None

    # Give services a moment to boot, then collect status + logs.
    await asyncio.sleep(6)

    _, ps_out = await _run_command(
        ["docker", "compose", "-p", project, "ps"],
        cwd=repo_path,
    )
    _, logs = await _run_command(
        ["docker", "compose", "-p", project, "logs", "--tail", "60"],
        cwd=repo_path,
    )

    host_port = await _read_compose_port(project, repo_path, frontend_service, frontend_port)
    combined = f"{build_out}\n{ps_out}\n{logs}".strip()

    if host_port is None:
        # Could not map the frontend port — treat as failure and clean up.
        await compose_down(build_id, repo_path)
        return False, f"Could not determine frontend port.\n{combined}", None

    return True, f"Stack is up.\n{combined}", host_port


async def _read_compose_port(
    project: str,
    repo_path: str,
    service: str,
    container_port: int,
) -> int | None:
    code, out = await _run_command(
        ["docker", "compose", "-p", project, "port", service, str(container_port)],
        cwd=repo_path,
    )
    if code != 0 or not out.strip():
        return None
    last = out.strip().splitlines()[-1].strip()
    if ":" in last:
        try:
            return int(last.rsplit(":", 1)[1])
        except ValueError:
            return None
    return None


async def compose_down(build_id: str, repo_path: str) -> None:
    """Stop and remove a compose stack (containers, networks, local images, volumes)."""
    project = _compose_project(build_id)
    try:
        await _run_command(
            ["docker", "compose", "-p", project, "down", "--rmi", "local", "-v", "--remove-orphans"],
            cwd=repo_path,
            timeout=120,
        )
    except Exception:
        pass
