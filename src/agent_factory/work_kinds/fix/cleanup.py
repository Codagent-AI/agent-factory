"""Releases a settled fix claim's per-attempt clones and run image tags after Review."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from typing import cast

from agent_factory.store import ClaimStore


class FixCleanup:
    """Mirrors WorktreeCleanup's Review-then-Done gate for fix clones and image tags."""

    def __init__(self, store: ClaimStore) -> None:
        self._store = store

    def reconcile(self, claim_id: str, *, board_status: str) -> bool:
        claim = self._store.get_claim(claim_id)
        if claim is None or claim.lifecycle != "settled":
            return False
        cleanup = dict(claim.cleanup)
        if board_status == "Review":
            if cleanup.get("review_observed") is not True:
                cleanup["review_observed"] = True
                self._store.set_cleanup(claim_id, cleanup)
            return False
        if board_status != "Done" or cleanup.get("review_observed") is not True:
            return False
        if cleanup.get("complete") is True:
            return True
        errors: dict[str, str] = {}
        clones = claim.preparation.get("clones")
        if isinstance(clones, Mapping):
            for name, path in cast(Mapping[str, object], clones).items():
                if not isinstance(path, str):
                    continue
                try:
                    shutil.rmtree(path)
                except FileNotFoundError:
                    pass
                except OSError as error:
                    errors[f"clone:{name}"] = str(error)
        image = claim.preparation.get("image")
        if isinstance(image, str):
            try:
                result = subprocess.run(
                    ["docker", "rmi", image], capture_output=True, text=True, check=False
                )
            except OSError as error:
                errors["image"] = f"cannot run docker: {error}"
            else:
                if result.returncode != 0 and "No such image" not in result.stderr:
                    errors["image"] = (
                        result.stderr.strip() or f"docker rmi exited {result.returncode}"
                    )
        cleanup["complete"] = not errors
        cleanup["last_error"] = errors or None
        self._store.set_cleanup(claim_id, cleanup)
        return not errors
