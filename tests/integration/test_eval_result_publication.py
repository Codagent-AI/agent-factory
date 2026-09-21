"""Every finished repetition's results reach the eval repository, reviewed or not."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agent_factory.config import SharedConfig
from agent_factory.github import GitHubApiError
from agent_factory.store import Claim, ClaimDraft, ClaimStore, Run
from agent_factory.work_kinds.eval.publication import publish_eval_results

CONFIG = Path(__file__).resolve().parents[2] / "config" / "codagent.toml"
RESULTS = "evals/agent-runner/and-scene/results"
CURATED = (
    "result.json",
    "report.html",
    "ambiguity-ledger.json",
    "implementation.diff",
    "artifact-manifest.json",
)


@dataclass
class Commit:
    repository: str
    branch: str
    files: dict[str, bytes]
    message: str


@dataclass
class RecordingClient:
    commits: list[Commit] = field(default_factory=lambda: list[Commit]())
    failure: Exception | None = None

    def commit_files(
        self, repository: str, branch: str, files: Mapping[str, bytes], message: str
    ) -> str | None:
        if self.failure is not None:
            raise self.failure
        self.commits.append(Commit(repository, branch, dict(files), message))
        return f"commit-{len(self.commits)}"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[ClaimStore]:
    opened = ClaimStore(tmp_path / "state.sqlite3")
    yield opened
    opened.close()


def _shared(results_repository: str | None = "Codagent-AI/agent-evals") -> SharedConfig:
    text = CONFIG.read_text(encoding="utf-8")
    text = text.replace('results_repository = "Codagent-AI/agent-evals"\n', "")
    if results_repository is not None:
        text = text.replace(
            'suite = "and-scene"\n',
            f'suite = "and-scene"\nresults_repository = "{results_repository}"\n',
            1,
        )
    return SharedConfig.from_toml(text)


def _finished(
    store: ClaimStore,
    tmp_path: Path,
    unit: str = "rep-1",
    *,
    status: str = "pending-human-review",
    consumed: bool = True,
) -> tuple[Claim, Run, Path]:
    claims = store.all_claims()
    claim = (
        claims[0]
        if claims
        else store.create_claim(
            ClaimDraft("Codagent-AI/agent-evals", 7, "I7", "P7", "eval", "fp", {"settings": {}})
        )
    )
    artifact = tmp_path / f"{claim.id}-{unit}"
    artifact.mkdir()
    for name in CURATED:
        (artifact / name).write_text(f"{name} body", encoding="utf-8")
    (artifact / "result.json").write_text(
        json.dumps({"evaluation_status": status, "run_id": f"{claim.id}-{unit}"}),
        encoding="utf-8",
    )
    (artifact / "logs").mkdir()
    (artifact / "logs" / "agent-runner.log").write_text("private", encoding="utf-8")
    run = store.reserve_run(claim.id, unit, reason="initial", evidence_path=str(artifact))
    store.mark_running(run.id, {})
    store.finish_run(run.id, execution_status="completed", result={})
    if consumed:
        store.set_setting("consumed-results", run.id, {"complete": True})
    return claim, run, artifact


def _finalize_review(artifact: Path) -> None:
    result = json.loads((artifact / "result.json").read_text(encoding="utf-8"))
    (artifact / "human-review.json").write_text(json.dumps({"complete": True}), encoding="utf-8")
    (artifact / "result.json").write_text(
        json.dumps({**result, "evaluation_status": "complete"}), encoding="utf-8"
    )


def _events(store: ClaimStore, claim: Claim) -> list[str]:
    reloaded = store.get_claim(claim.id)
    assert reloaded is not None
    events = reloaded.reporting.get("events", {})
    assert isinstance(events, dict)
    return [str(value["body"]) for value in events.values()]  # pyright: ignore[reportUnknownVariableType,reportUnknownArgumentType]


def test_unreviewed_repetition_is_committed_to_the_results_branch(
    store: ClaimStore, tmp_path: Path
) -> None:
    claim, _, _ = _finished(store, tmp_path)
    client = RecordingClient()

    publish_eval_results(store, client, _shared())

    assert len(client.commits) == 1
    commit = client.commits[0]
    run_id = f"{claim.id}-rep-1"
    assert (commit.repository, commit.branch) == ("Codagent-AI/agent-evals", "main")
    assert commit.message == f"chore: record and-scene eval {run_id}"
    assert set(commit.files) == {f"{RESULTS}/{run_id}/{name}" for name in CURATED}
    assert commit.files[f"{RESULTS}/{run_id}/report.html"] == b"report.html body"
    (event,) = _events(store, claim)
    assert "rep-1" in event and "not human-reviewed" in event
    assert f"https://github.com/Codagent-AI/agent-evals/tree/commit-1/{RESULTS}/{run_id}" in event


def test_an_unchanged_repetition_is_not_committed_again(store: ClaimStore, tmp_path: Path) -> None:
    _finished(store, tmp_path)
    client = RecordingClient()

    publish_eval_results(store, client, _shared())
    publish_eval_results(store, client, _shared())

    assert len(client.commits) == 1


def test_a_later_human_review_is_added_to_the_same_results(
    store: ClaimStore, tmp_path: Path
) -> None:
    claim, _, artifact = _finished(store, tmp_path)
    client = RecordingClient()
    publish_eval_results(store, client, _shared())

    _finalize_review(artifact)
    publish_eval_results(store, client, _shared())

    assert len(client.commits) == 2
    run_id = f"{claim.id}-rep-1"
    assert f"{RESULTS}/{run_id}/human-review.json" in client.commits[1].files
    assert any("human review" in event and "commit-2" in event for event in _events(store, claim))


def test_a_conclusive_product_failure_is_captured_too(store: ClaimStore, tmp_path: Path) -> None:
    _finished(store, tmp_path, status="complete")
    client = RecordingClient()

    publish_eval_results(store, client, _shared())

    assert len(client.commits) == 1


@pytest.mark.parametrize(
    ("status", "consumed"),
    [("evaluation-harness-failed", True), ("pending-human-review", False)],
)
def test_harness_failures_and_unconsumed_attempts_are_not_captured(
    store: ClaimStore, tmp_path: Path, status: str, consumed: bool
) -> None:
    _finished(store, tmp_path, status=status, consumed=consumed)
    client = RecordingClient()

    publish_eval_results(store, client, _shared())

    assert client.commits == []


def test_repetitions_missing_a_curated_file_wait_rather_than_commit_a_partial_record(
    store: ClaimStore, tmp_path: Path
) -> None:
    _, _, artifact = _finished(store, tmp_path)
    (artifact / "implementation.diff").unlink()
    client = RecordingClient()

    publish_eval_results(store, client, _shared())

    assert client.commits == []


def test_nothing_is_committed_without_a_configured_results_repository(
    store: ClaimStore, tmp_path: Path
) -> None:
    _finished(store, tmp_path)
    client = RecordingClient()

    publish_eval_results(store, client, _shared(results_repository=None))

    assert client.commits == []


def test_a_failed_commit_is_reported_once_and_retried_on_a_later_tick(
    store: ClaimStore, tmp_path: Path
) -> None:
    claim, _, _ = _finished(store, tmp_path)
    client = RecordingClient(failure=GitHubApiError("gh api request failed"))

    publish_eval_results(store, client, _shared())
    publish_eval_results(store, client, _shared())
    failures = [event for event in _events(store, claim) if "could not be saved" in event]

    assert len(failures) == 1
    assert "contents: write" in failures[0]
    client.failure = None
    publish_eval_results(store, client, _shared())
    assert len(client.commits) == 1


@pytest.mark.parametrize("review", ['{"complete": false, "answers": [1]}', '{"complete": tr'])
def test_an_unfinished_or_half_written_review_is_left_out(
    store: ClaimStore, tmp_path: Path, review: str
) -> None:
    claim, _, artifact = _finished(store, tmp_path)
    (artifact / "human-review.json").write_text(review, encoding="utf-8")
    client = RecordingClient()

    publish_eval_results(store, client, _shared())

    (commit,) = client.commits
    assert not any(path.endswith("human-review.json") for path in commit.files)
    assert all("not human-reviewed" in event for event in _events(store, claim))


def test_a_finished_review_waits_for_the_finalized_result(
    store: ClaimStore, tmp_path: Path
) -> None:
    _, _, artifact = _finished(store, tmp_path)
    client = RecordingClient()
    publish_eval_results(store, client, _shared())

    # The review file is final but result.json has not been rewritten yet.
    (artifact / "human-review.json").write_text(json.dumps({"complete": True}), encoding="utf-8")
    publish_eval_results(store, client, _shared())

    assert len(client.commits) == 1


def test_an_unchanged_run_directory_is_not_read_again(
    store: ClaimStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _finished(store, tmp_path)
    client = RecordingClient()
    publish_eval_results(store, client, _shared())
    reads: list[Path] = []
    original = Path.read_bytes

    def counting(path: Path) -> bytes:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", counting)
    publish_eval_results(store, client, _shared())

    assert reads == []


def _mark_done(store: ClaimStore, claim: Claim) -> None:
    current = store.get_claim(claim.id)
    assert current is not None
    store.set_cleanup(claim.id, {**current.cleanup, "done_observed_at": "2026-09-21T00:00:00"})


def test_a_done_claim_stops_being_scanned_once_its_results_are_saved(
    store: ClaimStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim, _, _ = _finished(store, tmp_path)
    _mark_done(store, claim)
    client = RecordingClient()
    publish_eval_results(store, client, _shared())
    scanned: list[str] = []
    original = ClaimStore.runs_for_claim

    def counting(self: ClaimStore, claim_id: str) -> list[Run]:
        scanned.append(claim_id)
        return original(self, claim_id)

    monkeypatch.setattr(ClaimStore, "runs_for_claim", counting)
    publish_eval_results(store, client, _shared())

    assert len(client.commits) == 1
    assert scanned == []


def test_a_done_claim_keeps_retrying_until_its_results_are_saved(
    store: ClaimStore, tmp_path: Path
) -> None:
    claim, _, _ = _finished(store, tmp_path)
    _mark_done(store, claim)
    client = RecordingClient(failure=GitHubApiError("gh api request failed"))
    publish_eval_results(store, client, _shared())

    client.failure = None
    publish_eval_results(store, client, _shared())

    assert len(client.commits) == 1
