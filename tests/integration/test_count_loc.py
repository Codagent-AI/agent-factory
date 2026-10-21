"""INT-001 through INT-005: real Git, published heads, and stubbed GitHub writes."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_feature_workflow_scripts import PACKAGE, git, repository

SCRIPT = str(PACKAGE / "count-loc.py")


def commit(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def published(tmp_path: Path) -> tuple[Path, Path, str, str]:
    repo, remote = repository(tmp_path)
    (repo / "sample.py").write_text("one\nsame\nsame\nsame\nfive\n")
    (repo / "deleted.py").write_text("old\n" * 7)
    (repo / "test_sample.py").write_text("old\n")
    commit(repo, "base")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "-b", "feature")
    (repo / "new.py").write_text("new\n" * 20)
    (repo / "deleted.py").unlink()
    (repo / "test_sample.py").write_text("new\nextra\n")
    (repo / "sample.py").write_text("changed\nsame\nsame\nsame\nchanged\n")
    (repo / "binary.py").write_bytes(b"a\0b\n")
    (repo / "openspec/ignored.py").write_text("excluded\n")
    commit(repo, "feature")
    git(repo, "checkout", "main")
    (repo / "target.py").write_text("target\n" * 400)
    base = commit(repo, "target moved")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "feature")
    git(repo, "merge", "--no-ff", "-m", "merge target", "main")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", "feature")
    (repo / "unpushed.py").write_text("local\n" * 500)
    commit(repo, "local only")
    return repo, remote, base, head


def invoke(
    repo: Path, *args: str, env: dict[str, str] | None = None, payload: dict[str, Any] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=repo,
        env=env,
        input=json.dumps(payload) if payload else None,
        text=True,
        capture_output=True,
    )


def assert_counts(section: str, base: str, head: str) -> None:
    assert "| code | 3 | 20 | 2 | 7 | +22 −9 |" in section
    assert "| tests | 1 | 1 | 1 | 0 | +2 −1 |" in section
    assert f"Measured `{base[:12]}..{head[:12]}`" in section


def test_published_head_merge_base_and_conflicting_settings(
    published: tuple[Path, Path, str, str],
    tmp_path: Path,
) -> None:
    repo, remote, base, head = published
    baseline = invoke(repo, "section", "--base-ref", "main", "--head", head)
    assert baseline.returncode == 0, baseline.stderr
    assert_counts(baseline.stdout, base, head)
    git(repo, "config", "diff.interHunkContext", "5")
    git(repo, "config", "diff.algorithm", "patience")
    git(repo, "config", "diff.indentHeuristic", "false")
    git(repo, "config", "diff.custom.algorithm", "histogram")
    git(repo, "config", "diff.custom.command", "false")
    global_config = tmp_path / "global-config"
    global_config.write_text("[diff]\ninterHunkContext = 5\nalgorithm = histogram\n")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(global_config)}
    for attributes in ("sample.py diff=custom\n", "sample.py -diff\n", "sample.py binary\n"):
        (repo / ".gitattributes").write_text(attributes)
        (repo / ".git/info/attributes").write_text(attributes)
        result = invoke(repo, "section", "--base-ref", "main", "--head", head, env=env)
        assert result.stdout == baseline.stdout, result.stderr
    # Fetch must retain global transport/authentication settings even though diff ignores them.
    global_config.write_text(
        global_config.read_text() + f'[url "{remote}"]\ninsteadOf = /not-a-real-origin\n'
    )
    git(repo, "remote", "set-url", "origin", "/not-a-real-origin")
    assert (
        invoke(repo, "section", "--base-ref", "main", "--head", head, env=env).stdout
        == baseline.stdout
    )
    git(repo, "remote", "set-url", "origin", str(remote))
    git(repo, "checkout", "-b", "attributes", head)
    new_head = commit(repo, "committed attributes")
    git(repo, "push", "origin", "attributes")
    result = invoke(repo, "section", "--base-ref", "main", "--head", new_head)
    # .gitattributes itself is code under the specified initial patterns.
    assert "| code | 4 | 21 | 2 | 7 | +23 −9 |" in result.stdout
    assert "| tests | 1 | 1 | 1 | 0 | +2 −1 |" in result.stdout


def test_missing_head_is_fetched_and_missing_base_is_unavailable(
    published: tuple[Path, Path, str, str],
    tmp_path: Path,
) -> None:
    _, remote, base, head = published
    clone = tmp_path / "fresh"
    git(
        tmp_path,
        "clone",
        "--no-local",
        "--single-branch",
        "--branch",
        "main",
        str(remote),
        str(clone),
    )
    assert subprocess.run(
        ["git", "cat-file", "-e", head], cwd=clone, capture_output=True
    ).returncode
    result = invoke(clone, "section", "--base-ref", "main", "--head", head)
    assert result.returncode == 0
    assert_counts(result.stdout, base, head)
    missing = invoke(clone, "section", "--base-ref", "nonexistent", "--head", head)
    assert (
        missing.returncode == 0 and "Lines-of-code count unavailable: git fetch" in missing.stdout
    )
    assert "<!-- agent-factory:loc-end -->" in missing.stdout


GH_STUB = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
state = Path(os.environ['GH_STATE'])
if args[:2] == ['pr', 'list']:
    print('[]' if os.environ.get('GH_NO_PR') else '[{"number": 9}]')
elif 'PATCH' in args:
    with open(os.environ['GH_PATCHES'], 'a') as stream:
        stream.write('PATCH\\n')
    if os.environ.get('GH_FAIL_PATCH'):
        print('HTTP 403: denied', file=sys.stderr)
        sys.exit(1)
    body_file = next(a[6:] for a in args if a.startswith('body=@'))
    data = json.loads(state.read_text())
    data['body'] = Path(body_file).read_bytes().decode()
    state.write_text(json.dumps(data))
else:
    if os.environ.get('GH_FAIL_READ'):
        print('HTTP 503: read failed', file=sys.stderr)
        sys.exit(1)
    data = json.loads(state.read_text())
    print(json.dumps({'body': data['body']}) if args[:2] == ['pr', 'view'] else json.dumps(data))
"""


