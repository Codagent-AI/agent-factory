"""E2E-002: the fix journey through the CLI with a controlled sandbox and stub GitHub."""

# ruff: noqa: E501

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest

from agent_factory.config import SharedConfig
from agent_factory.store import ClaimStore, Run

REPOSITORY = "example/work"
FIX_TOKEN = "fix-token-value"


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _commit(path: Path, message: str) -> str:
    _git(path, "add", "-A")
    _git(
        path,
        "-c",
        "user.name=Factory Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        message,
    )
    return _git(path, "rev-parse", "HEAD")


def _repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        if name.endswith(".sh"):
            target.chmod(0o755)
    sha = _commit(path, "test fixture")
    remote = path.parent / (path.name + "-origin.git")
    subprocess.run(["git", "clone", "--quiet", "--bare", str(path), str(remote)], check=True)
    _git(path, "remote", "add", "origin", str(remote))
    _git(path, "fetch", "--quiet", "origin")
    return sha


SANDBOX = f"""#!{sys.executable}
# Controlled stand-in for scripts/sandbox-run.sh. Accepts the real flags:
# --image --artifact-dir --no-default-secrets --env-file --docker-run-arg --mount-*-auth
import json, os, pathlib, sys, time
args = sys.argv[1:]
out = pathlib.Path(args[args.index('--artifact-dir') + 1]); out.mkdir(parents=True, exist_ok=True)
(out / 'sandbox-args.json').write_text(json.dumps(args))
(out / 'env-names.json').write_text(json.dumps(sorted(os.environ)))
(out / 'env-file-copy.txt').write_text(pathlib.Path(args[args.index('--env-file') + 1]).read_text())
(out / 'cwd.txt').write_text(os.getcwd())
(out / 'started').touch()
while not (out / 'finish').exists():
    time.sleep(.02)
script = (out / 'finish').read_text()
if script.strip() == 'crash':
    sys.exit(3)
(out / 'fix-outcome.json').write_text(script)
"""

RUNNER = f"""#!{sys.executable}
# Controlled stand-in for the installed agent-runner in host mode. Answers the readiness
# probes and runs `factory-fix` the way the wrapper invokes it: --session-dir plus --param.
import json, os, pathlib, subprocess, sys, time
args = sys.argv[1:]
if args[:1] == ['-version']:
    print('stub-runner 1.0'); sys.exit(0)
if args[:1] == ['-validate']:
    sys.exit(0)
if args[:2] == ['run', '--help']:
    print('Usage: agent-runner run <workflow> [--session-dir <path>] [--param key=value]'); sys.exit(0)
session = None; params = {{}}
it = iter(range(len(args)))
for i in it:
    if args[i] == '--session-dir':
        session = args[i + 1]; next(it)
    elif args[i] == '--param':
        k, v = args[i + 1].split('=', 1); params[k] = v; next(it)
out = pathlib.Path(params['artifact_dir']); out.mkdir(parents=True, exist_ok=True)
sess = pathlib.Path(session); sess.mkdir(parents=True, exist_ok=True)
(out / 'runner-args.json').write_text(json.dumps(args))
(out / 'env-names.json').write_text(json.dumps(sorted(os.environ)))
(out / 'cwd.txt').write_text(os.getcwd())
(out / 'git-user.txt').write_text(subprocess.run(['git', 'config', '--get', 'user.name'], capture_output=True, text=True).stdout)
(out / 'started').touch()
while not (out / 'finish').exists():
    (sess / 'state.json').write_text(str(time.time()))
    time.sleep(.02)
script = (out / 'finish').read_text()
if script.strip() == 'crash':
    sys.exit(3)
(out / 'fix-outcome.json').write_text(script)
"""

CODEX = """#!/bin/sh
case "$1 $2" in
  "plugin list") echo '[{"name": "codagent"}]' ;;
esac
exit 0
"""

DOCKER = """#!/bin/sh
case "$1" in
  info) echo 17179869184 ;;
  stats) printf "" ;;
  ps) printf "" ;;
  inspect) echo "[]" ;;
  rmi) echo "$2" >> "$(dirname "$0")/../docker-rmi.log" ;;
esac
exit 0
"""


