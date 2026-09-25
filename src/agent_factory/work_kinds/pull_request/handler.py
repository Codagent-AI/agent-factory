"""Fix work-kind handler: bug admission, sandboxed launch, and outcome mapping."""

from __future__ import annotations

import logging
import shutil
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
from agent_factory.work_kinds.pull_request import launch
from agent_factory.work_kinds.pull_request.cleanup import PullRequestCleanup
from agent_factory.work_kinds.pull_request.kinds import FIX, PullRequestKind, ReconcilePolicy
from agent_factory.work_kinds.pull_request.outcome import read_interpreted_outcome
from agent_factory.work_kinds.pull_request.readiness import check_readiness
from agent_factory.work_kinds.pull_request.workspace import PullRequestWorkspace

Resolver = Callable[[FixTarget], tuple[str, str, str]]
logger = logging.getLogger(__name__)


def feature_resume_point(
    outcome: object, stopped_step: object, checkpoint: str | None, draft: bool, continuing: bool
) -> str:
    """Choose the first workflow step that has no durable completed checkpoint."""
    if continuing:
        return "implement" if checkpoint in {"planned", "implemented", "archived"} else ""
    if outcome == "needs-input":
        return stopped_step if isinstance(stopped_step, str) and stopped_step != "preflight" else ""
    if draft:
        return "verify"
    return {"planned": "implement", "implemented": "archive", "archived": "verify"}.get(
        checkpoint or "", ""
    )


class PullRequestGitHub(Protocol):
    """The GitHub calls the handler needs for reconciliation and issue input."""

    def get_permission(self, repository: str, login: str) -> str | None: ...

    def get_branch(self, repository: str, branch: str) -> BranchInfo | None: ...

    def list_open_pull_requests_for_head(
        self, repository: str, branch: str
    ) -> list[PullRequestInfo]: ...

    def list_open_factory_pull_requests_for_issue(
        self, repository: str, number: int
    ) -> list[PullRequestInfo]: ...

    def get_source_item(self, repository: str, number: int) -> SourceItem: ...

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]: ...

    def create_comment(self, repository: str, number: int, body: str) -> str | None: ...