def github(tmp_path: Path, head: str, body: str) -> tuple[dict[str, str], Path, Path]:
    stub = tmp_path / "bin/gh"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"body": body, "base": {"ref": "main"}, "head": {"sha": head}}))
    patches = tmp_path / "patches"
    return (
        {
            **os.environ,
            "PATH": f"{stub.parent}:{os.environ['PATH']}",
            "GH_STATE": str(state),
            "GH_PATCHES": str(patches),
        },
        state,
        patches,
    )


def test_refresh_insert_replace_duplicates_unchanged_and_rewrite(
    published: tuple[Path, Path, str, str],
    tmp_path: Path,
) -> None:
    repo, _, base, head = published
    original = "Refs #2\r\n<!-- agent-factory:claim:x -->\r\n\r\nHuman text  \r\n"
    env, state, patches = github(tmp_path, head, original)
    artifacts = tmp_path / "artifacts"
    result = invoke(repo, env=env, payload={"mode": "refresh-pr", "artifact_dir": str(artifacts)})
    assert result.returncode == 0, result.stderr
    wanted = json.loads(state.read_text())["body"]
    assert_counts(wanted, base, head)
    assert wanted.startswith("Refs #2\r\n<!-- agent-factory:claim:x -->\r\n")
    assert wanted.endswith("\r\nHuman text  \r\n")
    assert (artifacts / "pr-body-loc.md").read_bytes() == wanted.encode()
    unchanged = invoke(
        repo, "refresh-pr", "--number", "9", "--artifact-dir", str(artifacts), env=env
    )
    assert unchanged.returncode == 0 and patches.read_text() == "PATCH\n"
    data = json.loads(state.read_text())
    start = "<!-- agent-factory:loc -->"
    end = "<!-- agent-factory:loc-end -->"
    for body in (original, f"prefix\n{start}\nstale\n{end}\nmiddle\n{start}\nstale\n{end}\nsuffix"):
        data["body"] = body
        state.write_text(json.dumps(data))
        refreshed = invoke(
            repo, "refresh-pr", "--number", "9", "--artifact-dir", str(artifacts), env=env
        )
        assert refreshed.returncode == 0, refreshed.stderr
        updated = json.loads(state.read_text())["body"]
        assert updated.count(start) == 1 and "stale" not in updated
        assert_counts(updated, base, head)
    data["head"]["sha"] = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", "feature")
    state.write_text(json.dumps(data))
    assert (
        invoke(
            repo, "refresh-pr", "--number", "9", "--artifact-dir", str(artifacts), env=env
        ).returncode
        == 0
    )
    assert data["head"]["sha"][:12] in json.loads(state.read_text())["body"]


