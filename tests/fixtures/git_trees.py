"""Build a test's fixture Git repositories once per worker, then copy them.

A journey harness creates about a dozen repositories with some forty git commands
before its first tick. The repositories are the same for every test, so each
worker builds them once into a private directory and copies the tree into each
test's directory. The copies are independent: every path that names the build
directory is rewritten to name the test's directory.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

_BUILT: dict[str, tuple[Path, object]] = {}


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
    # Remotes, alternates, worktree links, and reflogs record absolute paths. Object
    # files are compressed, so they cannot hold the path in plain text.
    old, new = str(root).encode(), str(destination).encode()
    for path in destination.rglob("*"):
        in_objects = "objects" in path.relative_to(destination).parts
        if (in_objects and path.name != "alternates") or not path.is_file():
            continue
        content = path.read_bytes()
        if old in content:
            path.write_bytes(content.replace(old, new))
    return cast(T, value)
