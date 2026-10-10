"""In-memory board for real runtime/controller/handler lane integration tests."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from agent_factory import runtime, work_kinds
from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.controller import AttemptResult, Controller, ExecutionPlan
from agent_factory.github import (
    BranchInfo,
    GitHubClient,
    IssueComment,
    ProjectQueueItem,
    ReviewActivity,
)
from agent_factory.routing import SourceItem
from agent_factory.store import Claim, ClaimStore, Run
from agent_factory.suites.and_scene import ReadinessError
from agent_factory.work_kinds.base import Preparation, WorkKindHandler
from agent_factory.work_kinds.eval.handler import Resolution
from agent_factory.work_kinds.pull_request.handler import PullRequestHandler
from tests.integration.test_fix_gestures import _LOCAL_BASE, _SHARED_BASE


class Board:
    def __init__(self, shared: SharedConfig) -> None:
        self.shared = shared
        self.cards: list[ProjectQueueItem] = []
        self.comments: dict[int, list[IssueComment]] = {}
        self.review_feedback: dict[int, ReviewActivity] = {}
        self.labels: list[tuple[int, bool]] = []

    def validate_project(self, *args: object) -> None:
        pass

    def list_project_items(self, *args: object, **kwargs: object) -> list[ProjectQueueItem]:
        return sorted(self.cards, key=lambda c: runtime.github._priority_rank(c.priority))

    def get_permission(self, *args: object) -> str:
        return "write"

    def list_comment_records(self, repo: str, number: int) -> list[IssueComment]:
        return self.comments.get(number, [])

    def create_comment(self, repo: str, number: int, body: str) -> str:
        identifier = str(len(self.comments.setdefault(number, [])) + 1)
        self.comments[number].append(
            IssueComment(identifier, body, self.shared.bot_login, datetime.now(UTC).isoformat())
        )
        return identifier

    def set_attention_label(self, repo: str, number: int, needed: bool) -> None:
        self.labels.append((number, needed))
        for card in self.cards:
            if card.source.number == number:
                labels = (
                    card.source.labels | {"needs-input"}
                    if needed
                    else card.source.labels - {"needs-input"}
                )
                object.__setattr__(card, "source", replace(card.source, labels=frozenset(labels)))

    def set_single_select_field(self, project: str, item: str, field: str, value: str) -> None:
        next(c for c in self.cards if c.id == item).fields[field] = value

    def clear_field(self, project: str, item: str, field: str) -> None:
        next(c for c in self.cards if c.id == item).fields.pop(field, None)

    def set_text_field(self, *args: object) -> None:
        pass

    def get_pull_request(self, *args: object) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(state="open", merged_at=None)

    def list_review_activity(self, repo: str, number: int) -> ReviewActivity:
        return self.review_feedback.get(number, ReviewActivity((), (), ()))

    def get_branch(self, repo: str, branch: str) -> BranchInfo:
        return BranchInfo(branch, "f" * 40)

    def get_source_item(self, repo: str, number: int) -> SourceItem:
        return next(c.source for c in self.cards if c.source.number == number)

    def list_open_factory_pull_requests_for_issue(self, *args: object) -> list[object]:
        return []


class Site:
    def __init__(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, lanes: bool = True
    ) -> None:
        self.shared_path = tmp_path / "shared.toml"
        self.shared_path.write_text(
            _SHARED_BASE
            + """
