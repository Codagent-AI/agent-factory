"""Build a test's fixture Git repositories once per worker, then copy them.

A journey harness creates about a dozen repositories with some forty git commands
before its first tick. The repositories are the same for every test, so each
worker builds them once into a private directory and copies the tree into each
test's directory. The copies are independent; remotes and other paths that name
the build directory are rewritten to name the test's directory.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

_BUILT: dict[str, tuple[Path, object]] = {}
# Git files that record another repository's absolute path.
_PATH_FILES = ("config", "FETCH_HEAD")


def copy_built[T](key: str, build: Callable[[Path], T], destination: Path) -> T:
    """Copy the tree that ``build`` made under ``key`` into ``destination``; return its value.

    ``build`` receives an empty directory and must create everything inside it. Its
    return value is kept for later copies, so it must not refer to the directory.
    """
    if key not in _BUILT:
        root = Path(tempfile.mkdtemp(prefix="git-trees-")).resolve()
        atexit.register(shutil.rmtree, root, True)
        value = build(root)
        _BUILT[key] = (root, value)
    root, value = _BUILT[key]
    shutil.copytree(root, destination, symlinks=True, dirs_exist_ok=True)
    old, new = str(root), str(destination)
    for name in _PATH_FILES:
        for path in destination.rglob(name):
            if path.is_file():
                text = path.read_text()
                if old in text:
                    path.write_text(text.replace(old, new))
    return cast(T, value)