def _gh_stub(board: Path, bot_login: str) -> str:
    return f"""#!{sys.executable}
import json, os, re, sys, pathlib
p = pathlib.Path({str(board)!r}); s = json.loads(p.read_text()); args = sys.argv[1:]
if args[:2] == ['auth', 'status']:
    sys.exit(0 if os.environ.get('GH_TOKEN') else 1)
if args[:2] == ['api', 'user']:
    print('fixbot'); sys.exit(0)
body = json.load(sys.stdin) if '--input' in args else {{}}
command = args[0]; endpoint = args[1] if len(args) > 1 else ''
q = body.get('query', ''); v = body.get('variables', {{}})
def fail404():
    sys.stderr.write('gh: Not Found (HTTP 404)\\n'); sys.exit(1)
if command == 'pr':
    number = int(args[2]); state = s['pr_states'].get(str(number), {{'state': 'OPEN', 'mergedAt': None}})
    result = state
elif '/collaborators/' in endpoint:
    login = endpoint.split('/collaborators/')[1].split('/')[0]
    result = {{'permission': s['permissions'].get(login, 'write')}}
elif endpoint == 'graphql':
    if 'query Fields' in q:
        result = {{'data': {{'node': {{'fields': {{'nodes': s['fields'], 'pageInfo': {{'hasNextPage': False}}}}}}}}}}
    elif 'query Items' in q:
        result = {{'data': {{'node': {{'items': {{'nodes': s['items'], 'pageInfo': {{'hasNextPage': False}}}}}}}}}}
    else:
        item = next(x for x in s['items'] if x['id'] == v['item']); vals = item['fieldValues']['nodes']
        vals[:] = [x for x in vals if x['field']['id'] != v['field']]
        if 'option' in v:
            vals.append({{'field': {{'id': v['field']}}, 'optionId': v['option']}})
        elif 'text' in v:
            vals.append({{'field': {{'id': v['field']}}, 'text': v['text']}})
        result = {{'data': {{'updateProjectV2ItemFieldValue': {{'projectV2Item': {{'id': v['item']}}}}}}}}
elif '/comments' in endpoint:
    if 'POST' in args:
        result = {{'id': len(s['comments']) + 1, 'body': body['body'], 'user': {{'login': {bot_login!r}}}, 'created_at': '2026-01-01T00:00:%02dZ' % (len(s['comments']) + 1)}}
        s['comments'].append(result)
    else:
        result = s['comments']
elif '/labels' in endpoint:
    if 'POST' in args:
        s['labels'] = sorted(set(s['labels']) | set(body['labels'])); result = []
    elif 'DELETE' in args:
        s['labels'] = [l for l in s['labels'] if l != endpoint.rsplit('/', 1)[1]]; result = []
    else:
        result = [{{'name': l}} for l in s['labels']]
elif re.fullmatch(r'repos/[^/]+/[^/]+/issues/\\d+', endpoint):
    issue = s['issue']
    if 'PATCH' in args:
        issue['state'] = body.get('state', issue['state'])
        for item in s['items']:
            item['content']['state'] = issue['state'].upper()
    result = {{'node_id': 'I1', 'number': 1, 'user': {{'login': 'writer'}}, 'labels': [{{'name': l}} for l in s['labels']],
              'type': {{'name': 'Bug'}}, 'state': issue['state'], 'body': issue['body'], 'title': issue['title']}}
elif '/branches/' in endpoint:
    branch = endpoint.split('/branches/')[1].replace('%2F', '/')
    if branch not in s['branches']:
        fail404()
    result = {{'name': branch, 'commit': {{'sha': s['branches'][branch]}}}}
elif '/pulls?' in endpoint:
    head = endpoint.split('head=')[1].split('&')[0].replace('%2F', '/').replace('%3A', ':').split(':', 1)[1]
    result = [{{'html_url': pr['url'], 'number': pr['number'], 'head': {{'sha': pr['sha']}}}} for pr in s['pulls'] if pr['branch'] == head]
else:
    raise Exception('Unexpected gh request ' + repr(args))
p.write_text(json.dumps(s)); print(json.dumps(result))
"""


