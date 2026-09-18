"""Fix work-kind handler: bug admission, sandboxed launch, and outcome mapping."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from agent_factory.config import FixTarget, LocalConfig, ScheduleConfig, SharedConfig
from agent_factory.controller import (
    AttemptResult,
    ClaimPresentation,
    ExecutionPlan,
    RequestSnapshot,
)
from agent_factory.github import (
    WRITER_PERMISSIONS,
    BranchInfo,
    GitHubApiError,
    GitHubClient,
    IssueComment,
    ProjectQueueItem,
    PullRequestInfo,
)
from agent_factory.operations import Diagnostic, model_authentication
from agent_factory.routing import SourceItem
from agent_factory.store import NONTERMINAL_RUN_STATUSES, Claim, ClaimDraft, ClaimStore, Run
from agent_factory.suites.and_scene import ReadinessError
from agent_factory.supervisor import SupervisionLimits
from agent_factory.work_kinds.base import (
    Classification,
    Feedback,
    Gesture,
    Outcome,
    Preparation,
    ReportEvent,
    card_status,
    claim_is_idle,
    mapping,
    providers_from_roles,
)
from agent_factory.work_kinds.fix import launch
from agent_factory.work_kinds.fix.cleanup import FixCleanup
from agent_factory.work_kinds.fix.outcome import read_outcome
from agent_factory.work_kinds.fix.readiness import check_readiness
from agent_factory.work_kinds.fix.workspace import FixWorkspace

Resolver = Callable[[FixTarget], tuple[str, str, str]]


class FixGitHub(Protocol):
    """The GitHub calls the handler needs for reconciliation and issue input."""

    def get_permission(self, repository: str, login: str) -> str | None: ...

    def get_branch(self, repository: str, branch: str) -> BranchInfo | None: ...

    def list_open_pull_requests_for_head(
        self, repository: str, branch: str
    ) -> list[PullRequestInfo]: ...

    def get_source_item(self, repository: str, number: int) -> SourceItem: ...

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]: ...


class FixHandler:
    """Owns every fix-shaped decision: admission, launch, and outcome mapping."""

    kind = "fix"

    def __init__(
        self,
        shared: SharedConfig,
        local: LocalConfig,
        *,
        resolver: Resolver | None = None,
        workspace: FixWorkspace | None = None,
    ) -> None:
        self._shared = shared
        self._local = local
        self._contract = shared.fix.contract
        self._resolver = resolver
        self._workspace = workspace
        self._store: ClaimStore | None = None
        self._cleanup: FixCleanup | None = None
        self._installation_token: Callable[[], str] | None = None
        self._github: FixGitHub | None = None

    @classmethod
    def from_config(cls, shared: SharedConfig, local: LocalConfig) -> FixHandler:
        workspace = FixWorkspace(
            local.storage_root, local.repositories.agent_runner, local.repositories.agent_skills
        )
        return cls(shared, local, workspace=workspace)

    def attach_store(self, store: ClaimStore) -> None:
        self._store = store
        self._cleanup = FixCleanup(
            store, private_root=self._local.storage_root.expanduser() / "private"
        )

    def attach_installation_token(self, provider: Callable[[], str]) -> None:
        """Let readiness reject a fix credential that is really the App installation token."""
        self._installation_token = provider

    def attach_github(self, client: FixGitHub) -> None:
        """Give reconciliation and issue-input construction the controller's read client."""
        self._github = client

    def handles(self, snapshot: RequestSnapshot) -> bool:
        return (
            not snapshot.closed
            and snapshot.owner == "factory"
            and snapshot.status == "Ready"
            and snapshot.issue_type == "Bug"
            and "needs-input" not in snapshot.labels
            and snapshot.author_permission in WRITER_PERMISSIONS
        )

    def snapshot(
        self, card: ProjectQueueItem, client: object, shared: SharedConfig
    ) -> RequestSnapshot | None:
        source = card.source
        targets = {target.repository for target in shared.fix.targets}
        if (
            source.repository not in targets
            or source.state.lower() == "closed"
            or source.issue_type != "Bug"
            or "needs-input" in source.labels
            or card.fields.get(shared.project.owner.id) != shared.project.owner.option("factory")
            or card_status(shared, card) != "Ready"
        ):
            return None
        github = cast(GitHubClient, client)
        permission = github.get_permission(source.repository, source.author)
        if permission not in WRITER_PERMISSIONS:
            return None
        return RequestSnapshot(
            source.repository,
            source.number,
            source.id,
            card.id,
            source.author,
            permission,
            "Bug",
            source.labels,
            "Ready",
            "factory",
            None,
            source.body,
            False,
        )

    def request_fingerprint(self, snapshot: RequestSnapshot) -> str | Feedback:
        return f"fix:{snapshot.repository}#{snapshot.issue_number}"

    def resolve(self, target: FixTarget) -> tuple[str, str, str]:
        """Fetch the target mirror and resolve the three configured branches to commits."""
        if self._resolver is not None:
            return self._resolver(target)
        if self._workspace is None:
            raise ReadinessError("fix handler has no workspace for mirrors and clones")
        from agent_factory import runtime

        token = self._installation_token() if self._installation_token is not None else None
        self._workspace.fetch_mirror(target.repository, token)
        target_sha = self._workspace.resolve_mirror(target.repository, target.branch)
        resolve = runtime._resolve_revision  # pyright: ignore[reportPrivateUsage]
        runner_sha = resolve(
            self._local.repositories.agent_runner, self._shared.fix.branches.runner
        )
        skills_sha = resolve(
            self._local.repositories.agent_skills, self._shared.fix.branches.skills
        )
        return target_sha, runner_sha, skills_sha

    def accept(
        self,
        snapshot: RequestSnapshot,
        store: ClaimStore,
        resolve: object,
    ) -> ClaimDraft | Feedback:
        target = next(
            (t for t in self._shared.fix.targets if t.repository == snapshot.repository), None
        )
        if target is None:
            return Feedback(f"{snapshot.repository} is not a configured fix target")
        resolver = cast(Resolver, resolve)
        target_sha, runner_sha, skills_sha = resolver(target)
        frozen: dict[str, object] = {
            "version": 1,
            "kind": "fix",
            "target": {"repository": target.repository, "branch": target.branch},
            "branches": {
                "runner": self._shared.fix.branches.runner,
                "skills": self._shared.fix.branches.skills,
            },
            "revisions": {"target": target_sha, "runner": runner_sha, "skills": skills_sha},
            "roles": dict(self._shared.fix.defaults),
            "contract": self._contract,
        }
        fingerprint = self.request_fingerprint(snapshot)
        assert isinstance(fingerprint, str)
        return ClaimDraft(
            snapshot.repository,
            snapshot.issue_number,
            snapshot.issue_id,
            snapshot.project_item_id,
            self.kind,
            fingerprint,
            frozen,
        )

    def readiness(
        self,
        local: LocalConfig,
        shared: SharedConfig,
        *,
        docker_diagnostic: Diagnostic | None = None,
    ) -> list[Diagnostic]:
        token: str | None = None
        minting_failure: Diagnostic | None = None
        if self._installation_token is not None:
            try:
                token = self._installation_token()
            except Exception as error:  # noqa: BLE001 - any minting failure fails closed
                # Fail closed: without the App token the fix credential cannot be proven
                # distinct from it, so readiness reports the failure as a diagnostic
                # instead of skipping the check or crashing the readiness pass.
                minting_failure = Diagnostic(
                    "fix credential",
                    False,
                    f"cannot mint the App installation token to validate the fix credential: "
                    f"{error}",
                    "Repair the GitHub App key or installation, then rerun doctor.",
                )
        diagnostics = check_readiness(
            local, shared, installation_token=token, docker_diagnostic=docker_diagnostic
        )
        if minting_failure is not None:
            diagnostics = [
                minting_failure if d.name == "fix credential" else d for d in diagnostics
            ]
        return diagnostics

    # -- launch -----------------------------------------------------------------

    def branch_name(self, claim: Claim) -> str:
        return launch.branch_name(claim.issue_number, claim.id)

    def reconcile(self, claim: Claim) -> PullRequestInfo | None:
        """Look for a branch or open PR from an earlier attempt before launching anything."""
        if self._github is None or self._store is None:
            raise ReadinessError("fix handler has no GitHub client for side-effect reconciliation")
        branch = self.branch_name(claim)
        try:
            existing = self._github.get_branch(claim.repository, branch)
            pulls = self._github.list_open_pull_requests_for_head(claim.repository, branch)
        except (GitHubApiError, OSError) as error:
            raise ReadinessError(
                f"cannot establish whether an earlier attempt pushed {branch}: {error}"
            ) from error
        if pulls:
            pull = pulls[0]
            self._store.set_claim_lifecycle(
                claim.id,
                "settled",
                {
                    "verdict": "pending-human-review",
                    "pr": {
                        "url": pull.url,
                        "number": pull.number,
                        "branch": branch,
                        "head_sha": pull.head_sha,
                    },
                },
            )
            self._store.record_event(
                claim.id,
                "handoff",
                f"An earlier attempt already opened a pull request: {pull.url}\n\n"
                "No new attempt was launched.",
            )
            return pull
        if existing is not None:
            self._store.record_event(
                claim.id,
                f"reconcile:branch:{existing.sha[:7]}",
                f"Branch `{branch}` already exists at {existing.sha[:7]} without an open pull "
                "request; the next attempt pushes over it or fails loudly.",
            )
        return None

    def prepare(self, claim: Claim) -> Preparation:
        if self._store is None or self._workspace is None or self._github is None:
            raise ReadinessError("fix handler is not wired for launch")
        if self.reconcile(claim) is not None:
            return Preparation()
        roles = mapping(claim.frozen_spec.get("roles"))
        failures = [
            check.detail
            for check in model_authentication({k: str(v) for k, v in roles.items()})
            if not check.available
        ]
        if failures:
            raise ReadinessError("; ".join(failures))
        if self._local.credentials.fix_environment is None:
            raise ReadinessError("credentials.fix_environment is not configured")
        issue = self._issue_input(claim)
        attempt = len([r for r in self._store.runs_for_claim(claim.id) if r.unit_key == "fix"])
        target = mapping(claim.frozen_spec.get("target"))
        repository = target.get("repository")
        if not isinstance(repository, str):
            raise ReadinessError("claim has no recorded target repository")
        clones = self._workspace.prepare_clones(
            claim.id, attempt, repository, mapping(claim.frozen_spec.get("revisions"))
        )
        if self._local.fix.execution == "host":
            # The recorded Runner commit does not execute on the host, so only the packaged
            # workflow is checked here; the installed Runner is checked by host readiness.
            launch.check_packaged_workflow(self._contract)
        else:
            launch.check_runner_contract(Path(clones["runner"]), self._contract)
        launch.check_target_catalog(Path(clones["repo"]))
        recorded = dict(mapping(claim.preparation.get("clones")))
        recorded[f"attempt-{attempt}"] = str(self._workspace.attempt_directory(claim.id, attempt))
        self._store.set_preparation(
            claim.id,
            {
                **claim.preparation,
                "clones": recorded,
                "branch_name": self.branch_name(claim),
                "issue": issue,
            },
        )
        return Preparation(payload={"clones": clones, "attempt": attempt, "issue": issue})

    def prepare_review(self, claim: Claim, review: Mapping[str, object]) -> Preparation:
        """Prepare fresh clones at the observed PR head for a review round."""
        if self._store is None or self._workspace is None:
            raise ReadinessError("fix handler is not wired for review launch")
        branch, head_sha = review.get("branch"), review.get("head_sha")
        if not isinstance(branch, str) or not isinstance(head_sha, str):
            raise ReadinessError("review input lacks the PR branch or head commit")
        attempt = len([r for r in self._store.runs_for_claim(claim.id) if r.unit_key == "fix"])
        target = mapping(claim.frozen_spec.get("target"))
        repository = target.get("repository")
        if not isinstance(repository, str):
            raise ReadinessError("claim has no recorded target repository")
        clones = self._workspace.prepare_review_clones(
            claim.id,
            attempt,
            repository,
            mapping(claim.frozen_spec.get("revisions")),
            branch=branch,
            head_sha=head_sha,
        )
        if self._local.fix.execution == "host":
            launch.check_packaged_workflow(launch.REVIEW_CONTRACT)
        else:
            launch.check_runner_contract(Path(clones["runner"]), self._contract)
        launch.check_target_catalog(Path(clones["repo"]))
        return Preparation(payload={"clones": clones, "attempt": attempt, "review": dict(review)})

    def _issue_input(self, claim: Claim) -> dict[str, object]:
        """Current issue fields plus the writer comments a new attempt may rely on."""
        assert self._github is not None
        from agent_factory.work_kinds.fix.blocked import eligible_comments

        github = self._github
        try:
            item = github.get_source_item(claim.repository, claim.issue_number)
            comments = github.list_comment_records(claim.repository, claim.issue_number)
        except (GitHubApiError, OSError) as error:
            raise ReadinessError(f"cannot read the issue for launch input: {error}") from error
        cache: dict[str, str | None] = {}

        def permission(login: str) -> str | None:
            # Launch input is frozen for the whole attempt, so a lookup failure must hold
            # the attempt rather than silently drop a writer's comment from issue.json.
            if login not in cache:
                try:
                    cache[login] = github.get_permission(claim.repository, login)
                except (GitHubApiError, OSError) as error:
                    raise ReadinessError(
                        f"cannot verify commenter permission for {login}: {error}"
                    ) from error
            return cache[login]

        since = claim.outcome.get("declined_at")
        eligible = eligible_comments(
            comments,
            since=since if isinstance(since, str) else None,
            bot_login=self._shared.bot_login,
            permission=permission,
        )
        prior = self._prior_pull_request(claim)
        return {
            "repository": claim.repository,
            "number": claim.issue_number,
            "title": item.title,
            "body": item.body,
            "author": item.author,
            "claim_id": claim.id,
            "prior_pull_request": prior,
            "comments": [
                {"author": c.author, "body": c.body, "created_at": c.created_at} for c in eligible
            ],
        }

    def _prior_pull_request(self, claim: Claim) -> dict[str, object] | None:
        if self._store is None:
            return None
        for run in reversed(self._store.runs_for_claim(claim.id)):
            pr = mapping(run.result.get("pr"))
            if isinstance(pr.get("url"), str):
                return dict(pr)
        return None

    def next_unit(self, claim: Claim, runs: Sequence[Run]) -> tuple[str | None, str]:
        unit_runs = [run for run in runs if run.unit_key == "fix"]
        if not unit_runs:
            return "fix", "initial"
        latest = unit_runs[-1]
        if latest.status in NONTERMINAL_RUN_STATUSES:
            return None, "initial"
        if _needs_recovery(latest) and latest.reason != "recovery":
            return "fix", "recovery"
        return None, "initial"

    def plan(self, claim: Claim, run: Run, preparation: Preparation) -> ExecutionPlan:
        clones = mapping(preparation.payload.get("clones"))
        if not all(isinstance(clones.get(name), str) for name in ("repo", "runner", "skills")):
            raise ReadinessError("fix execution plan is missing prepared clones")
        # Each attempt gets its own artifact directory: there is no resume, and a stale
        # outcome or log from an earlier attempt must never be read as this one's.
        evidence = attempt_evidence(run)
        evidence.mkdir(parents=True, exist_ok=True)
        (
            evidence / ("review-outcome.json" if run.reason == "review" else "fix-outcome.json")
        ).unlink(missing_ok=True)
        if run.reason == "review":
            review = dict(mapping(preparation.payload.get("review")))
            review["attempt"] = run.attempt_number + 1
            launch.write_review_input(evidence, review)
            workflow_contract = launch.REVIEW_CONTRACT
            branch = review.get("branch")
            if not isinstance(branch, str):
                raise ReadinessError("review input lacks PR branch")
        else:
            workflow_contract = self._contract
            branch = self.branch_name(claim)
            issue = dict(mapping(preparation.payload.get("issue")))
            issue["attempt"] = run.attempt_number + 1
            issue["reason"] = run.reason
            launch.write_issue_input(evidence, issue)
        credential = launch.validated_credential_copy(
            self._local, self._local.storage_root.expanduser() / "private" / run.id / "fix.env"
        )
        if self._local.fix.execution == "host":
            return launch.build_host_plan(
                evidence=evidence,
                repo_clone=Path(str(clones["repo"])),
                credential_copy=credential,
                roles=mapping(claim.frozen_spec.get("roles")),
                branch=branch,
                contract=workflow_contract,
                recorded_revisions=mapping(claim.frozen_spec.get("revisions")),
            )
        launch.stage_workflow(evidence, workflow_contract)
        return launch.build_plan(
            run_id=run.id,
            evidence=evidence,
            clones={name: str(clones[name]) for name in ("repo", "runner", "skills")},
            credential_copy=credential,
            roles=mapping(claim.frozen_spec.get("roles")),
            branch=branch,
            contract=workflow_contract,
        )

    # -- results ----------------------------------------------------------------

    def read_result(self, run: Run) -> AttemptResult:
        hints = mapping(run.plan.get("ownership_hints"))
        extra = {
            key: hints[key]
            for key in (
                "image_tag",
                "branch_name",
                "sandbox",
                "runner_executable",
                "runner_version",
                "session_dir",
            )
            if isinstance(hints.get(key), str)
        }
        base = AttemptResult(
            "interrupted" if run.status == "timed_out" else run.status,
            None,
            {**run.result, **extra},
        )
        if run.status == "timed_out":
            return base
        payload = read_outcome(
            attempt_evidence(run),
            launch.REVIEW_CONTRACT if run.reason == "review" else self._contract,
        )
        if payload is None:
            return base
        outcome = payload.get("outcome")
        return AttemptResult(
            "completed", outcome if isinstance(outcome, str) else None, {**payload, **extra}
        )

    def classify(self, run: Run, result: AttemptResult) -> Classification:
        if result.product_verdict is None:
            return Classification("technical")
        if result.product_verdict == "needs-input":
            return Classification("blocked")
        return Classification("settled")

    def settle(self, claim: Claim, runs: Sequence[Run]) -> Outcome | None:
        if claim.lifecycle in {"settled", "blocked"}:
            return None
        unit_runs = [run for run in runs if run.unit_key == "fix"]
        if not unit_runs:
            return None
        latest = unit_runs[-1]
        if latest.status not in {"completed", "failed"}:
            return None
        if _needs_recovery(latest):
            return None
        outcome = latest.result.get("outcome")
        if outcome == "needs-input":
            if self._store is not None:
                reasons = _reasons_text(latest.result)
                declined = datetime.now(UTC).isoformat()
                blocked: dict[str, object] = {"declined_at": declined}
                if latest.reason == "review":
                    blocked.update(
                        {
                            "blocked_by": "review",
                            "review_checkpoint": declined,
                            "pre_review_verdict": claim.outcome.get(
                                "pre_review_verdict", "pending-human-review"
                            ),
                        }
                    )
                self._store.set_claim_lifecycle(claim.id, "blocked", blocked)
                body = f"Needs input.\n\n{reasons}"
                self._store.record_event(
                    claim.id, f"{latest.id}:needs-input", _with_host_note(body, latest.result)
                )
            return None
        if outcome == "pull-request":
            return Outcome("pending-human-review", event_body=_pr_message(latest.result))
        if outcome == "failed":
            return Outcome("failed", event_body=_failed_message(latest.result))
        return None

    def presentation(self, claim: Claim) -> ClaimPresentation:
        # Cancelled claims are presented by the controller before dispatching here.
        if claim.lifecycle == "settled":
            verdict = claim.outcome.get("verdict")
            return ClaimPresentation("Review", verdict if isinstance(verdict, str) else None, ())
        if claim.lifecycle == "blocked":
            if claim.outcome.get("blocked_by") == "review":
                return ClaimPresentation(
                    "Review",
                    str(claim.outcome.get("pre_review_verdict") or "pending-human-review"),
                    (),
                    labels={"needs-input": True},
                )
            return ClaimPresentation("Running", None, (), labels={"needs-input": True})
        if claim.lifecycle == "waiting":
            verdict = claim.outcome.get("verdict")
            return ClaimPresentation(
                "Ready", verdict if isinstance(verdict, str) else "infra-error", ()
            )
        return ClaimPresentation(
            "Ready" if claim_is_idle(self._store, claim) else "Running", None, ()
        )

    def report_events(self, claim: Claim, run: Run, result: AttemptResult) -> list[ReportEvent]:
        return []

    def gesture(
        self, claim: Claim, card: ProjectQueueItem, comments: Sequence[IssueComment]
    ) -> Gesture | None:
        if claim.lifecycle == "settled":
            return "fresh" if card_status(self._shared, card) == "Ready" else None
        if claim.lifecycle == "blocked":
            if comments or card_status(self._shared, card) == "Ready":
                return "unblock"
            return None
        return None

    def limits(self, local: LocalConfig) -> SupervisionLimits:
        return SupervisionLimits(
            local.fix.limits.inactivity_seconds,
            local.fix.limits.execution_seconds,
            local.fix.limits.total_seconds,
        )

    def window(self, local: LocalConfig) -> ScheduleConfig:
        if local.fix.schedule is not None:
            return local.fix.schedule
        return ScheduleConfig.always(local.schedule.timezone, local.schedule.poll_seconds)

    def providers(self, claim: Claim) -> set[str]:
        return providers_from_roles(mapping(claim.frozen_spec.get("roles")))

    def attempt_message(self, run: Run, stored_result: Mapping[str, object], *, stage: str) -> str:
        attempt = run.attempt_number + 1
        outcome = stored_result.get("outcome")
        if stage == "complete" and isinstance(outcome, str):
            return f"Fix attempt {attempt} finished with outcome `{outcome}`."
        reason = _technical_reason(stored_result)
        if stage == "retry":
            return (
                f"Fix attempt {attempt} failed technically ({reason}); one recovery attempt "
                "from fresh clones at the recorded commits follows."
            )
        if stage == "exhausted":
            return _with_host_note(
                f"Fix attempt {attempt} failed technically ({reason}); the recovery attempt is "
                "used up, so this bug is handed back with `infra-error`.",
                stored_result,
            )
        return f"Fix attempt {attempt} settled."

    def refs_text(self, claim: Claim) -> str | None:
        revisions = mapping(claim.frozen_spec.get("revisions"))
        parts: list[str] = []
        for key in ("target", "runner", "skills"):
            value = revisions.get(key)
            if not isinstance(value, str) or not value:
                if self._store is not None:
                    self._store.record_event(
                        claim.id,
                        "invalid-revisions",
                        f"Cannot report frozen revisions: missing or invalid {key}. "
                        "Repair the saved claim inputs.",
                    )
                return None
            parts.append(f"{key}@{value[:7]}")
        return " ".join(parts)

    def frozen_inputs_event(self, claim: Claim) -> str | None:
        target = mapping(claim.frozen_spec.get("target"))
        branches = mapping(claim.frozen_spec.get("branches"))
        revisions = mapping(claim.frozen_spec.get("revisions"))
        roles = mapping(claim.frozen_spec.get("roles"))

        def commit(name: str) -> str:
            value = revisions.get(name)
            return value[:7] if isinstance(value, str) else "unknown"

        lines = [
            "Fix inputs frozen for this claim:",
            "",
            f"- target: {target.get('repository')} branch `{target.get('branch')}` "
            f"at {commit('target')}",
            f"- Agent Runner: branch `{branches.get('runner', 'main')}` at {commit('runner')}",
            f"- Agent Skills: branch `{branches.get('skills', 'main')}` at {commit('skills')}",
            f"- fix branch: `{self.branch_name(claim)}`",
            "- roles: " + ", ".join(f"{role}={profile}" for role, profile in roles.items()),
        ]
        return "\n".join(lines)

    def cleanup(self, claim: Claim, *, board_status: str = "") -> None:
        if self._cleanup is not None:
            self._cleanup.reconcile(claim.id, board_status=board_status)


