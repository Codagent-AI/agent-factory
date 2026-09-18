"""Post-merge sync: fast-forward the operator's working clone and close the issue."""

from __future__ import annotations

import hashlib
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Protocol, cast

from agent_factory.config import LocalConfig
from agent_factory.github import IssueComment
from agent_factory.store import Claim, ClaimStore

AGENT_RUNNER_REPOSITORY = "Codagent-AI/agent-runner"


class SyncClient(Protocol):
    def get_pull_request(self, repository: str, number: int) -> object: ...

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]: ...

    def create_comment(self, repository: str, number: int, body: str) -> str | None: ...

    def set_attention_label(self, repository: str, number: int, needed: bool) -> None: ...

    def close_issue(self, repository: str, number: int) -> None: ...


def sync_claim(
    store: ClaimStore,
    client: SyncClient,
    local: LocalConfig,
    claim: Claim,
    *,
    bot_login: str,
    card_done: bool,
) -> None:
    """Run one post-merge sync attempt for a settled fix claim with a recorded PR."""
    if claim.kind != "fix" or claim.lifecycle != "settled":
        return
    sync = claim.reporting.get("sync")
    if isinstance(sync, Mapping) and cast(Mapping[str, object], sync).get("completed"):
        return
    pr = _find_pr(store, claim)
    if pr is None:
        return
    number, _url = pr
    state = client.get_pull_request(claim.repository, number)
    merged_at = getattr(state, "merged_at", None)
    if merged_at is None:
        return
    clone = local.repositories.working_clones.get(claim.repository)
    reason = (
        _merge_working_clone(
            clone, rebuild=claim.repository.casefold() == AGENT_RUNNER_REPOSITORY.casefold()
        )
        if clone is not None
        else "the operator's working clone is not configured"
    )
    if reason is not None:
        _report_blocked(store, client, claim, reason, bot_login=bot_login, card_done=card_done)
        return
    _report_success(store, client, claim, bot_login=bot_login)


def _find_pr(store: ClaimStore, claim: Claim) -> tuple[int, str] | None:
    candidates: list[object] = [claim.outcome.get("pr")]
    candidates.extend(
        run.result.get("pr")
        for run in reversed(store.runs_for_claim(claim.id))
        if run.unit_key == "fix"
    )
    for pr in candidates:
        if isinstance(pr, Mapping):
            pr_mapping = cast(Mapping[str, object], pr)
            number = pr_mapping.get("number")
            url = pr_mapping.get("url")
            if isinstance(number, int) and isinstance(url, str):
                return number, url
    return None