class Harness:
    def __init__(
        self, tmp_path: Path, *, factory_owner: bool = True, execution: str = "docker"
    ) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "factory"
        self.execution = execution
        self.target_sha = _repo(
            tmp_path / "work",
            {"README.md": "fixture\n", "lib.py": "def add(a, b):\n    return a - b\n"},
        )
        self.origin = tmp_path / "work-origin.git"
        self.runner_sha = _repo(
            tmp_path / "runner",
            {
                "scripts/sandbox-run.sh": SANDBOX,
                "workflows/core/finalize-pr-v1.0.yaml": 'name: finalize-pr\nparams:\n  - name: ci_fix_cycles\n    default: "3"\nsteps: []\n',
                "workflows/core/implement-change-v1.0.yaml": "# fixture",
            },
        )
        self.skills_sha = _repo(tmp_path / "skills", {"README.md": "fixture"})
        prefix = "evals/agent-runner/and-scene/"
        _repo(
            tmp_path / "evals",
            {
                prefix + "run.sh": "#!/bin/sh\nexit 0\n",
                prefix + "human-review.sh": "#!/bin/sh\nexit 0\n",
                **{
                    prefix + "lib/" + n + ".mjs": "// fixture"
                    for n in ("phases", "outcomes", "result")
                },
            },
        )
        mirrors = self.root / "mirrors"
        mirrors.mkdir(parents=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--mirror",
                str(self.origin),
                str(mirrors / "example__work.git"),
            ],
            check=True,
        )
        self.working = tmp_path / "working"
        subprocess.run(["git", "clone", "--quiet", str(self.origin), str(self.working)], check=True)
        _git(self.working, "checkout", "-q", "-b", "dev")
        shared_text = Path("config/codagent.toml").read_text()
        shared_text = shared_text[: shared_text.index("[fix]")] + (
            '[fix]\ncontract = "factory-fix/1"\n[fix.branches]\nrunner = "main"\nskills = "main"\n'
            f'[[fix.targets]]\nrepository = "{REPOSITORY}"\n[fix.defaults]\n'
            'lead = "codex:test:high"\nimplementor = "codex:test:high"\ntester = "codex:test:high"\n'
        )
        (tmp_path / "shared.toml").write_text(shared_text)
        self.shared = SharedConfig.from_toml(shared_text)
        self.config = tmp_path / "local.toml"
        self.config.write_text(f'''shared_config = "{tmp_path / "shared.toml"}"
storage_root = "{self.root}"
[repositories]
agent_evals = "{tmp_path / "evals"}"
agent_runner = "{tmp_path / "runner"}"
agent_skills = "{tmp_path / "skills"}"
[repositories.working_clones]
"{REPOSITORY}" = "{self.working}"
[schedule]
timezone = "UTC"
poll_seconds = 1
start_hour = 0
stop_hour = 15
[limits]
minimum_free_gib = 0
inactivity_seconds = 20
execution_seconds = 30
total_seconds = 60
codex_reset_fallback_seconds = 18000
[fix]
execution = "{execution}"
[fix.limits]
inactivity_seconds = 20
execution_seconds = 30
total_seconds = 60
[credentials]
github_app_key = "{tmp_path / "key.pem"}"
suite_environment = "{tmp_path / "suite.env"}"
fix_environment = "{tmp_path / "fix.env"}"
''')
        # Host mode reads the Runner's user settings and git identity from HOME; the harness
        # owns a HOME so the journey never depends on the developer's own configuration.
        self.home = tmp_path / "home"
        (self.home / ".agent-runner").mkdir(parents=True)
        (self.home / ".agent-runner" / "settings.yaml").write_text(
            "autonomous_backend: headless\nautonomous_permission_mode: yolo\n"
        )
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = Harness\n\temail = harness@example.invalid\n"
        )
        for name, content in (
            ("key.pem", "test key"),
            ("suite.env", "CANDIDATE_TOKEN=test-only\n"),
            ("fix.env", f"GH_TOKEN={FIX_TOKEN}\n"),
        ):
            (tmp_path / name).write_text(content)
            (tmp_path / name).chmod(0o600)
        self.board = tmp_path / "board.json"
        fields: list[dict[str, object]] = []
        for configured in (
            self.shared.project.status,
            self.shared.project.owner,
            self.shared.project.verdict,
        ):
            fields.append(
                {
                    "id": configured.id,
                    "dataType": "SINGLE_SELECT",
                    "options": [{"id": v, "name": k} for k, v in configured.options.items()],
                }
            )
        fields.append({"id": self.shared.project.refs.id, "dataType": "TEXT"})
        if self.shared.project.priority_id:
            fields.append(
                {
                    "id": self.shared.project.priority_id,
                    "dataType": "SINGLE_SELECT",
                    "name": "Priority",
                }
            )
        field_values = [
            {
                "field": {"id": self.shared.project.status.id},
                "optionId": self.shared.project.status.option("ready"),
            }
        ]
        if factory_owner:
            field_values.append(
                {
                    "field": {"id": self.shared.project.owner.id},
                    "optionId": self.shared.project.owner.option("factory"),
                }
            )
        item: dict[str, Any] = {
            "id": "P1",
            "content": {
                "__typename": "Issue",
                "id": "I1",
                "number": 1,
                "body": "add() subtracts",
                "state": "OPEN",
                "author": {"login": "writer"},
                "repository": {"nameWithOwner": REPOSITORY},
                "labels": {"nodes": []},
                "issueType": {"name": "Bug"},
            },
            "fieldValues": {"nodes": field_values},
        }
        self.board.write_text(
            json.dumps(
                {
                    "items": [item],
                    "fields": fields,
                    "comments": [
                        {
                            "id": 900,
                            "body": "steps: call add(1, 2)",
                            "user": {"login": "writer"},
                            "created_at": "2025-12-31T00:00:00Z",
                        },
                        {
                            "id": 901,
                            "body": "drive-by",
                            "user": {"login": "rando"},
                            "created_at": "2025-12-31T00:00:01Z",
                        },
                    ],
                    "labels": [],
                    "permissions": {"writer": "write", "rando": "read"},
                    "branches": {},
                    "pulls": [],
                    "pr_states": {},
                    "issue": {
                        "title": "add() subtracts",
                        "body": "add() subtracts",
                        "state": "open",
                    },
                }
            )
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name, content in {
            "gh": _gh_stub(self.board, self.shared.bot_login),
            "openssl": "#!/bin/sh\ncat >/dev/null\nprintf signature",
            "docker": DOCKER,
            "codex": CODEX,
            "cursor": "#!/bin/sh\nexit 0",
            "agent-runner": RUNNER,
            "jq": "#!/bin/sh\nexit 0",
            "agent-validator": "#!/bin/sh\nexit 0",
        }.items():
            script = bin_dir / name
            script.write_text(content)
            script.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(self.home),
        }
        self.store = ClaimStore(self.root / "state.sqlite3")

    def tick(self) -> None:
        entrypoint = """
import io, runpy, urllib.request
def token_response(request, *, timeout):
    return io.BytesIO(b'{"token":"app-token-value","expires_at":"2099-01-01T00:00:00Z"}')
urllib.request.urlopen = token_response
runpy.run_module('agent_factory.cli', run_name='__main__')
"""
        done = subprocess.run(
            [sys.executable, "-c", entrypoint, "--config", str(self.config), "tick"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert done.returncode == 0, done.stderr

    def cli(self, *args: str) -> str:
        done = subprocess.run(
            [sys.executable, "-m", "agent_factory.cli", "--config", str(self.config), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout

    def set_execution(self, execution: str) -> None:
        text = self.config.read_text()
        self.config.write_text(
            text.replace(f'execution = "{self.execution}"', f'execution = "{execution}"', 1)
        )
        self.execution = execution

    def state(self) -> dict[str, Any]:
        return json.loads(self.board.read_text())

    def update(self, **changes: Any) -> None:
        data = self.state()
        data.update(changes)
        self.board.write_text(json.dumps(data))

    def set_status(self, value: str) -> None:
        data = self.state()
        for field in data["items"][0]["fieldValues"]["nodes"]:
            if field["field"]["id"] == self.shared.project.status.id:
                field["optionId"] = self.shared.project.status.option(value)
        self.board.write_text(json.dumps(data))

    def field(self, field_id: str) -> str | None:
        for field in self.state()["items"][0]["fieldValues"]["nodes"]:
            if field["field"]["id"] == field_id:
                return str(field.get("optionId") or field.get("text"))
        return None

    def status(self) -> str:
        value = self.field(self.shared.project.status.id)
        return next(k for k, v in self.shared.project.status.options.items() if v == value)

    def comments(self) -> list[str]:
        return [c["body"] for c in self.state()["comments"]]

    def active_run(self) -> Run:
        runs = self.store.nonterminal_runs(kind="fix")
        assert len(runs) == 1, "expected exactly one fix attempt in flight"
        return runs[0]

    def wait_started(self, run: Run) -> Path:
        artifact = Path(run.evidence_path) / f"attempt-{run.attempt_number + 1}"
        deadline = time.monotonic() + 8
        while not (artifact / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (artifact / "started").exists(), "execution stub never started"
        return artifact

    def finish(self, artifact: Path, script: str) -> None:
        (artifact / "finish").write_text(script)
        deadline = time.monotonic() + 8
        while self.store.nonterminal_runs() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not self.store.nonterminal_runs(), "attempt did not terminate"

    def branch_for(self, claim_id: str) -> str:
        return f"factory/fix-1-{claim_id[:8]}"


def _pr_outcome(branch: str, number: int = 214) -> str:
    return json.dumps(
        {
            "contract": "factory-fix/1",
            "outcome": "pull-request",
            "reasons": [],
            "pr": {
                "url": f"https://github.com/{REPOSITORY}/pull/{number}",
                "number": number,
                "branch": branch,
                "head_sha": "f" * 40,
            },
            "validator": {"status": "passed"},
            "ci": {"status": "passed"},
        }
    )


def test_e2e_002_ready_bug_without_owner_is_assigned_to_factory_and_launched(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path, factory_owner=False)

    h.tick()

    assert h.field(h.shared.project.owner.id) == h.shared.project.owner.option("factory")
    run = h.active_run()
    artifact = h.wait_started(run)
    try:
        h.finish(artifact, json.dumps({"contract": "factory-fix/1", "outcome": "failed"}))
    finally:
        (artifact / "finish").touch()
        h.store.close()


def test_e2e_002_fix_journey_launches_reports_syncs_and_cleans_up(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.tick()
    run = h.active_run()
    artifact = h.wait_started(run)
    try:
        claim = h.store.get_claim(run.claim_id)
        assert claim is not None
        branch = h.branch_for(claim.id)
        args: list[str] = json.loads((artifact / "sandbox-args.json").read_text())
        assert args[args.index("--image") + 1] == f"agent-runner-factory:{run.id}"
        assert "--no-default-secrets" in args
        assert "--mount-codex-auth" in args
        clones = h.root / "clones" / claim.id / "0"
        assert f"type=bind,source={clones / 'repo'},target=/workspace/repo" in args
        assert f"type=bind,source={clones / 'skills'},target=/workspace/skills,readonly" in args
        assert (artifact / "env-file-copy.txt").read_text() == f"GH_TOKEN={FIX_TOKEN}\n"
        assert (artifact / "cwd.txt").read_text() == str(clones / "runner")
        script = args[args.index("--") + 1]
        assert f"--param branch_name={branch}" in script
        assert "agent-runner run factory-fix" in script
        staged = artifact / "agent-runner" / "workflows"
        workflow_text = (staged / "factory-fix-v1.0.yaml").read_text()
        assert workflow_text.splitlines()[0] == "# factory-contract: factory-fix/1"
        assert "builtin:core/finalize-pr-v1.0.yaml" in workflow_text
        for name in ("record-triage.sh", "record-outcome.sh"):
            assert os.access(staged / name, os.X_OK), name
        names: list[str] = json.loads((artifact / "env-names.json").read_text())
        assert "GH_TOKEN" not in names and "GITHUB_TOKEN" not in names
        assert "app-token-value" not in json.dumps(args) + json.dumps(run.plan)
        assert _git(clones / "repo", "rev-parse", "HEAD") == h.target_sha
        assert _git(clones / "runner", "rev-parse", "HEAD") == h.runner_sha
        assert _git(clones / "skills", "rev-parse", "HEAD") == h.skills_sha
        assert (
            _git(clones / "repo", "remote", "get-url", "origin")
            == f"https://github.com/{REPOSITORY}.git"
        )
        issue = json.loads((artifact / "input" / "issue.json").read_text())
        assert issue["title"] == "add() subtracts"
        assert issue["number"] == 1 and issue["claim_id"] == claim.id
        assert issue["attempt"] == 1
        assert [c["author"] for c in issue["comments"]] == ["writer"]
        frozen = claim.frozen_spec
        assert frozen["revisions"] == {
            "target": h.target_sha,
            "runner": h.runner_sha,
            "skills": h.skills_sha,
        }
        assert h.status() == "running"
        assert h.field(h.shared.project.refs.id) == (
            f"target@{h.target_sha[:7]} runner@{h.runner_sha[:7]} skills@{h.skills_sha[:7]}"
        )
        assert any("Fix inputs frozen" in body for body in h.comments())
        h.finish(artifact, _pr_outcome(branch))
        h.tick()
        claim = h.store.get_claim(claim.id)
        assert claim is not None and claim.lifecycle == "settled"
        assert claim.outcome["verdict"] == "pending-human-review"
        assert h.status() == "review"
        assert h.field(h.shared.project.verdict.id) == h.shared.project.verdict.option(
            "pending-human-review"
        )
        assert any(f"https://github.com/{REPOSITORY}/pull/214" in body for body in h.comments())
        finished = h.store.get_run(run.id)
        assert (
            finished is not None
            and finished.result["image_tag"] == f"agent-runner-factory:{run.id}"
        )
        # The PR merges upstream: main advances, GitHub reports it merged.
        merged_sha = _commit(tmp_path / "work", "the fix")
        _git(tmp_path / "work", "push", "-q", "origin", "main")
        h.update(pr_states={"214": {"state": "MERGED", "mergedAt": "2026-01-02T00:00:00Z"}})
        h.tick()
        assert _git(h.working, "branch", "--show-current") == "dev"
        assert _git(h.working, "merge-base", "--is-ancestor", merged_sha, "HEAD") == ""
        assert h.state()["issue"]["state"] == "closed"
        claim = h.store.get_claim(claim.id)
        assert claim is not None
        sync = cast(dict[str, Any], claim.reporting["sync"])
        assert sync["completed"] is True
        h.set_status("done")
        h.tick()
        assert not clones.exists()
        assert (h.root / "mirrors" / "example__work.git").is_dir()
        assert f"agent-runner-factory:{run.id}" in (tmp_path / "docker-rmi.log").read_text()
        claim = h.store.get_claim(claim.id)
        assert claim is not None and claim.cleanup["complete"] is True
        assert len(h.store.runs_for_claim(claim.id)) == 1
    finally:
        (artifact / "finish").touch()
        h.store.close()


def test_e2e_002_reconciliation_finds_the_pr_of_a_crashed_attempt(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.tick()
    run = h.active_run()
    artifact = h.wait_started(run)
    try:
        branch = h.branch_for(run.claim_id)
        h.finish(artifact, "crash")
        # The attempt pushed and opened a PR before dying without an outcome.
        h.update(
            branches={branch: "e" * 40},
            pulls=[
                {
                    "url": f"https://github.com/{REPOSITORY}/pull/300",
                    "number": 300,
                    "sha": "e" * 40,
                    "branch": branch,
                }
            ],
        )
        h.tick()
        claim = h.store.get_claim(run.claim_id)
        assert claim is not None and claim.lifecycle == "settled"
        assert claim.outcome["verdict"] == "pending-human-review"
        assert cast(dict[str, Any], claim.outcome["pr"])["number"] == 300
        assert len(h.store.runs_for_claim(claim.id)) == 1, "no relaunch after reconciliation"
        assert h.status() == "review"
        assert any("pull/300" in body for body in h.comments())
    finally:
        (artifact / "finish").touch()
        h.store.close()


def test_e2e_002_recovery_reclones_recorded_commits_then_failed_outcome_reaches_review(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.tick()
    run = h.active_run()
    artifact = h.wait_started(run)
    second: Path | None = None
    try:
        claim_id = run.claim_id
        # The target branch moves on after admission; the retry must ignore it.
        _commit(tmp_path / "work", "unrelated advance")
        _git(tmp_path / "work", "push", "-q", "origin", "main")
        h.finish(artifact, "crash")
        h.tick()
        retry = h.active_run()
        assert retry.reason == "recovery" and retry.claim_id == claim_id
        second = h.wait_started(retry)
        clones = h.root / "clones" / claim_id / "1"
        assert (second / "cwd.txt").read_text() == str(clones / "runner")
        assert _git(clones / "repo", "rev-parse", "HEAD") == h.target_sha
        assert (h.root / "clones" / claim_id / "0").is_dir(), "earlier clones stay until Done"
        assert any("recovery attempt" in body for body in h.comments())
        h.finish(
            second,
            json.dumps(
                {
                    "contract": "factory-fix/1",
                    "outcome": "failed",
                    "reasons": ["tests still fail"],
                    "pr": {"url": f"https://github.com/{REPOSITORY}/pull/215", "number": 215},
                    "validator": {"status": "passed"},
                    "ci": {"status": "failed"},
                }
            ),
        )
        h.tick()
        claim = h.store.get_claim(claim_id)
        assert claim is not None and claim.lifecycle == "settled"
        assert claim.outcome["verdict"] == "failed"
        assert h.status() == "review"
        assert h.field(h.shared.project.verdict.id) == h.shared.project.verdict.option("failed")
        assert any("tests still fail" in body for body in h.comments())
    finally:
        (artifact / "finish").touch()
        if second is not None:
            (second / "finish").touch()
        h.store.close()


def test_e2e_002_needs_input_blocks_then_a_writer_comment_relaunches(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.tick()
    run = h.active_run()
    artifact = h.wait_started(run)
    second: Path | None = None
    try:
        claim_id = run.claim_id
        h.finish(
            artifact,
            json.dumps(
                {"contract": "factory-fix/1", "outcome": "needs-input", "reasons": ["which API?"]}
            ),
        )
        h.tick()
        claim = h.store.get_claim(claim_id)
        assert claim is not None and claim.lifecycle == "blocked"
        assert h.status() == "running"
        assert "needs-input" in h.state()["labels"]
        assert any("which API?" in body for body in h.comments())
        assert not h.store.nonterminal_runs(), "a blocked claim holds no slot"
        data = h.state()
        data["comments"].append(
            {
                "id": 950,
                "body": "use the v2 API",
                "user": {"login": "writer"},
                "created_at": "2099-01-01T00:00:00Z",
            }
        )
        h.board.write_text(json.dumps(data))
        h.tick()
        retry = h.active_run()
        assert retry.reason == "unblock"
        second = h.wait_started(retry)
        issue = json.loads((second / "input" / "issue.json").read_text())
        assert [c["body"] for c in issue["comments"]] == ["use the v2 API"]
        assert issue["attempt"] == 2
        assert "needs-input" not in h.state()["labels"]
        assert (second / "cwd.txt").read_text() == str(
            h.root / "clones" / claim_id / "1" / "runner"
        )
        h.finish(second, _pr_outcome(h.branch_for(claim_id)))
        h.tick()
        claim = h.store.get_claim(claim_id)
        assert claim is not None and claim.lifecycle == "settled"
        assert h.status() == "review"
    finally:
        (artifact / "finish").touch()
        if second is not None:
            (second / "finish").touch()
        h.store.close()


# -- host mode (E2E-002 host journey and mixed-mode recovery) --------------------------------


def _assert_host_launch(h: Harness, run: Run, artifact: Path, claim_id: str) -> None:
    clones = h.root / "clones" / claim_id / str(run.attempt_number)
    args: list[str] = json.loads((artifact / "runner-args.json").read_text())
    assert args[:2] == ["run", "factory-fix"]
    assert args[args.index("--session-dir") + 1] == str(artifact / "agent-runner-session")
    assert f"artifact_dir={artifact}" in args
    assert f"branch_name={h.branch_for(claim_id)}" in args or any(
        a.startswith("branch_name=factory/fix-1-") for a in args
    )
    hints = cast(dict[str, Any], run.plan["ownership_hints"])
    assert hints["sandbox"] == "host" and "image_tag" not in hints
    assert hints["session_dir"] == str(artifact / "agent-runner-session")
    assert hints["runner_executable"] == str(h.tmp / "bin" / "agent-runner")
    assert hints["runner_version"] == "stub-runner 1.0"
    assert FIX_TOKEN not in json.dumps(run.plan)
    assert run.plan["argv"] == ["/bin/bash", str(h.root / "private" / run.id / "host-run.sh")]
    names: list[str] = json.loads((artifact / "env-names.json").read_text())
    for name in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GIT_CONFIG_GLOBAL",
        "GIT_ASKPASS",
        "AGENT_RUNNER_NO_TUI",
    ):
        assert name in names, name
    assert (artifact / "cwd.txt").read_text() == str(clones / "repo")
    assert (artifact / "git-user.txt").read_text().strip() == "fixbot"
    assert (artifact / "host-provenance.json").exists()
    assert (artifact / "agent-runner-session" / "state.json").exists()
    assert not (artifact / "agent-runner" / "workflows").exists(), (
        "host mode must not stage into evidence"
    )
    catalog = clones / "repo" / ".agent-runner" / "workflows"
    assert (catalog / "factory-fix-v1.0.yaml").read_text().splitlines()[
        0
    ] == "# factory-contract: factory-fix/1"
    exclude = (clones / "repo" / ".git" / "info" / "exclude").read_text().splitlines()
    assert "/.agent-runner/workflows/" in exclude and "/.agent-runner/config.yaml" in exclude
    assert _git(clones / "repo", "status", "--porcelain") == ""
    for path in artifact.rglob("*"):
        if path.is_file():
            assert FIX_TOKEN not in path.read_text(errors="replace"), path


def test_e2e_002_host_fix_journey_reports_cleans_up_and_prunes(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from agent_factory import retention
    from agent_factory.config import LocalConfig
    from agent_factory.work_kinds.pull_request.launch import HOST_NOTE

    h = Harness(tmp_path, execution="host")
    h.tick()
    run = h.active_run()
    artifact = h.wait_started(run)
    try:
        claim = h.store.get_claim(run.claim_id)
        assert claim is not None
        _assert_host_launch(h, run, artifact, claim.id)
        assert h.status() == "running"
        assert any("Fix inputs frozen" in body and HOST_NOTE not in body for body in h.comments())
        h.finish(artifact, _pr_outcome(h.branch_for(claim.id)))
        h.tick()
        claim = h.store.get_claim(claim.id)
        assert claim is not None and claim.lifecycle == "settled"
        assert h.status() == "review"
        pr_comment = next(
            b for b in h.comments() if f"https://github.com/{REPOSITORY}/pull/214" in b
        )
        assert HOST_NOTE in pr_comment
        finished = h.store.get_run(run.id)
        assert finished is not None
        assert finished.result["sandbox"] == "host"
        assert finished.result["session_dir"] == str(artifact / "agent-runner-session")
        assert "image_tag" not in finished.result
        assert f"{REPOSITORY}#1" in h.cli("status")
        merged_sha = _commit(tmp_path / "work", "the fix")
        _git(tmp_path / "work", "push", "-q", "origin", "main")
        h.update(pr_states={"214": {"state": "MERGED", "mergedAt": "2026-01-02T00:00:00Z"}})
        h.tick()
        assert _git(h.working, "merge-base", "--is-ancestor", merged_sha, "HEAD") == ""
        h.set_status("done")
        h.tick()
        clones = h.root / "clones" / claim.id / "0"
        assert not clones.exists()
        assert not (h.root / "private" / run.id).exists()
        assert not (tmp_path / "docker-rmi.log").exists(), (
            "a host-only claim must not remove images"
        )
        claim = h.store.get_claim(claim.id)
        assert claim is not None and claim.cleanup["complete"] is True
        assert claim.cleanup.get("done_observed_at")
        hidden = h.cli("status")
        assert f"{REPOSITORY}#1" not in hidden and "hidden: 1" in hidden
        assert f"{REPOSITORY}#1" in h.cli("status", "--all")
        # Retention: the same reconcile the runtime runs each poll, 15 days later.
        local = LocalConfig.from_toml(h.config.read_text())
        later = datetime.now(UTC) + timedelta(days=15)
        retention.reconcile(h.store, local, claim, "Done", later)
        assert not (artifact / "logs").exists()
        assert not (artifact / "agent-runner-session").exists()
        assert (artifact / "fix-outcome.json").exists()
        assert (artifact / "input" / "issue.json").exists()
        assert (artifact / "host-provenance.json").exists()
        claim = h.store.get_claim(claim.id)
        assert claim is not None
        assert cast(dict[str, Any], claim.cleanup["retention"])["pruned_at"]
    finally:
        (artifact / "finish").touch()
        h.store.close()


@pytest.mark.parametrize(("first", "second"), [("docker", "host"), ("host", "docker")])
def test_e2e_002_recovery_launches_in_the_currently_configured_mode(
    tmp_path: Path, first: str, second: str
) -> None:
    from agent_factory.work_kinds.pull_request.launch import HOST_NOTE

    h = Harness(tmp_path, execution=first)
    h.tick()
    run1 = h.active_run()
    artifact1 = h.wait_started(run1)
    artifact2: Path | None = None
    try:
        claim_id = run1.claim_id
        h.finish(artifact1, "crash")
        # The operator switches modes before the recovery retry is admitted.
        h.set_execution(second)
        h.tick()
        run2 = h.active_run()
        assert run2.reason == "recovery" and run2.id != run1.id
        artifact2 = h.wait_started(run2)
        hints1 = cast(dict[str, Any], run1.plan["ownership_hints"])
        hints2 = cast(dict[str, Any], run2.plan["ownership_hints"])
        assert hints1["sandbox"] == first and hints2["sandbox"] == second
        assert ("image_tag" in hints1) is (first == "docker")
        assert ("image_tag" in hints2) is (second == "docker")
        if second == "host":
            _assert_host_launch(h, run2, artifact2, claim_id)
        else:
            assert (artifact2 / "sandbox-args.json").exists()
        h.finish(artifact2, _pr_outcome(h.branch_for(claim_id)))
        h.tick()
        pr_comment = next(
            b for b in h.comments() if f"https://github.com/{REPOSITORY}/pull/214" in b
        )
        assert (HOST_NOTE in pr_comment) is (second == "host")
        retry_comment = next(b for b in h.comments() if "recovery attempt" in b)
        assert (HOST_NOTE in retry_comment) is False or first == "host"
        h.update(pr_states={"214": {"state": "MERGED", "mergedAt": "2026-01-02T00:00:00Z"}})
        h.tick()
        h.set_status("done")
        h.tick()
        rmi = (
            (tmp_path / "docker-rmi.log").read_text()
            if (tmp_path / "docker-rmi.log").exists()
            else ""
        )
        docker_runs = [r for r, mode in ((run1, first), (run2, second)) if mode == "docker"]
        for run in (run1, run2):
            tag = f"agent-runner-factory:{run.id}"
            assert (tag in rmi) is (run in docker_runs), (tag, rmi)
        assert not (h.root / "private" / run1.id).exists()
        assert not (h.root / "private" / run2.id).exists()
        assert not (h.root / "clones" / claim_id / "0").exists()
        assert not (h.root / "clones" / claim_id / "1").exists()
    finally:
        (artifact1 / "finish").touch()
        if artifact2 is not None:
            (artifact2 / "finish").touch()
        h.store.close()
