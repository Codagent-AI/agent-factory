"""Releases a fix claim's clones, run images, and credential copies.

A settled claim normally releases after Review then Done. The terminal sweep also releases
settled claims observed Done without Review and cancelled claims once they are quiescent.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from agent_factory.store import Claim, ClaimStore
from agent_factory.work_kinds.images import remove_images, run_image_tags


class PullRequestCleanup:
    """Reconciles Review-then-Done cleanup and releases recorded fix resources."""

    def __init__(
        self,
        store: ClaimStore,
        *,
        private_root: Path | None = None,
        claim_directory: Callable[[str], Path] | None = None,
    ) -> None:
        self._store = store
        self._private_root = private_root
        self._claim_directory = claim_directory

    def reconcile(self, claim_id: str, *, board_status: str) -> bool:
        claim = self._store.get_claim(claim_id)
        if claim is None:
            return False
        cleanup = dict(claim.cleanup)
        if cleanup.get("complete") is True:
            return True
        if claim.lifecycle == "settled":
            if board_status == "Review":
                if cleanup.get("review_observed") is not True:
                    cleanup["review_observed"] = True
                    self._store.set_cleanup(claim_id, cleanup)
                return False
            if board_status != "Done" or cleanup.get("review_observed") is not True:
                return False
        elif claim.lifecycle != "cancelled":
            return False
        from agent_factory.terminal import idle_and_reported

        # Cancellation stops execution asynchronously; the clones and the token copy are
        # released once no attempt can still be using them, whatever the card status.
        if not idle_and_reported(self._store, claim):
            return False
        return self._release(claim, cleanup)

    def _release(self, claim: Claim, cleanup: dict[str, object]) -> bool:
        claim_id = claim.id
        errors: dict[str, str] = {}
        if self._claim_directory is not None:
            directory = self._claim_directory(claim_id)
            try:
                _remove_tree(str(directory))
            except FileNotFoundError:
                pass
            except OSError as error:
                errors["clones"] = str(error)
        clones = claim.preparation.get("clones")
        if isinstance(clones, Mapping):
            for name, path in cast(Mapping[str, object], clones).items():
                if not isinstance(path, str):
                    continue
                try:
                    _remove_tree(path)
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

    def release(self, claim_id: str) -> bool:
        claim = self._store.get_claim(claim_id)
        if claim is None:
            return False
        if claim.cleanup.get("complete") is True:
            return True
        return self._release(claim, dict(claim.cleanup))

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


def _remove_tree(path: str) -> None:
    """Remove a clone even where tools left it read-only, as Go's module cache does."""

    def restore_write(function: Callable[..., object], target: str, error: BaseException) -> None:
        if not isinstance(error, PermissionError):
            raise error
        parent = os.path.dirname(target)
        os.chmod(parent, os.stat(parent).st_mode | stat.S_IRWXU)
        if os.path.isdir(target) and not os.path.islink(target):
            os.chmod(target, os.stat(target).st_mode | stat.S_IRWXU)
        function(target)

    shutil.rmtree(path, onexc=restore_write)