def _run(clone: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(clone), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _merge_working_clone(clone: Path, *, rebuild: bool = False) -> str | None:
    """Run the exact fetch/merge sequence the design mandates; return a block reason, if any."""
    if not clone.is_dir():
        return "the operator's working clone is not configured"
    try:
        reason = _merge_sequence(clone)
    except (subprocess.TimeoutExpired, OSError) as error:
        return f"git command did not complete: {error}"
    if reason is not None or not rebuild:
        return reason
    return _build_working_clone(clone)


def _build_working_clone(clone: Path, *, timeout: float = 300) -> str | None:
    """Build Agent Runner, terminating the complete build process group on timeout."""
    try:
        build = subprocess.Popen(
            ["make", "build"],
            cwd=clone,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        return f"cannot rebuild agent-runner: {error}"
    assert build.stdout is not None and build.stderr is not None
    stdout = _BoundedOutput()
    stderr = _BoundedOutput()
    output_selector = selectors.DefaultSelector()
    for stream, output in ((build.stdout, stdout), (build.stderr, stderr)):
        os.set_blocking(stream.fileno(), False)
        output_selector.register(stream.fileno(), selectors.EVENT_READ, output)
    deadline = time.monotonic() + timeout
    timed_out = False
    while build.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        _drain_ready_output(output_selector, timeout=min(0.1, remaining))
    with suppress(ProcessLookupError):
        os.killpg(build.pid, signal.SIGKILL)
    if timed_out:
        build.wait()
    drain_deadline = min(deadline, time.monotonic() + 1)
    while output_selector.get_map() and time.monotonic() < drain_deadline:
        _drain_ready_output(output_selector, timeout=min(0.1, drain_deadline - time.monotonic()))
    output_selector.close()
    build.stdout.close()
    build.stderr.close()
    if timed_out:
        return f"cannot rebuild agent-runner: make build timed out after {timeout:g} seconds"
    if build.returncode != 0:
        detail = stderr.text() or stdout.text() or "make build failed"
        return f"cannot rebuild agent-runner: {detail}"
    return None


class _BoundedOutput:
    def __init__(self, *, limit: int = 4096) -> None:
        self._limit = limit
        self._data = bytearray()

    def append(self, chunk: bytes) -> None:
        self._data.extend(chunk)
        if len(self._data) > self._limit:
            del self._data[: -self._limit]

    def text(self) -> str:
        return self._data.decode(errors="replace").strip()


def _drain_ready_output(output_selector: selectors.BaseSelector, *, timeout: float) -> None:
    for key, _events in output_selector.select(timeout=max(0, timeout)):
        try:
            chunk = os.read(key.fd, 8192)
        except BlockingIOError:
            continue
        if chunk:
            cast(_BoundedOutput, key.data).append(chunk)
        else:
            output_selector.unregister(key.fd)


def _merge_sequence(clone: Path) -> str | None:
    status = _run(clone, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        return f"cannot inspect working clone: {status.stderr.strip() or 'git status failed'}"
    if status.stdout.strip():
        return "uncommitted changes"
    current = _run(clone, "branch", "--show-current")
    branch = current.stdout.strip()
    if current.returncode != 0 or not branch:
        return "detached HEAD"
    if branch == "main":
        fetch = _run(clone, "fetch", "origin")
        if fetch.returncode != 0:
            return f"cannot fetch origin: {fetch.stderr.strip() or 'git fetch failed'}"
        merge = _run(clone, "merge", "--ff-only", "origin/main")
        if merge.returncode != 0:
            return "local main diverged"
        return None
    fetch = _run(clone, "fetch", "origin", "main:main")
    if fetch.returncode != 0:
        return fetch.stderr.strip() or "cannot update local main"
    tree = _run(clone, "merge-tree", "--write-tree", "HEAD", "refs/heads/main")
    if tree.returncode == 1:
        return "conflicts"
    if tree.returncode != 0:
        return f"cannot check for conflicts: {tree.stderr.strip() or 'git merge-tree failed'}"
    merge = _run(clone, "merge", "--no-edit", "main")
    if merge.returncode != 0:
        _run(clone, "merge", "--abort")
        return merge.stderr.strip() or "merge failed"
    return None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _deliver_marker(
    client: SyncClient, claim: Claim, bot_login: str, marker: str, body: str
) -> str | None:
    existing = next(
        (
            comment
            for comment in client.list_comment_records(claim.repository, claim.issue_number)
            if comment.author == bot_login and marker in comment.body
        ),
        None,
    )
    if existing is not None:
        return existing.id
    return client.create_comment(claim.repository, claim.issue_number, f"{marker}\n{body}")


def _report_blocked(
    store: ClaimStore,
    client: SyncClient,
    claim: Claim,
    reason: str,
    *,
    bot_login: str,
    card_done: bool,
) -> None:
    marker = f"<!-- agent-factory:fix-sync:{claim.id}:blocked:{_digest(reason)} -->"
    comment_id = _deliver_marker(
        client, claim, bot_login, marker, f"Post-merge sync is blocked: {reason}."
    )
    if not card_done:
        client.set_attention_label(claim.repository, claim.issue_number, True)
    store.set_claim_sync(
        claim.id,
        {"attempted": True, "blocked_reason": reason, "comment_id": comment_id or "acknowledged"},
    )


def _report_success(store: ClaimStore, client: SyncClient, claim: Claim, *, bot_login: str) -> None:
    client.close_issue(claim.repository, claim.issue_number)
    marker = f"<!-- agent-factory:fix-sync:{claim.id}:completed -->"
    comment_id = _deliver_marker(
        client,
        claim,
        bot_login,
        marker,
        "Post-merge sync completed: the working clone is up to date and the issue is closed.",
    )
    client.set_attention_label(claim.repository, claim.issue_number, False)
    store.set_claim_sync(
        claim.id, {"attempted": True, "completed": True, "comment_id": comment_id or "acknowledged"}
    )
