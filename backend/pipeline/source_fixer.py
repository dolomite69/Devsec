from __future__ import annotations

import os
import re

# Source files we scan for relative imports.
_SCAN_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

# Directories never worth scanning.
_SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", ".venv", "venv", "__pycache__"}

# Matches the quoted path in:  import x from '...'  |  import '...'  |
# require('...')  |  import('...')  — capturing the quote + path.
_IMPORT_RE = re.compile(
    r"""(?P<pre>(?:\bfrom\s+|\bimport\s+|\brequire\s*\(\s*|\bimport\s*\(\s*))(?P<q>['"])(?P<path>\.{1,2}/[^'"]+)(?P=q)"""
)


def _correct_relative_path(import_path: str, importing_dir: str) -> tuple[str, bool]:
    """Return (corrected_path, changed) fixing only case mismatches on disk.

    Walks each path segment against the real filesystem (case-sensitive). When a
    segment only differs by case, it is rewritten to the on-disk name. The final
    segment may omit a source extension (e.g. ./Foo -> Foo.jsx); in that case the
    original extension-less style is preserved. If any segment cannot be matched
    at all, the original path is returned unchanged (don't guess).
    """
    parts = import_path.split("/")
    current = importing_dir
    corrected: list[str] = []
    changed = False

    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1

        if part in ("", ".", ".."):
            corrected.append(part)
            if part == "..":
                current = os.path.dirname(current)
            elif part == "":
                pass
            # "." keeps current
            continue

        if not os.path.isdir(current):
            return import_path, False

        try:
            entries = os.listdir(current)
        except OSError:
            return import_path, False

        # Exact match — nothing to fix for this segment.
        if part in entries:
            corrected.append(part)
            current = os.path.join(current, part)
            continue

        # Case-insensitive match on the full segment name (dir or file w/ ext).
        match = next((e for e in entries if e.lower() == part.lower()), None)
        if match is not None:
            corrected.append(match)
            current = os.path.join(current, match)
            changed = True
            continue

        # Final segment may omit a source extension: ./Foo -> Foo.jsx
        if is_last:
            stem_match = next(
                (
                    e
                    for e in entries
                    if os.path.splitext(e)[0].lower() == part.lower()
                    and os.path.splitext(e)[1] in _SCAN_EXTS
                ),
                None,
            )
            if stem_match is not None:
                corrected.append(os.path.splitext(stem_match)[0])
                changed = True
                continue

        # No match — leave the whole import untouched.
        return import_path, False

    return "/".join(corrected), changed


def fix_import_casing(repo_path: str) -> list[str]:
    """Rewrite case-mismatched relative imports across the cloned repo.

    Case-insensitive host filesystems (Windows/macOS) let mismatched import
    casing build locally, but it breaks `vite build` / bundlers inside a
    case-sensitive Linux container. This corrects those imports in-place on the
    clone. Returns a list of human-readable change descriptions.
    """
    fixes: list[str] = []

    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for filename in filenames:
            if not filename.endswith(_SCAN_EXTS):
                continue

            abs_path = os.path.join(dirpath, filename)
            try:
                with open(abs_path, "r", encoding="utf-8") as fh:
                    source = fh.read()
            except (OSError, UnicodeDecodeError):
                continue

            file_changed = False

            def _replace(match: re.Match) -> str:
                nonlocal file_changed
                original = match.group("path")
                corrected, changed = _correct_relative_path(original, dirpath)
                if changed and corrected != original:
                    file_changed = True
                    rel = os.path.relpath(abs_path, repo_path).replace(os.sep, "/")
                    fixes.append(f"{rel}: '{original}' -> '{corrected}'")
                    return f"{match.group('pre')}{match.group('q')}{corrected}{match.group('q')}"
                return match.group(0)

            new_source = _IMPORT_RE.sub(_replace, source)

            if file_changed:
                try:
                    with open(abs_path, "w", encoding="utf-8", newline="") as fh:
                        fh.write(new_source)
                except OSError:
                    pass

    return fixes
