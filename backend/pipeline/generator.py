from __future__ import annotations

import asyncio
import os
import re

import httpx

# Groq exposes a fast, OpenAI-compatible chat endpoint.
_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
_DEFAULT_MODEL = "llama-3.3-70b-versatile"
_MAX_RATE_LIMIT_RETRIES = 3


def _get_provider_config() -> tuple[str, str, str]:
    """Return (endpoint, model, api_key) for the Groq API."""
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to backend/.env "
            "(get a free key at https://console.groq.com/keys)."
        )
    model = os.getenv("GROQ_MODEL", _DEFAULT_MODEL)
    return _GROQ_ENDPOINT, model, api_key

_SYSTEM_PROMPT = """\
You are a Dockerfile expert.
Output ONLY raw Dockerfile content — no markdown fences, no explanations, no commentary.

Critical rules:
- ONLY reference files that exist in the file tree provided. Never COPY a file that does not appear in the tree.
- For Python projects with pyproject.toml: you MUST "COPY . ." BEFORE running "pip install ." because pip needs ALL project files (README, LICENSE, source code, etc.) to build the wheel. Do NOT try to copy only pyproject.toml first — it will fail. If requirements.txt exists, you may COPY it first and run "pip install -r requirements.txt" for layer caching, then COPY the rest.
- For Node.js projects:
  * Use node:18-alpine or node:20-alpine as base image (NEVER use old versions like node:15 or node:12).
  * Copy package*.json first, run "npm install --production" (not plain npm install) to skip devDependencies and reduce image size.
  * Then COPY the rest of the source code.
  * START COMMAND: Look at the "scripts" section of the SPECIFIC package.json you are containerizing. If a "start" script exists, use CMD ["npm", "start"]. If there is NO "start" script, do NOT run "npm start" — it will fail with "Missing script: start". Instead run the entry file directly with CMD ["node", "<entryfile>"], where <entryfile> is the package.json "main" field (e.g. server.js) or the obvious server file present in that folder (server.js, index.js, app.js). NEVER use a dev-only script that relies on nodemon for the container start command — run "node" directly.
  * ONLY run npm scripts that are explicitly listed in the "scripts" section of package.json. Do NOT guess or invent script names like "client-install" or "build:client".
  * EXPOSE the correct port by reading it from the source code or config. Common patterns: process.env.PORT, app.listen(5000), const PORT = 8080. If the package.json or source mentions a specific port, use that. Default to 5000 for Express apps.
- MONOREPOS (repos containing multiple sub-projects such as backend/, server/, api/, frontend/, client/, admin/, web/):
  * Containerize ONLY the backend/server service (the folder whose package.json depends on express/fastify/koa/nest or whose entry file calls app.listen). Do NOT install or run the frontend/client/admin apps.
  * The docker build context is the repo ROOT. To build the backend, set WORKDIR /app then COPY only that subfolder, e.g. "COPY backend/package*.json ./", "RUN npm install --production", "COPY backend/ ./". Pick the start command from THAT subfolder's package.json using the START COMMAND rule above.
- For Python web apps (Flask, Django, FastAPI): EXPOSE the correct port (Flask=5000, Django=8000, FastAPI=8000).
- The Dockerfile must build and start successfully. If the project is a library (no server/app entrypoint), use CMD ["python", "-c", "import <package>; print('<package> loaded successfully')"] or CMD ["node", "-e", "console.log('module loaded')"] to prove it installs correctly.
- Prefer official slim/alpine base images.
- Keep the Dockerfile minimal — only: FROM, WORKDIR, COPY, RUN npm/pip install, EXPOSE, CMD. No unnecessary steps.
- Do NOT add RUN commands for tests, linting, builds, or checks — only install dependencies, copy code, and set the start command.\
"""


def _extract_error(build_log: str) -> str:
    """Extract the meaningful error from verbose docker build output."""
    important: list[str] = []
    for line in build_log.splitlines():
        stripped = line.strip()
        # Skip layer download progress lines
        if stripped.startswith("#") and ("sha256:" in stripped or "resolve " in stripped):
            continue
        # Skip empty lines in bulk
        if not stripped:
            continue
        important.append(line)
    # Return last 60 meaningful lines
    return "\n".join(important[-60:])


