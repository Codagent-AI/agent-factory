#!/usr/bin/env python3
"""Resolve the one dated OpenSpec archive directory for a change."""

import sys
from pathlib import Path

root = Path("openspec/changes/archive")
found = sorted(path for path in root.glob(f"*-{sys.argv[1]}") if path.is_dir())
if len(found) != 1:
    raise SystemExit(f"expected one archive for {sys.argv[1]}, found {len(found)}")
print(found[0])
