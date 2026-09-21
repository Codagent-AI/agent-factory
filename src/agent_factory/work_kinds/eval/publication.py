"""Commit each finished repetition's curated results to the eval repository.

Human review is optional. A repetition's automated results are captured as soon
as its attempt is consumed; when a review later writes ``human-review.json`` into
the same run directory, the changed snapshot is committed again on a later tick.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from agent_factory.config import SharedConfig
from agent_factory.github import GitHubApiError
from agent_factory.store import ClaimStore, Run

# The suite's own curated publication set; logs, sessions, and credentials never leave.
CURATED_FILES = (
    "result.json",
    "report.html",
    "ambiguity-ledger.json",
    "implementation.diff",
    "artifact-manifest.json",
)
REVIEW_FILE = "human-review.json"
# A conclusive product failure is captured too; harness failures are diagnostics only.
_CAPTURED_STATUSES = frozenset({"pending-human-review", "complete"})
_RESULTS_DIRECTORIES = {"and-scene": "evals/agent-runner/and-scene/results"}
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ResultsClient(Protocol):
    def commit_files(
        self, repository: str, branch: str, files: Mapping[str, bytes], message: str
    ) -> str | None: ...


def publish_eval_results(store: ClaimStore, client: ResultsClient, shared: SharedConfig) -> None:
    """Commit every captured repetition whose snapshot changed since it was last committed."""
    repository = shared.eval.results_repository
    directory = _RESULTS_DIRECTORIES.get(shared.eval.suite)
    if repository is None or directory is None:
        return
    branch = shared.eval.results_branch
    for claim in store.all_claims():
        if claim.kind != "eval" or claim.lifecycle in {"cancelled", "superseded"}:
            continue
        # A Done item is scanned only until everything it produced is saved, so
        # history does not grow the per-tick cost.
        done = claim.cleanup.get("done_observed_at") is not None
        if done and claim.cleanup.get("results_final") is True:
            continue
        pending = False
        for run in store.runs_for_claim(claim.id):
            if not store.get_setting("consumed-results", run.id):
                pending = True
                continue
            recorded = store.get_setting("eval-publication", run.id) or {}
            # Stat before reading: an untouched run directory costs no reads or hashing.
            signature = _signature(Path(run.evidence_path))
            if recorded.get("signature") == signature:
                continue
            snapshot = _snapshot(run)
            if snapshot is None:
                continue
            run_id, files = snapshot
            digest = hashlib.sha256(
                b"".join(name.encode() + b"\0" + files[name] for name in sorted(files))
            ).hexdigest()
            if recorded.get("digest") == digest:
                store.set_setting("eval-publication", run.id, {**recorded, "signature": signature})
                continue
            reviewed = REVIEW_FILE in files
            try:
                commit = client.commit_files(
                    repository,
                    branch,
                    {f"{directory}/{run_id}/{name}": content for name, content in files.items()},
                    f"chore: record and-scene eval {run_id}",
                )
            except GitHubApiError as error:
                store.record_event(
                    claim.id,
                    f"{run.unit_key}:results-failed:{digest[:12]}",
                    f"{run.unit_key} results could not be saved to {repository} ({error}); "
                    "the factory retries on each tick. Confirm the GitHub App has "
                    f"`contents: write` on {repository} and that `{branch}` accepts its pushes.",
                )
                pending = True
                continue
            store.set_setting(
                "eval-publication",
                run.id,
                {"digest": digest, "signature": signature, "commit": commit, "run_id": run_id},
            )
            location = (
                f"https://github.com/{repository}/tree/{commit or branch}/{directory}/{run_id}"
            )
            state = "with its human review" if reviewed else "not human-reviewed yet"
            prefix = "updated" if recorded.get("digest") else "saved"
            store.record_event(
                claim.id,
                f"{run.unit_key}:results:{digest[:12]}",
                f"{run.unit_key} results {prefix} to {repository} ({state}): {location}",
            )
        if done and not pending:
            store.set_cleanup(claim.id, {**claim.cleanup, "results_final": True})


def _signature(artifact: Path) -> list[list[object]]:
    entries: list[list[object]] = []
    for name in (*CURATED_FILES, REVIEW_FILE):
        try:
            observed = (artifact / name).stat()
        except OSError:
            entries.append([name, None, None])
            continue
        entries.append([name, observed.st_size, observed.st_mtime_ns])
    return entries


def _snapshot(run: Run) -> tuple[str, dict[str, bytes]] | None:
    """The run's curated files, or None until every one of them is present."""
    artifact = Path(run.evidence_path)
    try:
        result = json.loads((artifact / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(result, Mapping):
        return None
    values = cast(Mapping[str, object], result)
    run_id = values.get("run_id")
    if values.get("evaluation_status") not in _CAPTURED_STATUSES:
        return None
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        return None
    files: dict[str, bytes] = {}
    for name in CURATED_FILES:
        try:
            files[name] = (artifact / name).read_bytes()
        except OSError:
            return None
    # The review file is saved after every answer; only a finalized review, as the
    # suite itself defines one, is part of the record.
    try:
        review_bytes = (artifact / REVIEW_FILE).read_bytes()
        review = json.loads(review_bytes)
    except (OSError, ValueError):
        return run_id, files
    if (
        not isinstance(review, Mapping)
        or cast(Mapping[str, object], review).get("complete") is not True
    ):
        return run_id, files
    if values.get("evaluation_status") != "complete":
        return None  # the review finished but result.json is not rewritten yet
    files[REVIEW_FILE] = review_bytes
    return run_id, files