def test_no_pr_read_failure_patch_retries_and_count_failure(
    published: tuple[Path, Path, str, str],
    tmp_path: Path,
) -> None:
    repo, _, _, head = published
    env, state, patches = github(tmp_path, head, "body")
    assert invoke(repo, "refresh-pr", env={**env, "GH_NO_PR": "1"}).returncode == 0
    assert not patches.exists()
    assert invoke(repo, "refresh-pr", env={**env, "GH_FAIL_READ": "1"}).returncode == 1
    result = invoke(
        repo, "refresh-pr", "--artifact-dir", str(tmp_path), env={**env, "GH_FAIL_PATCH": "1"}
    )
    assert result.returncode == 1 and patches.read_text() == "PATCH\n" * 3
    data = json.loads(state.read_text())
    data["base"]["ref"] = "missing"
    state.write_text(json.dumps(data))
    assert invoke(repo, "refresh-pr", "--artifact-dir", str(tmp_path), env=env).returncode == 0
    assert "count unavailable" in json.loads(state.read_text())["body"]
    local_body = tmp_path / "local.md"
    local_body.write_text("feature body\n")
    result = invoke(
        repo, "refresh-file", str(local_body), "--number", "9", env={**env, "GH_FAIL_READ": "1"}
    )
    assert result.returncode == 0 and "HTTP 503" in local_body.read_text()


@pytest.mark.parametrize("kind", ["fix", "feature", "task"])
@pytest.mark.parametrize("saved", ["present", "failed", "empty"])
def test_review_round_refresh_by_kind_and_saved_body(
    published: tuple[Path, Path, str, str],
    tmp_path: Path,
    kind: str,
    saved: str,
) -> None:
    repo, _, base, head = published
    examples = (
        "Inline `<!-- agent-factory:loc -->`\n```md\n<!-- agent-factory:loc -->\n"
        "example\n<!-- agent-factory:loc-end -->\n```\n"
    )
    original = "## Change summary\n\nArtifact links\n\n" + examples
    env, state, patches = github(tmp_path, head, "" if saved == "empty" else original)
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"kind": kind, "head_sha": base, "pull_request": {"number": 9}}))

    def describe(
        mode: str, extra: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(PACKAGE / "review-description.sh")],
            cwd=repo,
            env={**env, **(extra or {})},
            text=True,
            capture_output=True,
            input=json.dumps(
                {"mode": mode, "review_file": str(review), "artifact_dir": str(tmp_path)}
            ),
        )

    result = describe("save", {"GH_FAIL_READ": "1"} if saved == "failed" else None)
    assert (result.returncode != 0) == (saved == "failed" and kind == "feature")
    rewritten = "finalization rewrite\n" + examples
    data = json.loads(state.read_text())
    data["body"] = rewritten
    state.write_text(json.dumps(data))
    result = describe("restore")
    assert result.returncode == 0, result.stderr
    updated = json.loads(state.read_text())["body"]
    if kind == "task":
        assert updated == rewritten and not patches.exists()
    else:
        assert_counts(updated, base, head)
        assert examples in updated
        assert updated.count("<!-- agent-factory:loc -->") == 3  # inline, fenced, live
        if kind == "feature" and saved == "present":
            assert "Review round after" in updated and "Artifact links" in updated
            assert "finalization rewrite" not in updated
        else:
            assert updated.startswith(rewritten)


@pytest.mark.parametrize("kind", ["fix", "feature"])
@pytest.mark.parametrize("saved", [False, True])
def test_review_refresh_failure_is_recorded(
    published: tuple[Path, Path, str, str],
    tmp_path: Path,
    kind: str,
    saved: bool,
) -> None:
    repo, _, _, head = published
    env, _, patches = github(tmp_path, head, "body\n")
    if saved:
        (tmp_path / "pr-description-before-review.md").write_text("saved body\n")
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"kind": kind, "pull_request": {"number": 9}}))
    result = subprocess.run(
        [str(PACKAGE / "review-description.sh")],
        cwd=repo,
        env={**env, "GH_FAIL_PATCH": "1"},
        text=True,
        capture_output=True,
        input=json.dumps(
            {"mode": "restore", "review_file": str(review), "artifact_dir": str(tmp_path)}
        ),
    )
    assert result.returncode != 0
    assert "HTTP 403" in (tmp_path / "description-restore-failed").read_text()
    assert patches.is_file()


def test_real_rename_modes_and_binary_conversion(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    (repo / "old.py").write_text("one\ntwo\n")
    (repo / "mode.py").write_text("unchanged\n")
    (repo / "was_binary.py").write_bytes(b"old\0binary\n")
    (repo / "now_binary.py").write_text("old text\n")
    commit(repo, "base files")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "-b", "feature")
    (repo / "old.py").rename(repo / "new.py")
    (repo / "mode.py").chmod(0o755)
    (repo / "was_binary.py").write_text("now text\n")
    (repo / "now_binary.py").write_bytes(b"now\0binary\n")
    head = commit(repo, "rename, mode, and binary transitions")
    git(repo, "push", "origin", "feature")
    result = invoke(repo, "section", "--base-ref", "main", "--head", head)
    assert result.returncode == 0
    assert "| code | 0 | 0 | 0 | 0 | +0 −0 |" in result.stdout


