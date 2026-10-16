"""Install executable stand-ins as links to one checked copy per worker.

macOS checks each new executable when it first runs: about 0.1 s on a developer's
Mac, and work for system services on the CI runner. The journey harnesses write the
same stand-ins for every test, so `install` writes each distinct content once per
worker and hard-links it into place; a link is the same file, so only its first run
is checked. The shared copy is read-only: rewriting an installed stand-in in place
fails instead of changing other tests' copies. Install the new content instead.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

_directory: Path | None = None


def install(path: Path, content: str) -> None:
    """Make ``path`` an executable with ``content``, replacing whatever is there."""
    global _directory
    if _directory is None:
        _directory = Path(tempfile.mkdtemp(prefix="stand-ins-"))
        atexit.register(shutil.rmtree, _directory, True)
    shared = _directory / hashlib.sha256(content.encode()).hexdigest()
    if not shared.exists():
        staged = shared.with_suffix(".tmp")
        staged.write_text(content)
        staged.chmod(0o555)
        staged.replace(shared)
    path.unlink(missing_ok=True)
    try:
        os.link(shared, path)
    except OSError:
        # Another file system: a plain copy still works, only without the saving.
        path.write_text(content)
        path.chmod(0o755)