def _build_user_message(scan_result: dict) -> str:
    """Build the initial user message describing the project."""
    language = scan_result.get("detected_language", "Unknown")
    has_dockerfile = scan_result.get("has_existing_dockerfile", False)
    file_tree = scan_result.get("file_tree", [])
    key_files: dict[str, str] = scan_result.get("key_files", {})

    # List root-level files explicitly so the LLM knows what can be COPYed
    root_files = [f for f in file_tree if "/" not in f]

    # Detect "wrapper directory" pattern: repo root has only one directory and maybe a README
    root_dirs = [f for f in root_files if not f.startswith(".")]
    non_dir_files = [f for f in root_files if "." in f and f.lower() != "readme.md"]
    project_subdir = None
    if not non_dir_files or (len(non_dir_files) == 0 and len(root_dirs) == 1):
        # Check if a single subdirectory contains the actual project files
        candidate_dirs = set()
        for entry in file_tree:
            if "/" in entry:
                top = entry.split("/")[0]
                candidate_dirs.add(top)
        # If all files are under one directory, that's likely the project root
        if len(candidate_dirs) == 1:
            project_subdir = candidate_dirs.pop()

    lines: list[str] = [
        f"Detected language: {language}",
        f"Existing Dockerfile present: {has_dockerfile}",
    ]

    if project_subdir:
        lines.append("")
        lines.append(f"IMPORTANT: The project files are inside a subdirectory called '{project_subdir}/'.")
        lines.append(f"The docker build context is the repo root. To access project files, use:")
        lines.append(f"  COPY {project_subdir}/ /app/")
        lines.append(f"Or set WORKDIR /app and COPY {project_subdir}/. .")
        lines.append(f"Do NOT use 'COPY . .' alone — that copies the wrapper directory, not the project files directly.")

    lines.extend([
        "",
        "ROOT-LEVEL FILES (these are the ONLY files you can COPY directly):",
        *[f"  {f}" for f in root_files],
        "",
        "Full file tree (up to 3 levels):",
        *[f"  {entry}" for entry in file_tree],
    ])

    if key_files:
        lines.append("")
        lines.append("Key file contents:")
        for filename, content in key_files.items():
            lines.append(f"\n--- {filename} ---")
            lines.append(content)

    if key_files:
        lines.append("")
        lines.append("Key file contents:")
        for filename, content in key_files.items():
            lines.append(f"\n--- {filename} ---")
            lines.append(content)

    # Surface EVERY package.json in the repo (monorepos have several). For each,
    # extract the scripts/main so the LLM picks the right start command and the
    # right sub-project to containerize.
    import json as _json
    import re as _re

    manifests: dict[str, str] = scan_result.get("manifests", {})
    package_manifests = {
        path: content
        for path, content in manifests.items()
        if path.rsplit("/", 1)[-1] == "package.json"
    }

    if package_manifests:
        lines.append("")
        if len(package_manifests) > 1:
            lines.append(
                "MONOREPO DETECTED — multiple package.json files found. "
                "Containerize the BACKEND/SERVER service only (the one depending "
                "on express/fastify/etc. or whose entry calls app.listen)."
            )
        lines.append("PACKAGE.JSON FILES (path → details):")
        for path, content in sorted(package_manifests.items()):
            lines.append(f"\n  {path}:")
            try:
                pkg = _json.loads(content)
            except Exception:
                lines.append("    (could not parse)")
                continue
            scripts = pkg.get("scripts", {})
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            is_server = any(d in deps for d in ("express", "fastify", "koa", "@nestjs/core", "hapi"))
            lines.append(f"    role: {'BACKEND/SERVER' if is_server else 'frontend/other'}")
            lines.append(f"    available scripts: {list(scripts.keys())}")
            if scripts.get("start"):
                lines.append(f"    start script: \"{scripts['start']}\"  -> use CMD [\"npm\", \"start\"]")
            else:
                entry = pkg.get("main") or "server.js"
                lines.append(
                    f"    NO start script — use CMD [\"node\", \"{entry}\"] "
                    f"(main field: {pkg.get('main', 'not set')})"
                )
            port_match = _re.search(r'(?:PORT|port)[=:\s]+(\d{4,5})', content)
            if port_match:
                lines.append(f"    detected port: {port_match.group(1)}")

    lines.append("")
    lines.append("Generate a Dockerfile for this project.")
    return "\n".join(lines)


def _build_messages(
    scan_result: dict,
    previous_error: str | None = None,
    previous_dockerfile: str | None = None,
) -> list[dict]:
    """Build the chat messages list, including retry context if applicable."""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(scan_result)},
    ]

    if previous_dockerfile and previous_error:
        # Show the LLM what it previously generated
        messages.append({"role": "assistant", "content": previous_dockerfile})
        # Then show the error and ask for a fix
        error_summary = _extract_error(previous_error)
        messages.append({
            "role": "user",
            "content": (
                "The Dockerfile above FAILED to build with this error:\n\n"
                f"{error_summary}\n\n"
                "INSTRUCTIONS FOR THE FIX:\n"
                "- Read the error carefully. If a file was not found during COPY, "
                "check the ROOT-LEVEL FILES list above and only COPY files that exist.\n"
                "- If 'pip install .' failed because of missing files (README.md, LICENSE, source code), "
                "you MUST do 'COPY . .' BEFORE 'RUN pip install .' — pip needs all project files.\n"
                "- Do NOT repeat the same Dockerfile. Make the specific fix needed.\n"
                "- Output ONLY the corrected Dockerfile, nothing else."
            ),
        })

    return messages


async def generate_dockerfile(
    scan_result: dict,
    previous_error: str | None = None,
    previous_dockerfile: str | None = None,
) -> str:
    endpoint, model, api_key = _get_provider_config()

    # Increase temperature on retries to avoid repeating the same output
    temperature = 0.3 if previous_error else 0.2

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": _build_messages(scan_result, previous_error, previous_dockerfile),
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    # Groq is fast; a 120s ceiling is plenty even for large prompts.
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = None
        for attempt in range(_MAX_RATE_LIMIT_RETRIES):
            response = await client.post(endpoint, json=payload, headers=headers)
            if response.status_code == 429:
                wait = 30
                try:
                    msg = response.json().get("error", {}).get("message", "")
                    m = re.search(r"(\d+(?:\.\d+)?)s", msg)
                    if m:
                        wait = min(int(float(m.group(1))) + 2, 120)
                except Exception:
                    pass
                if attempt < _MAX_RATE_LIMIT_RETRIES - 1:
                    await asyncio.sleep(wait)
                    continue
            break

    if response is None or response.status_code != 200:
        raise RuntimeError(
            f"LLM API returned HTTP {response.status_code if response else 'N/A'}: "
            f"{response.text if response else 'no response'}"
        )

    try:
        data = response.json()
        content: str = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise RuntimeError(
            f"Malformed response from LLM API: {exc}\nRaw response: {response.text}"
        ) from exc

    content = content.strip()
    if not content:
        raise RuntimeError("LLM API returned an empty Dockerfile.")

    # Strip markdown fences the LLM may add despite instructions
    if content.startswith("```"):
        lines = content.splitlines()
        # Remove opening fence (```dockerfile or ```)
        lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    return content