@pytest.mark.parametrize(("position", "files"), [(7999, 0), (8000, 1)])
def test_binary_detection_reads_first_8000_bytes_only(
    tmp_path: Path,
    position: int,
    files: int,
) -> None:
    repo, _ = repository(tmp_path)
    git(repo, "checkout", "-b", "feature")
    (repo / "sample.py").write_bytes(b"x" * position + b"\0\n")
    head = commit(repo, "NUL boundary")
    git(repo, "push", "origin", "feature")
    result = invoke(repo, "section", "--base-ref", "main", "--head", head)
    assert f"| code | {files} | {files} | 0 | 0 | +{files} −0 |" in result.stdout


@pytest.mark.parametrize("blob_error", [False, True])
def test_blob_prefix_streaming_and_failed_reads(tmp_path: Path, blob_error: bool) -> None:
    """A producer that never finishes must be stopped after its 8000-byte prefix."""
    stub = tmp_path / "git"
    stub.write_text(
        """#!/usr/bin/env python3
import os, sys, time
args = sys.argv[1:]
if args[:2] == ['cat-file', 'blob']:
    if os.environ.get('BLOB_ERROR'):
        print('fatal: missing blob', file=sys.stderr)
        sys.exit(1)
    sys.stdout.buffer.write(b'x' * 8000)
    sys.stdout.buffer.flush()
    time.sleep(60)
elif args[0] == 'merge-base':
    print('a' * 40)
elif 'diff' in args:
    print('diff --git a/new.py b/new.py')
    print('index ' + '0' * 40 + '..' + 'b' * 40 + ' 100644')
    print('--- /dev/null\\n+++ b/new.py\\n@@ -0,0 +1 @@\\n+source')
"""
    )
    stub.chmod(0o755)
    result = subprocess.run(
        [sys.executable, SCRIPT, "section", "--base-ref", "main", "--head", "b" * 40],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "BLOB_ERROR": "1" if blob_error else "",
        },
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    if blob_error:
        assert "Lines-of-code count unavailable: git cat-file blob" in result.stdout
        assert "fatal: missing blob" in result.stdout
        assert "| code |" not in result.stdout
    else:
        assert "| code | 1 | 1 | 0 | 0 | +1 −0 |" in result.stdout


@pytest.mark.parametrize(
    ("setting", "value"),
    [("diff.submodule", "log"), ("diff.submodule", "diff"), ("diff.ignoreSubmodules", "all")],
)
def test_gitlink_count_ignores_local_diff_settings(
    tmp_path: Path, setting: str, value: str
) -> None:
    repo, _ = repository(tmp_path)
    first = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{first},vendor")
    git(repo, "commit", "-m", "base gitlink")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "-b", "feature")
    second = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--cacheinfo", f"160000,{second},vendor")
    git(repo, "commit", "-m", "advance gitlink")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", "feature")
    baseline = invoke(repo, "section", "--base-ref", "main", "--head", head)
    assert baseline.returncode == 0, baseline.stderr
    assert "| code | 0 | 0 | 0 | 0 | +0 −0 |" in baseline.stdout
    git(repo, "config", setting, value)
    result = invoke(repo, "section", "--base-ref", "main", "--head", head)
    assert result.returncode == 0, result.stderr
    assert result.stdout == baseline.stdout


def test_credential_prompt_never_blocks_the_count(
    published: tuple[Path, Path, str, str],
) -> None:
    """An origin that asks for credentials fails fast instead of waiting for a prompt."""
    import http.server
    import threading

    class Unauthorized(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="git"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Unauthorized)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    repo, _, _, head = published
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/repo.git"
        git(repo, "remote", "set-url", "origin", url)
        result = subprocess.run(
            [sys.executable, SCRIPT, "section", "--base-ref", "main", "--head", head],
            cwd=repo,
            env={**os.environ, "GIT_ASKPASS": "", "SSH_ASKPASS": ""},
            text=True,
            capture_output=True,
            timeout=60,
        )
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stderr
    assert "Lines-of-code count unavailable: git fetch" in result.stdout
    assert "<!-- agent-factory:loc-end -->" in result.stdout


