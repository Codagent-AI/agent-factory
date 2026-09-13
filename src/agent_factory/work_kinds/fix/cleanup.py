"""Releases a settled fix claim's clones, run images, and credential copies after Review."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from agent_factory.store import ClaimStore
from agent_factory.work_kinds.images import remove_images, run_image_tags


class FixCleanup:
    """Mirrors WorktreeCleanup's Review-then-Done gate for fix clones and image tags."""

    def __init__(self, store: ClaimStore, *, private_root: Path | None = None) -> None:
        self._store = store
        self._private_root = private_root

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
        errors.update(remove_images(run_image_tags(self._store, claim_id)))
        errors.update(self._remove_credential_copies(claim_id))
        cleanup["complete"] = not errors
        cleanup["last_error"] = errors or None
        self._store.set_cleanup(claim_id, cleanup)
        return not errors

    def _remove_credential_copies(self, claim_id: str) -> dict[str, str]:
        """Delete every attempt's private `GH_TOKEN` copy; the token must not outlive Done."""
        errors: dict[str, str] = {}
        for run in self._store.runs_for_claim(claim_id):
            paths = [
                Path(item)
                for item in cast(list[object], run.plan.get("credential_files") or [])
                if isinstance(item, str)
            ]
            if self._private_root is not None:
                paths.append(self._private_root / run.id)
            for path in paths:
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink(missing_ok=True)
                except OSError as error:
                    errors[f"credential:{run.id}"] = str(error)
        return errors
