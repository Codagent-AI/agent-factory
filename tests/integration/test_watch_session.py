"""INT-003: a watcher launches in a throwaway mirror clone with a private brief."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from agent_factory import audit
from agent_factory.config import (
    CredentialsConfig,
    LimitsConfig,
    LocalConfig,
    RepositoryConfig,
    ScheduleConfig,
    SharedConfig,
    WatchConfig,
)
from agent_factory.github import InstallationTokenProvider
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.watch import session
from agent_factory.watch import store as watch_store
from agent_factory.work_kinds.pull_request.workspace import PullRequestWorkspace


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


@pytest.mark.parametrize(
    ("audit_enabled", "event_kind"),
    [(False, "FAILURE"), (True, "FAILURE"), (False, "PR-READY")],
)
def test_session_stages_and_launches_with_no_token_in_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_enabled: bool, event_kind: str
) -> None:
    monkeypatch.setattr(audit, "AUDIT_ENABLED", audit_enabled)
    # All git and Runner effects stay under this isolated root.
    source = tmp_path / "source"
    _git("init", "-b", "main", str(source))
    (source / "README.md").write_text("watch fixture\n")
    _git("-C", str(source), "add", "README.md")
    _git(
        "-C",
        str(source),
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    mirror = tmp_path / "mirrors" / "o__r.git"
    mirror.parent.mkdir()
    _git("clone", "--bare", str(source), str(mirror))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runner = bin_dir / "agent-runner"
    runner.write_text("#!/bin/sh\nexit 1\n")
    runner.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GH_TOKEN", "must-not-pass")
    local = LocalConfig(
        tmp_path / "shared.toml",
        tmp_path,
        RepositoryConfig(tmp_path, tmp_path, tmp_path),
        ScheduleConfig.always(ZoneInfo("UTC"), 60),
        LimitsConfig(0, 1, 1, 1, 1),
        CredentialsConfig(tmp_path, tmp_path),
    )
    shared = replace(
        SharedConfig.from_file(Path("config/codagent.toml")),
        watch=WatchConfig(True, "o/r", "claude:model:medium"),
    )

    # The local bare fixture already represents a fetched mirror.
    fetches: list[str] = []

    def fetched(self: PullRequestWorkspace, repository: str, token: str | None) -> None:
        fetches.append(repository)

    monkeypatch.setattr(PullRequestWorkspace, "fetch_mirror", fetched)
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "eval", "fp", {}))
        now = datetime.now(UTC)
        pr = event_kind == "PR-READY"
        watch_store.insert(
            store,
            event_key=f"{event_kind}:r",
            event_kind=event_kind,
            claim_id=claim.id,
            run_id=None,
            repository="o/product" if pr else "o/r",
            issue_number=1,
            pr_number=5 if pr else None,
            pr_url="https://github.com/o/product/pull/5" if pr else None,
            event_at=now.isoformat(),
            now=now.isoformat(),
        )
        row = watch_store.rows(store)[0]
        evidence, clone = session.paths(local, row["id"])
        assert watch_store.claim_launch(
            store, row["id"], now, 90, "claude:model:medium", str(evidence)
        )
        launched = watch_store.get(store, row["id"])
        assert launched is not None
        identity = session.start(
            store,
            launched,
            local,
            shared,
            tmp_path / "local.toml",
            cast(InstallationTokenProvider, lambda: "unused"),
        )
        assert isinstance(identity.get("pid"), int)
        brief = json.loads((evidence / "input" / "brief.json").read_text())
        assert brief["procedure"] == ("pr-check" if pr else "triage")
        assert fetches == ["o/r"]
        assert "pr_source" not in brief["paths"]
        assert "operator_login" not in brief
        assert brief["fix_targets"] == [target.repository for target in shared.fix.targets]
        forbidden = " ".join(brief["forbidden"])
        assert "commit, push, or open pull requests" in forbidden
        assert ("post a review or comment on the pull request" in forbidden) is pr
        assert Path(str(brief["paths"]["scratch"])).is_dir()
        assert "allowed_environment" not in json.dumps(brief)
        wrapper = (evidence / "private" / "watch-run.sh").read_text()
        assert wrapper.splitlines()[1].startswith("echo $$ > ")
        assert "must-not-pass" not in wrapper
        assert ("-m agent_factory.audit host" in wrapper) is audit_enabled
        assert (clone / ".agent-runner" / "workflows" / "factory-watch-v3.0.yaml").is_file()
        for _ in range(100):
            if (evidence / "exit.json").is_file():
                break
            time.sleep(0.05)
        assert (evidence / "exit.json").is_file()
    finally:
        store.close()
