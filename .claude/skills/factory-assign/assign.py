"""Check, and with --apply set, what the live Agent Factory needs to admit one issue.

Run it with the live release's interpreter, so the check is the code the service runs:

    ~/.agent-factory/releases/current/.venv/bin/python assign.py OWNER/REPO NUMBER
    ~/.agent-factory/releases/current/.venv/bin/python assign.py OWNER/REPO NUMBER --apply fix

Board reads and writes use the Factory App token; issue type, Priority and labels use
Paul's gh login. Tokens stay in process memory and are never printed.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from agent_factory import work_kinds
from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.github import (
    WRITER_PERMISSIONS,
    AppCredentials,
    GitHubApiError,
    GitHubClient,
    InstallationTokenProvider,
    ProjectQueueItem,
    SubprocessGhRunner,
)
from agent_factory.routing import SourceItem
from agent_factory.work_kinds.base import Feedback, card_status
from agent_factory.work_kinds.eval import parse_request


def paul_gh(*arguments: str) -> str:
    environment = {k: v for k, v in os.environ.items() if k not in {"GH_TOKEN", "GITHUB_TOKEN"}}
    completed = subprocess.run(
        ["gh", *arguments], env=environment, text=True, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise GitHubApiError(f"gh {arguments[0]} failed: {completed.stderr.strip()[:300]}")
    return completed.stdout.strip()


class Factory:
    def __init__(self) -> None:
        path = os.environ.get("AGENT_FACTORY_CONFIG", "~/.agent-factory/config.toml")
        self.local = LocalConfig.from_file(Path(path).expanduser())
        self.shared = SharedConfig.from_file(self.local.shared_config)
        app = AppCredentials(
            self.shared.app_id, self.shared.installation_id, self.local.credentials.github_app_key
        )
        self.app = GitHubClient(SubprocessGhRunner(), InstallationTokenProvider(app))
        self.paul = GitHubClient(SubprocessGhRunner(), lambda: paul_gh("auth", "token"))
        self.handlers = work_kinds.handlers(self.shared, self.local)
        self.targets = {target.repository for target in self.shared.fix.targets}

    def cards(self) -> list[ProjectQueueItem]:
        project = self.shared.project
        return self.app.list_project_items(project.id, priority_id=project.priority_id)

    def kind_of(self, source: SourceItem) -> str | None:
        routing = self.shared.routing
        if source.repository == routing.eval_source and source.issue_type == routing.eval_type:
            return "eval"
        if source.repository in self.targets and source.issue_type == routing.bug_type:
            return "fix"
        return None

    def wanted_type(self, kind: str) -> str:
        return self.shared.routing.eval_type if kind == "eval" else self.shared.routing.bug_type

    def eval_problem(self, source: SourceItem) -> str | None:
        handler = self.handlers["eval"]
        try:
            parse_request(source.body, handler._defaults)  # the defaults admission parses with
        except ValueError as error:
            return str(error)
        return None


def mark(ok: bool) -> str:
    return "ok" if ok else "MISSING"


def report(factory: Factory, repository: str, number: int, kind: str | None) -> bool:
    """Print every admission requirement; True when admissible or already claimed."""
    shared = factory.shared
    cards = factory.cards()
    card = next(
        (c for c in cards if c.source.repository == repository and c.source.number == number),
        None,
    )
    detail = factory.app.get_source_item(repository, number)
    source = card.source if card else detail
    kind = kind or factory.kind_of(source)
    print(f"issue: https://github.com/{repository}/issues/{number} {detail.title!r}")
    print(f"kind: {kind or 'unknown (type is ' + str(source.issue_type) + ')'}")
    print(f"state: {source.state} [{mark(source.state.lower() == 'open')}]")
    is_target = repository in factory.targets
    is_eval_source = repository == shared.routing.eval_source
    if kind is None:
        print(f"repository: fix target {is_target}, eval source {is_eval_source}")
    else:
        in_scope = is_eval_source if kind == "eval" else is_target
        print(f"repository takes {kind} work: [{mark(in_scope)}]")
    wanted = factory.wanted_type(kind) if kind else None
    print(f"type: {source.issue_type} [{mark(wanted is not None and source.issue_type == wanted)}]")
    print(f"labels: {', '.join(sorted(source.labels)) or '(none)'}")
    if kind == "fix":
        print(f"needs-input label absent: [{mark('needs-input' not in source.labels)}]")
    if kind == "eval":
        label = shared.routing.eval_label
        print(f"{label} label (routing signal, not an admission gate): {label in source.labels}")
        problem = factory.eval_problem(source)
        print(f"request body: {problem or 'valid'} [{mark(problem is None)}]")
    permission = factory.app.get_permission(repository, source.author)
    print(f"author: {source.author} ({permission}) [{mark(permission in WRITER_PERMISSIONS)}]")
    print(f"priority: {card.priority if card else 'unknown (no board item)'}")
    if card is None:
        # The queue listing leaves out some cards, for example untyped issues.
        item = factory.app.find_project_item(shared.project.id, detail.id)
        if item is None:
            print("board item: none [MISSING]")
        else:
            owner = item.fields.get(shared.project.owner.id)
            status = item.fields.get(shared.project.status.id)
            print(f"board item: {item.id} (not in the factory's queue) [MISSING]")
            print(f"status is Ready: [{mark(status == shared.project.status.option('ready'))}]")
            print(f"owner is factory: [{mark(owner == shared.project.owner.option('factory'))}]")
    else:
        owner = card.fields.get(shared.project.owner.id)
        owner_name = next((k for k, v in shared.project.owner.options.items() if v == owner), None)
        status = card_status(shared, card)
        print(f"board item: {card.id} [ok]")
        print(f"status: {status or '(none)'} [{mark(status == 'Ready')}]")
        print(f"owner: {owner_name or '(none)'} [{mark(owner_name == 'factory')}]")
        if kind is not None:
            ahead = [
                f"{c.source.repository}#{c.source.number}"
                for c in cards[: cards.index(card)]
                if c.source.state.lower() == "open"
                and card_status(shared, c) == "Ready"
                and c.fields.get(shared.project.owner.id) == shared.project.owner.option("factory")
                and factory.kind_of(c.source) == kind
            ]
            print(f"ranked ahead of it (Ready, factory, {kind}): {', '.join(ahead) or 'none'}")
    admissible = False
    if card is not None:
        for handler in factory.handlers.values():
            snapshot = handler.snapshot(card, factory.app, shared)
            if snapshot is None:
                continue
            fingerprint = handler.request_fingerprint(snapshot)
            if isinstance(fingerprint, Feedback):
                print(f"factory view: {handler.kind} request, invalid: {fingerprint.explanation}")
            else:
                admissible = True
                print(f"factory view: admissible {handler.kind} request")
            break
        else:
            print("factory view: not a request the factory admits")
    with sqlite3.connect(factory.local.state_path) as database:
        rows = database.execute(
            "SELECT id, kind, lifecycle, created_at FROM claim"
            " WHERE repository = ? AND issue_number = ? ORDER BY created_at",
            (repository, number),
        ).fetchall()
    for claim_id, claim_kind, lifecycle, created_at in rows:
        print(f"claim: {claim_id} {claim_kind} {lifecycle} created {created_at}")
    live = [row for row in rows if row[2] not in {"settled", "cancelled", "superseded"}]
    if live:
        print(f"result: already claimed {live[-1][0]} ({live[-1][2]})")
        return True
    print(f"result: {'admissible' if admissible else 'not admissible'}")
    return admissible


def apply(factory: Factory, repository: str, number: int, kind: str, retype: bool) -> None:
    shared = factory.shared
    source = factory.app.get_source_item(repository, number)
    scope = shared.routing.eval_source if kind == "eval" else None
    if (kind == "eval" and repository != scope) or (
        kind == "fix" and repository not in factory.targets
    ):
        sys.exit(f"refused: {repository} does not take {kind} work")
    if source.state.lower() != "open":
        sys.exit("refused: the issue is closed")
    if kind == "fix" and "needs-input" in source.labels:
        sys.exit("refused: needs-input is set; answer the factory's question first")
    if factory.app.get_permission(repository, source.author) not in WRITER_PERMISSIONS:
        sys.exit(f"refused: author {source.author} lacks write permission")
    if kind == "eval" and (problem := factory.eval_problem(source)):
        sys.exit(f"refused: the eval request is invalid: {problem}")
    with sqlite3.connect(factory.local.state_path) as database:
        live = database.execute(
            "SELECT id, lifecycle FROM claim WHERE repository = ? AND issue_number = ?"
            " AND lifecycle NOT IN ('settled', 'cancelled', 'superseded')",
            (repository, number),
        ).fetchone()
    if live is not None:
        # Moving a blocked claim's card to Ready is the gesture that resumes it.
        sys.exit(
            f"refused: claim {live[0]} is {live[1]}; move its card to Ready by hand "
            "only to resume that claim"
        )
    wanted = factory.wanted_type(kind)
    if source.issue_type != wanted:
        if source.issue_type is not None and not retype:
            sys.exit(f"refused: type is {source.issue_type}; rerun with --retype if confirmed")
        factory.paul.set_issue_type(repository, number, wanted)
        print(f"set type: {wanted}")
    label = shared.routing.eval_label
    if kind == "eval" and label not in source.labels:
        paul_gh("api", f"repos/{repository}/issues/{number}/labels", "-f", f"labels[]={label}")
        print(f"added label: {label}")
    project = shared.project
    if project.priority_issue_field_id and project.priority_default_option_id:
        # Sets the default (Low) only when Priority is empty, exactly as routing does.
        factory.paul.ensure_issue_select_default(
            source.id, project.priority_issue_field_id, project.priority_default_option_id
        )
    item = factory.app.find_project_item(project.id, source.id)
    if item is None:
        item = factory.app.add_project_item(project.id, source.id)
        print(f"added to board: {item.id}")
    for field, name in ((project.owner, "factory"), (project.status, "ready")):
        if item.fields.get(field.id) != field.option(name):
            factory.app.set_single_select_field(project.id, item.id, field.id, field.option(name))
            print(f"set {'Owner' if field is project.owner else 'Status'}: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repository", help="OWNER/REPO")
    parser.add_argument("number", type=int)
    parser.add_argument("--apply", choices=("fix", "eval"), help="set the missing fields")
    parser.add_argument("--retype", action="store_true", help="replace a different issue type")
    arguments = parser.parse_args()
    factory = Factory()
    try:
        if arguments.apply:
            apply(
                factory, arguments.repository, arguments.number, arguments.apply, arguments.retype
            )
            print("--- read back")
        admissible = report(factory, arguments.repository, arguments.number, arguments.apply)
    except GitHubApiError as error:
        sys.exit(f"GitHub request failed: {error}")
    sys.exit(0 if admissible else 1)


if __name__ == "__main__":
    main()