def attempt_evidence(run: Run) -> Path:
    """The artifact directory mounted at /artifacts for one attempt of a fix claim."""
    return Path(run.evidence_path).resolve() / f"attempt-{run.attempt_number + 1}"


def _needs_recovery(run: Run) -> bool:
    return run.status in {"failed", "interrupted", "timed_out"} and (
        run.result.get("outcome") is None
    )


def _technical_reason(result: Mapping[str, object]) -> str:
    timeout = result.get("timeout")
    if isinstance(timeout, str):
        return f"{timeout} limit exceeded"
    for key in ("reason", "error"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value.replace("\n", " ")[:300]
    return "no structured outcome was written"


def _reasons_text(result: Mapping[str, object]) -> str:
    reasons = result.get("reasons")
    if isinstance(reasons, list):
        return "\n".join(f"- {reason}" for reason in cast(list[object], reasons))
    return ""


def _with_host_note(body: str, result: Mapping[str, object]) -> str:
    """Outcome comments for host-mode runs say so; Docker-mode comments are unchanged."""
    if result.get("sandbox") == "host":
        return f"{body}\n\n{launch.HOST_NOTE}"
    return body


def _pr_message(result: Mapping[str, object]) -> str:
    pr = mapping(result.get("pr"))
    url = pr.get("url")
    body = f"Pull request opened: {url}" if isinstance(url, str) else "Pull request opened."
    return _with_host_note(body, result)


def _failed_message(result: Mapping[str, object]) -> str:
    reasons = _reasons_text(result)
    pr = mapping(result.get("pr"))
    url = pr.get("url")
    lines = ["Fix attempt failed."]
    if reasons:
        lines.append(reasons)
    if isinstance(url, str):
        lines.append(f"Pull request: {url}")
    return _with_host_note("\n\n".join(lines), result)