[fields.priority]
id = "priority-field"
[fix]
[[fix.targets]]
repository = "example/work"
[feature]
[task]
"""
        )
        self.config = tmp_path / "local.toml"
        self.config.write_text(
            _LOCAL_BASE.replace(
                'shared_config = "/opt/agent-factory/config/codagent.toml"',
                f'shared_config = "{self.shared_path}"',
            )
            .replace('storage_root = "~/.agent-factory"', f'storage_root = "{tmp_path / "state"}"')
            .replace("stop_hour = 15", "stop_hour = 0")
            + '\n[fix]\nexecution = "host"\n'
        )
        self.local = LocalConfig.from_file(self.config)
        self.shared = SharedConfig.from_file(self.shared_path)
        self.store = ClaimStore(self.local.state_path)
        if lanes:
            self.store.enable_lanes()
        self.board = Board(self.shared)
        self.prepared: list[int] = []
        self.reconciled: list[int] = []
        self.launched: list[Run] = []
        self.planning_failure: set[int] = set()
        self.memory_probes = 0
        self.handlers = work_kinds.handlers(self.shared, self.local)
        for handler in self.handlers.values():
            if isinstance(handler, PullRequestHandler):
                monkeypatch.setattr(handler, "resolve_request", self.resolve_pr)
                monkeypatch.setattr(handler, "prepare_review", self.prepare_review)
                monkeypatch.setattr(handler, "reconcile", self.reconcile)
                monkeypatch.setattr(handler, "merge_sync", self.noop)
            else:
                monkeypatch.setattr(handler, "resolve_request", self.resolve_eval)
            monkeypatch.setattr(handler, "prepare", self.prepare)
            monkeypatch.setattr(handler, "cleanup", self.noop)
        monkeypatch.setattr(runtime.work_kinds, "handlers", self.registered)
        monkeypatch.setattr(runtime, "GitHubClient", self.client)
        monkeypatch.setattr(runtime, "InstallationTokenProvider", self.token_provider)
        monkeypatch.setattr(runtime, "_kind_failures", self.diagnostics)
        monkeypatch.setattr(runtime, "_reconcile_backends", self.noop)
        monkeypatch.setattr(runtime, "doctor", self.diagnostics)
        monkeypatch.setattr(runtime, "launch_supervisor", self.launch)
        for handler in self.handlers.values():
            monkeypatch.setattr(handler, "plan", self.plan)
        from agent_factory.backends.docker import DockerContainerBackend

        monkeypatch.setattr(DockerContainerBackend, "memory_readiness", self.memory)
        self.controller = Controller(self.store, cast(GitHubClient, self.board), self.handlers)

    def resolve_pr(self, target: object) -> tuple[str, str, str]:
        return "a" * 40, "b" * 40, "c" * 40

    def registered(self, *args: object) -> Mapping[str, WorkKindHandler]:
        return self.handlers

    def client(self, *args: object) -> Board:
        return self.board

    def token_provider(self, *args: object) -> Callable[[], str]:
        return lambda: "token"

    def noop(self, *args: object, **kwargs: object) -> None:
        pass

    def diagnostics(self, *args: object, **kwargs: object) -> list[object]:
        return []

    def resolve_eval(self, request: object) -> Resolution:
        return Resolution({"runner": "a" * 40, "skills": "b" * 40, "evals": "c" * 40}, {})

    def prepare(self, claim: Claim) -> Preparation:
        self.prepared.append(claim.issue_number)
        return Preparation()

    def prepare_review(self, claim: Claim, review: Mapping[str, object]) -> Preparation:
        return self.prepare(claim)

    def reconcile(self, claim: Claim) -> None:
        self.reconciled.append(claim.issue_number)

    def memory(self, *args: object) -> runtime.Diagnostic:
        self.memory_probes += 1
        return runtime.Diagnostic("memory", True, "ok", "")

    def plan(self, claim: Claim, run: Run, preparation: Preparation) -> ExecutionPlan:
        if claim.issue_number in self.planning_failure:
            raise ReadinessError("planning failed")
        return ExecutionPlan(("stub",), "/tmp", {}, (), (), {}, False)

    def launch(self, state: Path, run_id: str, *args: object, **kwargs: object) -> None:
        with closing(ClaimStore(state)) as store:
            store.mark_running(run_id, {})
            run = store.get_run(run_id)
            assert run is not None
            self.launched.append(run)

    def card(
        self, number: int, priority: str | None, kind: str = "fix", status: str = "Ready"
    ) -> ProjectQueueItem:
        card = ProjectQueueItem(
            f"P{number}",
            f"I{number}",
            {
                self.shared.project.status.id: self.shared.project.status.option(status.lower()),
                self.shared.project.owner.id: self.shared.project.owner.option("factory"),
            },
            SourceItem(
                id=f"I{number}",
                repository="example/evals" if kind == "eval" else "example/work",
                number=number,
                author="writer",
                labels=frozenset(),
                issue_type={"fix": "Bug", "feature": "Feature", "task": "Task", "eval": "Eval"}[
                    kind
                ],
                state="OPEN",
                title="fixture",
                body="```eval\nrepetitions=3\n```" if kind == "eval" else "fixture",
            ),
            priority,
        )
        self.board.cards.append(card)
        return card

    def tick(self) -> list[Run]:
        previous = len(self.launched)
        runtime.cycle(self.local.state_path, self.config)
        for handler in self.handlers.values():
            handler.attach_store(self.store)
        return self.launched[previous:]

    def finish(self, run: Run, *, planning: bool = False, verdict: str | None = None) -> None:
        self.controller.record_result(
            run.id,
            AttemptResult(
                "failed" if planning else "completed",
                verdict,
                {"failure_stage": "pre-suite"}
                if planning
                else {"score": 60}
                if run.kind == "eval"
                else {},
            ),
        )
        self.store.set_setting("consumed-results", run.id, {"complete": True})

    def seed_review(
        self,
        number: int,
        priority: str | None,
        kind: str = "fix",
        *,
        blocked: bool = False,
        fallback: bool = False,
        feedback: bool = True,
        ready: bool = False,
    ) -> Claim:
        card = self.card(number, priority, kind)
        handler = self.handlers[kind]
        snapshot = handler.snapshot(card, self.board, self.shared)
        assert snapshot is not None
        claim = self.controller.accept(snapshot, resolve=handler.resolve_request)
        assert claim is not None
        pr = {
            "number": number,
            "branch": f"factory/{kind}-{number}-abcd",
            "head_sha": "a" * 40,
            "url": f"https://example.invalid/{number}",
        }
        if fallback:
            run = self.store.reserve_run(
                claim.id, kind, lane="low", reason="initial", evidence_path="/e"
            )
            self.store.mark_running(run.id, {})
            self.store.finish_run(run.id, execution_status="completed", result={"pr": pr})
            self.store.set_setting("consumed-results", run.id, {"complete": True})
        self.store.set_claim_lifecycle(
            claim.id,
            "blocked" if blocked else "settled",
            {
                "verdict": "pending-human-review",
                "review_checkpoint": "2026-01-01",
                **({"blocked_by": "review"} if blocked else {}),
                **({} if fallback else {"pr": pr}),
            },
        )
        card.fields[self.shared.project.status.id] = self.shared.project.status.option(
            "ready" if ready else "review"
        )
        if feedback:
            self.board.review_feedback[number] = ReviewActivity(
                (), (), (IssueComment("feedback", "please revise", "writer", "2099-01-01"),)
            )
        return self.store.get_claim(claim.id) or claim