def test_unresponsive_remote_times_out_as_unavailable(
    published: tuple[Path, Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.test_count_loc import loc

    repo, _, _, head = published
    git(repo, "config", "remote.origin.uploadpack", "sleep 30; git-upload-pack")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(loc, "COMMAND_TIMEOUT", 1.0)
    started = time.monotonic()
    result = loc.measure("main", head)
    # The upload-pack shell's `sleep 30` is killed with fetch, not waited for.
    assert time.monotonic() - started < 10
    assert isinstance(result, loc.Unavailable)
    assert "timed out after 1s" in result.reason


@pytest.mark.parametrize("hang", [False, True])
def test_blob_reads_are_bounded_and_non_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hang: bool
) -> None:
    """A blob read that reaches the network (a lazy fetch in a partial clone) gets the same
    non-interactive environment and deadline as every other command."""
    from tests.unit.test_count_loc import loc

    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "git").write_text(
        "#!/bin/sh\n"
        + (
            "sleep 30 &\nwait\n"
            if hang
            else 'printf "%s%s" "$GIT_TERMINAL_PROMPT" "$GCM_INTERACTIVE"\n'
        )
    )
    (fake / "git").chmod(0o755)
    monkeypatch.setattr(loc, "COMMAND_TIMEOUT", 1.0)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}
    env.pop("GIT_TERMINAL_PROMPT", None)
    started = time.monotonic()
    if hang:
        with pytest.raises(RuntimeError, match="timed out after 1s"):
            loc.blob_prefix("a" * 40, env)
        assert time.monotonic() - started < 10
    else:
        assert loc.blob_prefix("a" * 40, env) == b"0never"


def test_breakdown_total_matches_rename_aware_numstat(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    original = "".join(f"source line {i}\n" for i in range(30))
    (repo / "source.py").write_text(original)
    change = repo / "openspec/changes/x/tasks.md"
    change.parent.mkdir(parents=True)
    change.write_text("pure rename\n")
    commit(repo, "base for renames")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "-b", "feature")
    renamed = repo / "tests/test_source.py"
    renamed.parent.mkdir()
    (repo / "source.py").rename(renamed)
    renamed.write_text(original.replace("source line 15", "edited line 15"))
    archive = repo / "openspec/changes/archive/x/tasks.md"
    archive.parent.mkdir(parents=True)
    change.rename(archive)
    for name in (
        "new.py",
        "openspec/changes/archive/y/tasks.md",
        "openspec/specs/x/spec.md",
        "docs/x.md",
        "config/codagent.toml",
    ):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"new {name}\n")
    (repo / "binary.py").write_bytes(b"binary\0content\n")
    head = commit(repo, "changes and renames")
    git(repo, "push", "origin", "feature")
    base = git(repo, "merge-base", "origin/main", head)
    numstat = git(
        repo,
        "diff",
        "--numstat",
        "-M",
        "-l0",
        "--diff-algorithm=myers",
        "--indent-heuristic",
        "--no-ext-diff",
        "--no-textconv",
        base,
        head,
    )
    added = deleted = files = 0
    for line in numstat.splitlines():
        plus, minus, _ = line.split("\t", 2)
        if plus != "-":
            added += int(plus)
            deleted += int(minus)
            files += bool(int(plus) or int(minus))
    result = invoke(repo, "section", "--base-ref", "main", "--head", head)
    assert result.returncode == 0, result.stderr
    assert "| tests | 1 | 0 | 1 | 0 | +1 −1 |" in result.stdout
    assert "| OpenSpec changes | 1 | +1 −0 |" in result.stdout
    assert f"| total | {files} | +{added} −{deleted} |" in result.stdout


def test_repository_rename_limit_does_not_change_counts(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    for i in range(3):
        (repo / f"source{i}.py").write_text("".join(f"file {i} line {j}\n" for j in range(30)))
    commit(repo, "rename sources")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "-b", "feature")
    for i in range(3):
        old = repo / f"source{i}.py"
        new = repo / f"destination{i}.py"
        old.rename(new)
        new.write_text(new.read_text().replace(f"file {i} line 15", f"edited {i} line 15"))
    head = commit(repo, "edit and rename three files")
    git(repo, "push", "origin", "feature")
    baseline = invoke(repo, "section", "--base-ref", "main", "--head", head)
    assert "| code | 3 | 0 | 3 | 0 | +3 −3 |" in baseline.stdout
    git(repo, "config", "diff.renameLimit", "1")
    limited = invoke(repo, "section", "--base-ref", "main", "--head", head)
    assert limited.stdout == baseline.stdout
