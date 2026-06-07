from __future__ import annotations

import asyncio
import os
import re
import shutil

import git

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", ".next",
    ".venv", "venv", "__pycache__",
}

_KEY_FILES = [
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "Cargo.toml",
    "CMakeLists.txt",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".dockerignore",
    ".env.example",
    "server.js",
    "index.js",
    "app.js",
    "main.py",
    "app.py",
    "manage.py",
    "main.go",
    "README.md",
]

_LANGUAGE_MARKERS: list[tuple[str, str]] = [
    ("package.json",    "Node.js"),
    ("requirements.txt","Python"),
    ("pyproject.toml",  "Python"),
    ("setup.py",        "Python"),
    ("go.mod",          "Go"),
    ("pom.xml",         "Java"),
    ("build.gradle",    "Java"),
    ("build.gradle.kts","Kotlin/Java"),
    ("Gemfile",         "Ruby"),
    ("Cargo.toml",      "Rust"),
    ("CMakeLists.txt",  "C/C++"),
    ("Makefile",        "C/C++"),
]

# Extension-based fallback when no marker file is found
_EXT_LANGUAGE: list[tuple[str, str]] = [
    (".c",    "C"),
    (".cpp",  "C++"),
    (".cc",   "C++"),
    (".h",    "C/C++"),
    (".hpp",  "C++"),
    (".cs",   "C#"),
    (".java", "Java"),
    (".go",   "Go"),
    (".rs",   "Rust"),
    (".rb",   "Ruby"),
    (".php",  "PHP"),
    (".ts",   "TypeScript"),
    (".tsx",  "TypeScript"),
    (".js",   "JavaScript"),
    (".jsx",  "JavaScript"),
    (".py",   "Python"),
    (".swift","Swift"),
    (".kt",   "Kotlin"),
]

_MAX_FILE_BYTES = 8 * 1024  # 8 KB


# ---------------------------------------------------------------------------
# Helpers (all synchronous — called via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _clone(repo_url: str, clone_path: str) -> None:
    if os.path.exists(clone_path):
        shutil.rmtree(clone_path)
    git.Repo.clone_from(repo_url, clone_path)


def _project_name(repo_url: str) -> str:
    """Derive a filesystem-safe project name from the repo URL.

    https://github.com/user/QuickBite11(.git) -> "QuickBite11"
    """
    name = repo_url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    # Replace anything that isn't a safe path char
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name).strip("-._")
    return name or "project"


def _build_file_tree(root: str, max_depth: int = 3) -> list[str]:
    tree: list[str] = []
    root_depth = root.rstrip(os.sep).count(os.sep)

    for dirpath, dirnames, filenames in os.walk(root):
        current_depth = dirpath.count(os.sep) - root_depth
        if current_depth >= max_depth:
            dirnames.clear()
            continue

        # Prune unwanted dirs in-place so os.walk skips them
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        rel_dir = os.path.relpath(dirpath, root)
        for filename in filenames:
            entry = os.path.join(rel_dir, filename) if rel_dir != "." else filename
            tree.append(entry.replace(os.sep, "/"))

    return sorted(tree)


def _detect_language(file_tree: list[str]) -> str:
    names = {p.rsplit("/", 1)[-1] for p in file_tree}
    for marker, language in _LANGUAGE_MARKERS:
        if marker in names:
            return language

    # Fallback: detect by most common file extension
    from collections import Counter
    ext_counts: Counter[str] = Counter()
    for path in file_tree:
        fname = path.rsplit("/", 1)[-1]
        if "." in fname:
            ext = "." + fname.rsplit(".", 1)[-1]
            for marker_ext, lang in _EXT_LANGUAGE:
                if ext == marker_ext:
                    ext_counts[lang] += 1
    if ext_counts:
        return ext_counts.most_common(1)[0][0]
    return "Unknown"


def _read_key_files(root: str, file_tree: list[str]) -> dict[str, str]:
    names_in_tree = {p.rsplit("/", 1)[-1]: p for p in file_tree}
    result: dict[str, str] = {}
    for key_file in _KEY_FILES:
        rel_path = names_in_tree.get(key_file)
        if rel_path is None:
            continue
        abs_path = os.path.join(root, rel_path.replace("/", os.sep))
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                result[key_file] = fh.read(_MAX_FILE_BYTES)
        except OSError:
            pass
    return result


# Manifest files that can appear in multiple sub-projects of a monorepo. We
# collect EVERY occurrence (keyed by relative path) so the LLM can see e.g.
# backend/package.json and frontend/package.json separately instead of just one.
_MANIFEST_NAMES = {
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "go.mod",
    "pom.xml",
}


def _read_all_manifests(root: str, file_tree: list[str]) -> dict[str, str]:
    manifests: dict[str, str] = {}
    for rel_path in file_tree:
        fname = rel_path.rsplit("/", 1)[-1]
        if fname in _MANIFEST_NAMES:
            abs_path = os.path.join(root, rel_path.replace("/", os.sep))
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    manifests[rel_path] = fh.read(_MAX_FILE_BYTES)
            except OSError:
                pass
    return manifests


def _has_dockerfile(file_tree: list[str]) -> bool:
    return any(p.rsplit("/", 1)[-1] == "Dockerfile" for p in file_tree)


def _inspect_sync(repo_url: str, clone_path: str) -> dict:
    _clone(repo_url, clone_path)
    file_tree = _build_file_tree(clone_path)
    detected_language = _detect_language(file_tree)
    key_files = _read_key_files(clone_path, file_tree)
    manifests = _read_all_manifests(clone_path, file_tree)
    has_existing_dockerfile = _has_dockerfile(file_tree)

    return {
        "repo_url": repo_url,
        "local_path": clone_path,
        "project_name": _project_name(repo_url),
        "detected_language": detected_language,
        "file_tree": file_tree,
        "key_files": key_files,
        "manifests": manifests,
        "has_existing_dockerfile": has_existing_dockerfile,
    }


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def inspect_repo(repo_url: str, work_dir: str, build_id: str) -> dict:
    if not repo_url.startswith("https://github.com/"):
        raise ValueError(f"Only GitHub URLs are supported, got: {repo_url!r}")

    # Folder is <projectname>-<uuid> so kept clones are human-identifiable.
    clone_path = os.path.join(work_dir, f"{_project_name(repo_url)}-{build_id}")
    return await asyncio.to_thread(_inspect_sync, repo_url, clone_path)
