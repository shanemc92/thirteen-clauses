#!/usr/bin/env python3
"""Regenerate python/manifest.json.

Walks python/ and lists every file the browser should copy into the sandbox.
Run it after adding, renaming or deleting anything under python/.

    python3 build.py
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent / "python"
MANIFEST = ROOT / "manifest.json"

SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", ".pytest_cache", ".mypy_cache", ".idea"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".13save"}
SKIP_NAMES = {"manifest.json", ".DS_Store"}

# Contents of python/saves/ never belong in the manifest. The directory is
# created on demand by the game and by the worker, and anything sitting in it
# locally is a save — either the player's own, or the skip-to-any-floor
# fixtures tools/make_saves.py writes for the test suite. Sweeping those into
# the manifest ships them to every visitor as spoilers and a cheat, and the
# test suite regenerates them, so a manifest built after a test run silently
# picked them up.
SKIP_TREES = {"saves"}


def main():
    if not ROOT.is_dir():
        sys.exit("no python/ directory next to build.py")

    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.relative_to(ROOT).parts[0] in SKIP_TREES:
            continue
        if path.suffix in SKIP_SUFFIXES or path.name in SKIP_NAMES:
            continue
        if any(part.startswith(".") for part in path.relative_to(ROOT).parts):
            continue  # many static hosts refuse to serve dotfiles
        files.append(path.relative_to(ROOT).as_posix())

    if not files:
        sys.exit("python/ is empty")

    MANIFEST.write_text(json.dumps(files, indent=2) + "\n")

    total = sum((ROOT / f).stat().st_size for f in files)
    print("wrote %s" % MANIFEST)
    print("%d files, %.1f KB" % (len(files), total / 1024))

    for f in files:
        print("  " + f)


if __name__ == "__main__":
    main()
