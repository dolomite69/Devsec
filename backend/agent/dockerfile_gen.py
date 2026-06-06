from __future__ import annotations

import asyncio
import os
import re

import httpx

# Provider endpoints
_PROVIDERS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
}
_DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o-mini",
}
_MAX_RATE_LIMIT_RETRIES = 3


def _get_provider_config() -> tuple[str, str, str]:
    """Return (endpoint, model, api_key) based on .env settings."""
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    api_key = os.getenv("GROQ_API_KEY", "")

    endpoint = _PROVIDERS.get(provider, _PROVIDERS["openai"])
    default_model = _DEFAULT_MODELS.get(provider, "gpt-4o-mini")
    model = os.getenv("GROQ_MODEL", default_model)

    # Auto-detect: if key starts with sk- it's OpenAI
    if api_key.startswith("sk-") and provider == "groq":
        endpoint = _PROVIDERS["openai"]
        if model.startswith("llama"):
            model = _DEFAULT_MODELS["openai"]

    return endpoint, model, api_key

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
  * ALWAYS use CMD ["npm", "start"] as the entrypoint — do NOT guess filenames like server.js or index.js.
  * ONLY run npm scripts that are explicitly listed in the "scripts" section of package.json. Do NOT guess or invent script names like "client-install" or "build:client".
  * For monorepo projects with a "client" or "frontend" folder: do NOT install client/frontend dependencies in the Docker image. Only install and run the backend server.
  * EXPOSE the correct port by reading it from the source code or config. Common patterns: process.env.PORT, app.listen(5000), const PORT = 8080. If the package.json or source mentions a specific port, use that. Default to 5000 for Express apps.
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

    # Extract actionable info from package.json for Node.js projects
    pkg_content = key_files.get("package.json", "")
    if pkg_content:
        import json as _json
        try:
            pkg = _json.loads(pkg_content)
            scripts = pkg.get("scripts", {})
            lines.append("")
            lines.append("EXTRACTED FROM package.json:")
            if scripts.get("start"):
                lines.append(f"  start script: \"{scripts['start']}\"")
            available_scripts = list(scripts.keys())
            lines.append(f"  available scripts: {available_scripts}")
            if pkg.get("main"):
                lines.append(f"  main entrypoint: \"{pkg['main']}\"")

            # Detect port from start script or known patterns
            import re as _re
            start_cmd = scripts.get("start", "")
            port_match = _re.search(r'(?:PORT|port)[=:\s]+(\d{4,5})', pkg_content)
            if port_match:
                lines.append(f"  detected port: {port_match.group(1)}")
        except Exception:
            pass

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
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Add it to your .env file."
        )

    # Increase temperature on retries to avoid repeating the same output
    temperature = 0.3 if previous_error else 0.2

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": _build_messages(scan_result, previous_error, previous_dockerfile),
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=90.0) as client:
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