class PullRequestHandler:
    """Owns every fix-shaped decision: admission, launch, and outcome mapping."""

    def __init__(
        self,
        definition: PullRequestKind,
        shared: SharedConfig,
        local: LocalConfig,
        *,
        resolver: Resolver | None = None,
        workspace: PullRequestWorkspace | None = None,
    ) -> None:
        self.definition = definition
        self.kind = definition.kind
        self._shared = shared
        self._local = local
        self._contract = definition.contract(shared)
        self._resolver = resolver
        self._workspace = workspace or PullRequestWorkspace(
            local.storage_root, local.repositories.agent_runner, local.repositories.agent_skills
        )
        self._store: ClaimStore | None = None
        self._cleanup: PullRequestCleanup | None = None
        self._installation_token: Callable[[], str] | None = None
        self._github: PullRequestGitHub | None = None

    @classmethod
    def from_config(cls, shared: SharedConfig, local: LocalConfig) -> PullRequestHandler:
        workspace = PullRequestWorkspace(
            local.storage_root, local.repositories.agent_runner, local.repositories.agent_skills
        )
        return cls(FIX, shared, local, workspace=workspace)

    def accepted_message(self) -> str:
        return f"{self.definition.noun} inputs accepted and frozen."

    def resolve_request(self, request: object) -> tuple[str, ...]:
        if not isinstance(request, FixTarget):
            raise ReadinessError(f"{self.kind} handler cannot resolve a non-target request")
        return self.resolve(request)

    def execution_mode(self, local: LocalConfig) -> str:
        return self.definition.local(local).execution

    def needs_sandbox_memory(self, local: LocalConfig) -> bool:
        return self.execution_mode(local) == "docker"

    def ready_handoff(
        self,
        card: ProjectQueueItem,
        shared: SharedConfig,
        permission_cache: dict[tuple[str, str], str | None],
    ) -> None:
        if self._github is None or (self.kind == "feature" and shared.feature is None):
            return
        source = card.source
        factory = shared.project.owner.option("factory")
        if (
            source.repository
            not in {target.repository for target in self.definition.targets(shared)}
            or source.pull_request
            or source.state.lower() == "closed"
            or source.issue_type != self.definition.issue_type(shared)
            or card_status(shared, card) != "Ready"
            or card.fields.get(shared.project.owner.id) == factory
        ):
            return
        key = (source.repository, source.author)
        try:
            if key not in permission_cache:
                permission_cache[key] = self._github.get_permission(*key)
        except GitHubApiError as error:
            permission_cache[key] = None
            logger.warning(
                "Cannot verify Ready %s author permission; retrying next cycle "
                "(repository=%s author=%s card=%s): %s",
                self.definition.noun,
                source.repository,
                source.author,
                card.id,
                error,
            )
            return
        if permission_cache[key] not in WRITER_PERMISSIONS:
            if self.kind == "feature":
                marker = "<!-- agent-factory-feature-handoff:v1 -->"
                try:
                    comments = self._github.list_comment_records(source.repository, source.number)
                    if not any(marker in comment.body for comment in comments):
                        self._github.create_comment(
                            source.repository,
                            source.number,
                            f"{marker}\nFeature handoff requires the issue author to have "
                            "write, maintain, or admin access to this repository. "
                            "The card remains unassigned.",
                        )
                except GitHubApiError as error:
                    logger.warning("Cannot explain Feature handoff for %s: %s", card.id, error)
            return
        try:
            cast(GitHubClient, self._github).set_single_select_field(
                shared.project.id, card.id, shared.project.owner.id, factory
            )
        except GitHubApiError as error:
            logger.warning(
                "Cannot assign Ready %s to Factory; retrying next cycle "
                "(repository=%s author=%s card=%s): %s",
                self.definition.noun,
                source.repository,
                source.author,
                card.id,
                error,
            )
            return
        card.fields[shared.project.owner.id] = factory

    def unblock(
        self,
        store: ClaimStore,
        client: GitHubClient,
        shared: SharedConfig,
        local: LocalConfig,
        card: ProjectQueueItem,
        claim: Claim,
        *,
        bot_login: str,
        artifact_root: Path,
        now: datetime,
        memory_available: bool = True,
    ) -> tuple[Run, Preparation] | None:
        from agent_factory.work_kinds.pull_request.blocked import process_blocked_claim

        return process_blocked_claim(
            store,
            client,
            self,
            shared,
            local,
            card,
            claim,
            bot_login=bot_login,
            artifact_root=artifact_root,
            now=now,
            memory_available=memory_available,
        )

    def review_round(
        self,
        store: ClaimStore,
        client: GitHubClient,
        claim: Claim,
        *,
        bot_login: str,
        artifact_root: Path,
        now: datetime,
        local: LocalConfig,
        readiness: Callable[[], bool],
        memory_available: bool = True,
    ) -> tuple[Run, Preparation] | None:
        from agent_factory.work_kinds.pull_request.review import process_review_claim

        return process_review_claim(
            store,
            client,
            self,
            claim,
            bot_login=bot_login,
            artifact_root=artifact_root,
            now=now,
            local=local,
            readiness=readiness,
            memory_available=memory_available,
        )

    def merge_sync(
        self,
        store: ClaimStore,
        client: GitHubClient,
        local: LocalConfig,
        claim: Claim,
        *,
        bot_login: str,
        card_done: bool,
    ) -> None:
        from agent_factory.work_kinds.pull_request.sync import sync_claim

        sync_claim(
            store,
            client,
            local,
            claim,
            bot_login=bot_login,
            card_done=card_done,
            definition=self.definition,
        )

    def pending_sync(self, claim: Claim) -> bool:
        from agent_factory.work_kinds.pull_request.sync import pending_sync

        return self._store is not None and pending_sync(self._store, claim, self.definition)

    def retention_targets(self, run: Run) -> list[Path]:
        from agent_factory.retention import pull_request_attempt_targets

        return pull_request_attempt_targets(run)

    def blocked_reason(self, claim: Claim) -> str:
        if self._store is None:
            return "needs input"
        runs = [
            run
            for run in self._store.runs_for_claim(claim.id)
            if run.unit_key == self.definition.unit_key
        ]
        if not runs:
            return "needs input"
        event = mapping(mapping(claim.reporting.get("events")).get(f"{runs[-1].id}:needs-input"))
        body = event.get("body")
        return (
            body.removeprefix("Needs input.\n\n").strip()
            if isinstance(body, str)
            else "needs input"
        )

    def attach_store(self, store: ClaimStore) -> None:
        self._store = store
        self._cleanup = PullRequestCleanup(
            store, private_root=self._local.storage_root.expanduser() / "private"
        )

    def attach_installation_token(self, provider: Callable[[], str]) -> None:
        """Let readiness reject a fix credential that is really the App installation token."""
        self._installation_token = provider

    def attach_github(
        self, client: PullRequestGitHub, token_provider: Callable[[], str] | None = None
    ) -> None:
        """Give reconciliation and issue-input construction the controller's read client."""
        self._github = client
        if token_provider is not None:
            self._installation_token = token_provider

    def handles(self, snapshot: RequestSnapshot) -> bool:
        return (
            (self.kind != "feature" or self._shared.feature is not None)
            and snapshot.repository
            in {target.repository for target in self.definition.targets(self._shared)}
            and not snapshot.closed
            and snapshot.owner == "factory"
            and snapshot.status == "Ready"
            and snapshot.issue_type == self.definition.issue_type(self._shared)
            and "needs-input" not in snapshot.labels
            and snapshot.author_permission in WRITER_PERMISSIONS
        )

    def snapshot(
        self, card: ProjectQueueItem, client: object, shared: SharedConfig
    ) -> RequestSnapshot | None:
        source = card.source
        targets = {target.repository for target in self.definition.targets(shared)}
        if (
            (self.kind == "feature" and shared.feature is None)
            or source.repository not in targets
            or source.state.lower() == "closed"
            or source.issue_type != self.definition.issue_type(shared)
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
            self.definition.issue_type(shared),
            source.labels,
            "Ready",
            "factory",
            None,
            source.body,
            False,
        )

    def request_fingerprint(self, snapshot: RequestSnapshot) -> str | Feedback:
        return f"{self.kind}:{snapshot.repository}#{snapshot.issue_number}"

    def resolve(self, target: FixTarget) -> tuple[str, str, str]:
        """Fetch the target mirror and resolve the three configured branches to commits."""
        if self._resolver is not None:
            return self._resolver(target)
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
        if self.kind == "feature" and self._shared.feature is None:
            return Feedback("feature admission is disabled")
        target = next(
            (
                t
                for t in self.definition.targets(self._shared)
                if t.repository == snapshot.repository
            ),
            None,
        )
        if target is None:
            return Feedback(f"{snapshot.repository} is not a configured {self.kind} target")
        resolver = cast(Resolver, resolve)
        target_sha, runner_sha, skills_sha = resolver(target)
        frozen: dict[str, object] = {
            "version": 1,
            "kind": self.kind,
            "target": {"repository": target.repository, "branch": target.branch},
            "branches": {
                "runner": self._shared.fix.branches.runner,
                "skills": self._shared.fix.branches.skills,
            },
            "revisions": {"target": target_sha, "runner": runner_sha, "skills": skills_sha},
            "roles": dict(self.definition.defaults(self._shared)),
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
                    f"{self.kind} credential",
                    False,
                    f"cannot mint the App installation token to validate the "
                    f"{self.kind} credential: "
                    f"{error}",
                    "Repair the GitHub App key or installation, then rerun doctor.",
                )
        diagnostics = check_readiness(
            local,
            shared,
            installation_token=token,
            docker_diagnostic=docker_diagnostic,
            definition=self.definition,
        )
        if minting_failure is not None:
            diagnostics = [
                minting_failure if d.name == f"{self.kind} credential" else d for d in diagnostics
            ]
        return diagnostics

    # -- launch -----------------------------------------------------------------

    def branch_name(self, claim: Claim) -> str:
        return launch.branch_name(claim.issue_number, claim.id, self.definition.branch_prefix)

    def reconcile(self, claim: Claim) -> PullRequestInfo | None:
        """Look for a branch or open PR from an earlier attempt before launching anything."""
        if self._github is None or self._store is None:
            raise ReadinessError(f"{self.kind} handler has no GitHub client for reconciliation")
        branch = self.branch_name(claim)
        try:
            existing = self._github.get_branch(claim.repository, branch)
            pulls = self._github.list_open_pull_requests_for_head(claim.repository, branch)
        except (GitHubApiError, OSError) as error:
            raise ReadinessError(
                f"cannot establish whether an earlier attempt pushed {branch}: {error}"
            ) from error
        if self.definition.reconcile is ReconcilePolicy.RESUME_FROM_OWN_BRANCH:
            if len(pulls) > 1:
                raise ReadinessError(f"ambiguous open pull requests for {branch}")
            try:
                others = self._github.list_open_factory_pull_requests_for_issue(
                    claim.repository, claim.issue_number
                )
            except (GitHubApiError, OSError) as error:
                raise ReadinessError(
                    f"cannot establish open factory pull requests: {error}"
                ) from error
            completed = {pr.number: pr for pr in (*pulls, *others) if not pr.is_draft}
            if len(completed) > 1:
                raise ReadinessError("ambiguous open factory pull requests for issue")
            if completed:
                pulls = list(completed.values())
            elif pulls:
                self._store.set_preparation(
                    claim.id,
                    {
                        **claim.preparation,
                        "resume": {"branch": branch, "head_sha": pulls[0].head_sha, "draft": True},
                    },
                )
                return None
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
                        "branch": pull.branch or branch,
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
            if self.definition.reconcile is ReconcilePolicy.RESUME_FROM_OWN_BRANCH:
                self._store.set_preparation(
                    claim.id,
                    {
                        **claim.preparation,
                        "resume": {"branch": branch, "head_sha": existing.sha},
                    },
                )
                return None
            self._store.record_event(
                claim.id,
                f"reconcile:branch:{existing.sha[:7]}",
                f"Branch `{branch}` already exists at {existing.sha[:7]} without an open pull "
                "request; the next attempt pushes over it or fails loudly.",
            )
        elif self.definition.reconcile is ReconcilePolicy.RESUME_FROM_OWN_BRANCH:
            self._store.set_preparation(
                claim.id,
                {key: value for key, value in claim.preparation.items() if key != "resume"},
            )
        return None

    def prepare(self, claim: Claim) -> Preparation:
        if self._store is None or self._github is None:
            raise ReadinessError(f"{self.kind} handler is not wired for launch")
        if self.reconcile(claim) is not None:
            return Preparation()
        claim = self._store.get_claim(claim.id) or claim
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
        attempt = len(
            [
                r
                for r in self._store.runs_for_claim(claim.id)
                if r.unit_key == self.definition.unit_key
            ]
        )
        target = mapping(claim.frozen_spec.get("target"))
        repository = target.get("repository")
        if not isinstance(repository, str):
            raise ReadinessError("claim has no recorded target repository")
        revisions = dict(mapping(claim.frozen_spec.get("revisions")))
        resume = mapping(claim.preparation.get("resume"))
        resume_from = ""
        prior_branch = ""
        prior_report: Path | None = None
        if self.kind == "feature":
            own_branch = resume.get("branch")
            previous = next(
                (
                    item
                    for item in reversed(self._store.claims_for_item(claim.project_item_id))
                    if item.id != claim.id and item.kind == "feature"
                ),
                None,
            )
            previous_runs = self._store.runs_for_claim(previous.id) if previous else []
            own_runs = self._store.runs_for_claim(claim.id)
            latest = own_runs[-1] if own_runs else None
            last_result: Mapping[str, object] = latest.result if latest else {}
            if isinstance(own_branch, str):
                token = self._installation_token() if self._installation_token else None
                checkpoint = self._workspace.feature_checkpoint(repository, own_branch, token)
                resume_from = feature_resume_point(
                    last_result.get("outcome"),
                    last_result.get("stopped_step"),
                    checkpoint,
                    resume.get("draft") is True,
                    False,
                )
                if latest is not None:
                    prior_report = attempt_evidence(latest)
            elif last_result.get("outcome") == "needs-input":
                # Let prepare-branch try the recorded resume point and write resume.json
                # if the pushed branch vanished since the stop.
                resume_from = feature_resume_point(
                    "needs-input", last_result.get("stopped_step"), None, False, False
                )
            elif previous is not None and previous_runs:
                branch = self.branch_name(previous)
                try:
                    exists = self._github.get_branch(repository, branch)
                except (GitHubApiError, OSError) as error:
                    raise ReadinessError(
                        f"cannot check prior feature branch {branch}: {error}"
                    ) from error
                if exists is not None:
                    token = self._installation_token() if self._installation_token else None
                    checkpoint = self._workspace.feature_checkpoint(repository, branch, token)
                    resume_from = feature_resume_point(None, None, checkpoint, False, True)
                    if resume_from:
                        prior_branch = branch
            if (
                last_result.get("outcome") == "needs-input"
                and last_result.get("stopped_step") == "preflight"
            ):
                resume_from = ""
        head_sha = resume.get("head_sha")
        if (
            self.kind != "feature"
            and self.definition.reconcile is ReconcilePolicy.RESUME_FROM_OWN_BRANCH
            and isinstance(head_sha, str)
        ):
            revisions["target"] = head_sha
            issue["resume"] = dict(resume)
        clones = self._workspace.prepare_clones(claim.id, attempt, repository, revisions)
        if self.definition.local(self._local).execution == "host":
            # The recorded Runner commit does not execute on the host, so only the packaged
            # workflow is checked here; the installed Runner is checked by host readiness.
            launch.check_packaged_workflow(self._contract, self.definition)
        else:
            launch.check_runner_contract(Path(clones["runner"]), self._contract, self.definition)
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
                "resume_from": resume_from,
                "prior_branch": prior_branch,
                "prior_report": str(prior_report) if prior_report else "",
            },
        )
        return Preparation(
            payload={
                "clones": clones,
                "attempt": attempt,
                "issue": issue,
                "resume_from": resume_from,
                "prior_branch": prior_branch,
                "prior_report": str(prior_report) if prior_report else "",
            }
        )

    def prepare_review(self, claim: Claim, review: Mapping[str, object]) -> Preparation:
        """Prepare fresh clones at the observed PR head for a review round."""
        if self._store is None:
            raise ReadinessError(f"{self.kind} handler is not wired for review launch")
        branch, head_sha = review.get("branch"), review.get("head_sha")
        if not isinstance(branch, str) or not isinstance(head_sha, str):
            raise ReadinessError("review input lacks the PR branch or head commit")
        attempt = len(
            [
                r
                for r in self._store.runs_for_claim(claim.id)
                if r.unit_key == self.definition.unit_key
            ]
        )
        target = mapping(claim.frozen_spec.get("target"))
        repository = target.get("repository")
        if not isinstance(repository, str):
            raise ReadinessError("claim has no recorded target repository")
        # The PR head was pushed after the mirror was last fetched for this claim.
        token = self._installation_token() if self._installation_token is not None else None
        self._workspace.fetch_mirror(repository, token)
        clones = self._workspace.prepare_review_clones(
            claim.id,
            attempt,
            repository,
            mapping(claim.frozen_spec.get("revisions")),
            branch=branch,
            head_sha=head_sha,
        )
        if self.definition.local(self._local).execution == "host":
            launch.check_packaged_workflow(launch.REVIEW_CONTRACT, self.definition)
        else:
            launch.check_runner_contract(
                Path(clones["runner"]), launch.REVIEW_CONTRACT, self.definition
            )
        launch.check_target_catalog(Path(clones["repo"]))
        return Preparation(payload={"clones": clones, "attempt": attempt, "review": dict(review)})

    def _issue_input(self, claim: Claim) -> dict[str, object]:
        """Current issue fields plus the writer comments a new attempt may rely on."""
        assert self._github is not None
        from agent_factory.work_kinds.pull_request.blocked import eligible_comments

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
        prior = self.prior_pull_request(claim)
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

    def prior_pull_request(self, claim: Claim) -> dict[str, object] | None:
        if self._store is None:
            return None
        for run in reversed(self._store.runs_for_claim(claim.id)):
            pr = mapping(run.result.get("pr"))
            if isinstance(pr.get("url"), str):
                return dict(pr)
        return None

    def next_unit(self, claim: Claim, runs: Sequence[Run]) -> tuple[str | None, str]:
        unit_runs = [run for run in runs if run.unit_key == self.definition.unit_key]
        if not unit_runs:
            return self.definition.unit_key, "initial"
        latest = unit_runs[-1]
        if latest.status in NONTERMINAL_RUN_STATUSES:
            return None, "initial"
        if latest.result.get("failure_stage") == "pre-suite":
            return self.definition.unit_key, latest.reason
        if _needs_recovery(latest) and latest.reason != "recovery":
            return self.definition.unit_key, "recovery"
        return None, "initial"

    def plan(self, claim: Claim, run: Run, preparation: Preparation) -> ExecutionPlan:
        clones = mapping(preparation.payload.get("clones"))
        if not all(isinstance(clones.get(name), str) for name in ("repo", "runner", "skills")):
            raise ReadinessError(f"{self.kind} execution plan is missing prepared clones")
        # Each attempt gets its own artifact directory: there is no resume, and a stale
        # outcome or log from an earlier attempt must never be read as this one's.
        evidence = attempt_evidence(run)
        evidence.mkdir(parents=True, exist_ok=True)
        (
            evidence
            / ("review-outcome.json" if run.reason == "review" else self.definition.outcome_file)
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
            if self.kind == "feature" and preparation.payload.get("resume_from") in {
                "archive",
                "verify",
                "finalize",
            }:
                source = preparation.payload.get("prior_report")
                if isinstance(source, str) and source:
                    output = Path(source) / launch.SESSION_DIR_NAME / "output"
                    destination = evidence / launch.SESSION_DIR_NAME / "output"
                    for report in output.glob("*session-report*.out"):
                        destination.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(report, destination / report.name)
        credential = launch.validated_credential_copy(
            self._local,
            self._local.storage_root.expanduser() / "private" / run.id / f"{self.kind}.env",
        )
        if self.definition.local(self._local).execution == "host":
            plan = launch.build_host_plan(
                evidence=evidence,
                repo_clone=Path(str(clones["repo"])),
                credential_copy=credential,
                roles=mapping(claim.frozen_spec.get("roles")),
                branch=branch,
                contract=workflow_contract,
                definition=self.definition,
                change_name=branch.removeprefix("factory/").replace("/", "-"),
                resume_from=str(preparation.payload.get("resume_from", "")),
                prior_branch=str(preparation.payload.get("prior_branch", "")),
                recorded_revisions=mapping(claim.frozen_spec.get("revisions")),
            )
            if self.kind == "feature" and run.reason != "review" and self._store is not None:
                resume_step = preparation.payload.get("resume_from")
                prior_branch = preparation.payload.get("prior_branch")
                if isinstance(prior_branch, str) and prior_branch:
                    start = f"continues prior claim's branch `{prior_branch}` at {resume_step}"
                elif isinstance(resume_step, str) and resume_step:
                    start = f"resumes at {resume_step}"
                else:
                    start = "starts fresh"
                self._store.record_event(
                    claim.id,
                    f"feature-admission:{run.id}",
                    f"Feature attempt {run.attempt_number + 1} {start}. "
                    f"Resolved refs: {self.refs_text(claim) or 'unavailable'}.",
                )
            return plan
        launch.stage_workflow(evidence, workflow_contract, self.definition)
        return launch.build_plan(
            run_id=run.id,
            evidence=evidence,
            clones={name: str(clones[name]) for name in ("repo", "runner", "skills")},
            credential_copy=credential,
            roles=mapping(claim.frozen_spec.get("roles")),
            branch=branch,
            contract=workflow_contract,
            definition=self.definition,
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
        interpreted = read_interpreted_outcome(
            attempt_evidence(run),
            launch.REVIEW_CONTRACT if run.reason == "review" else self._contract,
            "review-outcome.json" if run.reason == "review" else self.definition.outcome_file,
        ).outcome
        if interpreted is None:
            return base
        return AttemptResult(
            interpreted.execution_status,
            interpreted.product_verdict,
            {**interpreted.result, **extra},
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
        unit_runs = [run for run in runs if run.unit_key == self.definition.unit_key]
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
                body = (
                    _feature_stop_message(latest.result, claim.repository)
                    if self.kind == "feature" and latest.reason != "review"
                    else f"Needs input.\n\n{reasons}"
                )
                self._store.record_event(
                    claim.id, f"{latest.id}:needs-input", _with_host_note(body, latest.result)
                )
            return None
        if outcome == "pull-request":
            if latest.reason == "review" and self.kind == "feature":
                pr = mapping(claim.outcome.get("pr"))
                url = pr.get("url")
                answered_raw = latest.result.get("answered")
                changed_raw = latest.result.get("changed")
                answered = (
                    cast(list[object], answered_raw) if isinstance(answered_raw, list) else []
                )
                changed = cast(list[object], changed_raw) if isinstance(changed_raw, list) else []
                body = (
                    f"Feature review round completed for {url}. "
                    f"Answered: {', '.join(map(str, answered)) if answered else 'none'}. "
                    f"Changed: {', '.join(map(str, changed)) if changed else 'none'}. "
                    "acceptance was not re-run."
                )
                return Outcome(
                    "pending-human-review",
                    f"review-complete:{latest.id}",
                    _with_host_note(body, latest.result),
                )
            return Outcome("pending-human-review", event_body=_pr_message(latest.result))
        if outcome == "failed":
            result = latest.result
            event_key = "handoff"
            if latest.reason == "review" and self.kind == "feature":
                result = {**result, "pr": claim.outcome.get("pr")}
                event_key = f"review-failed:{latest.id}"
            return Outcome(
                "failed",
                event_key=event_key,
                event_body=_failed_message(result, self.definition.noun, claim.repository),
            )
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
            if card_status(self._shared, card) != "Ready":
                return None
            if self.definition.reconcile is ReconcilePolicy.RESUME_FROM_OWN_BRANCH:
                if claim.outcome.get("verdict") not in {"failed", "infra-error"}:
                    return None
                if self._github is None:
                    return None
                try:
                    open_pulls = self._github.list_open_factory_pull_requests_for_issue(
                        claim.repository, claim.issue_number
                    )
                except (GitHubApiError, OSError) as error:
                    if self._store is not None:
                        self._store.record_event(
                            claim.id,
                            f"open-pr-lookup:{error}",
                            f"Cannot check open pull requests before retry: {error}",
                        )
                    return None
                if open_pulls:
                    if self._store is not None:
                        self._store.record_event(
                            claim.id,
                            "open-pr-retry",
                            "This feature already has an open factory pull request. "
                            "Comment on that pull request to continue it through a review round.",
                        )
                    return None
            return "fresh"
        if claim.lifecycle == "blocked":
            if claim.outcome.get("blocked_by") == "review":
                return None
            if comments or card_status(self._shared, card) == "Ready":
                return "unblock"
            return None
        return None

    def limits(self, local: LocalConfig) -> SupervisionLimits:
        settings = self.definition.local(local)
        return SupervisionLimits(
            settings.limits.inactivity_seconds,
            settings.limits.execution_seconds,
            settings.limits.total_seconds,
        )

    def window(self, local: LocalConfig) -> ScheduleConfig:
        schedule = self.definition.local(local).schedule
        if schedule is not None:
            return schedule
        return ScheduleConfig.always(local.schedule.timezone, local.schedule.poll_seconds)

    def providers(self, claim: Claim) -> set[str]:
        return providers_from_roles(mapping(claim.frozen_spec.get("roles")))

    def attempt_message(self, run: Run, stored_result: Mapping[str, object], *, stage: str) -> str:
        attempt = run.attempt_number + 1
        outcome = stored_result.get("outcome")
        if stage == "complete" and isinstance(outcome, str):
            fallback = mapping(stored_result.get("resume")).get("fallback")
            detail = (
                f" Resume point unavailable ({fallback}); this attempt started fresh."
                if self.kind == "feature" and isinstance(fallback, str)
                else ""
            )
            return _with_host_note(
                f"{self.definition.noun} attempt {attempt} finished with outcome "
                f"`{outcome}`.{detail}",
                stored_result,
            )
        reason = _technical_reason(stored_result)
        if stage == "retry":
            return _with_host_note(
                (
                    f"{self.definition.noun} attempt {attempt} failed technically ({reason}); "
                    "one recovery attempt "
                    "from fresh clones at the recorded commits follows."
                ),
                stored_result,
            )
        if stage == "exhausted":
            return _with_host_note(
                f"{self.definition.noun} attempt {attempt} failed technically ({reason}); "
                "the recovery attempt is "
                f"used up, so this {self.definition.item_noun} is handed back with `infra-error`.",
                stored_result,
            )
        return f"{self.definition.noun} attempt {attempt} settled."

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
            f"{self.definition.noun} inputs frozen for this claim:",
            "",
            f"- target: {target.get('repository')} branch `{target.get('branch')}` "
            f"at {commit('target')}",
            f"- Agent Runner: branch `{branches.get('runner', 'main')}` at {commit('runner')}",
            f"- Agent Skills: branch `{branches.get('skills', 'main')}` at {commit('skills')}",
            f"- {self.kind} branch: `{self.branch_name(claim)}`",
            "- roles: " + ", ".join(f"{role}={profile}" for role, profile in roles.items()),
        ]
        if self.kind == "feature":
            lines.append("- attempt 1 starts fresh")
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


def _feature_stop_message(result: Mapping[str, object], repository: str) -> str:
    questions = result.get("questions")
    lines = ["Needs input."]
    if isinstance(questions, list):
        lines.extend(f"- {question}" for question in cast(list[object], questions))
    summary = result.get("direction_summary")
    if isinstance(summary, str):
        lines.append(f"Direction drafted so far: {summary}")
    branch = result.get("branch")
    if result.get("stopped_step") == "preflight":
        lines.append("No branch was created; the next attempt starts fresh.")
    elif isinstance(branch, str):
        lines.append(f"Branch: https://github.com/{repository}/tree/{branch}")
    return "\n\n".join(lines)


def _with_host_note(body: str, result: Mapping[str, object]) -> str:
    """Outcome comments for host-mode runs say so; Docker-mode comments are unchanged."""
    if result.get("sandbox") == "host":
        return f"{body}\n\n{launch.HOST_NOTE}"
    return body


def _pr_message(result: Mapping[str, object]) -> str:
    pr = mapping(result.get("pr"))
    url = pr.get("url")
    body = f"Pull request opened: {url}" if isinstance(url, str) else "Pull request opened."
    counts = mapping(result.get("review_attention_counts"))
    if all(isinstance(counts.get(tier), int) for tier in ("red", "orange", "yellow")):
        body += (
            f"\n\n{counts['red']} red flags, {counts['orange']} orange flags, "
            f"{counts['yellow']} yellow items."
        )
    return _with_host_note(body, result)


def _failed_message(result: Mapping[str, object], noun: str, repository: str = "") -> str:
    reasons = _reasons_text(result)
    pr = mapping(result.get("pr"))
    url = pr.get("url")
    lines = [f"{noun} attempt failed."]
    if reasons:
        lines.append(reasons)
    if isinstance(url, str):
        lines.append(f"Pull request: {url}")
    elif repository and isinstance(result.get("branch"), str):
        lines.append(f"Branch: https://github.com/{repository}/tree/{result['branch']}")
    return _with_host_note("\n\n".join(lines), result)
