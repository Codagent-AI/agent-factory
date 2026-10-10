"""INT-006: execute deploy.sh only inside a temporary HOME with stub tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _deploy(
    tmp_path: Path, scenario: str
) -> tuple[subprocess.CompletedProcess[str], list[str], str, str]:
    home = tmp_path / "home"
    home.mkdir()
    scripts = tmp_path / "scripts"
    shutil.copytree(SCRIPTS, scripts)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    releases = home / "releases"
    live = releases / "live" / ".venv/bin/agent-factory"
    target = releases / "abc123456789" / ".venv/bin/agent-factory"
    for path in (live, target):
        path.parent.mkdir(parents=True)
    log = tmp_path / "calls"
    config = home / "local.toml"
    config.write_text(f'shared_config = "{releases}/live/config/codagent.toml"\n')
    plist = home / "plist.json"
    plist.write_text(json.dumps({"exe": str(live), "path": str(live.parent) + ":/bin"}))
    (releases / "abc123456789/.release-complete").touch()
    (releases / "current").symlink_to(releases / "live")
    base = home / "clone"
    (base / ".git").mkdir(parents=True)
    release_stub = f"""#!{sys.executable}
import pathlib, sys
log=pathlib.Path({str(log)!r})
role='live' if '/live/' in sys.argv[0] else 'target'
args=sys.argv[1:]
with log.open('a') as out: out.write(role+' '+ ' '.join(args)+'\\n')
if args[:1] == ['--config']: args=args[2:]
scenario={scenario!r}
if args == ['lanes','supported']:
    supported=((role == 'target' and scenario == 'target-lanes')
               or (role == 'live' and scenario != 'old-live'))
    if supported: print('priority-lanes')
    sys.exit(0 if supported else 2)
if args[:2] == ['lanes','downgrade']:
    if scenario == 'busy' or scenario == 'restore-fails' and '--check' not in args:
        print('fix: c1 example/work#1 low\\nfix: c2 example/work#2 high');sys.exit(1)
    if '--check' not in args: pathlib.Path({str(home / "guard")!r}).write_text('kind')
elif args == ['lanes','enable']:
    pathlib.Path({str(home / "guard")!r}).write_text('lanes')
elif args == ['honored-revisions']: print('fixture')
elif args == ['status']:
    print('paused: false\\neval slot: free\\nfix slot: busy (low)\\nhost attempts: 1')
elif args == ['doctor'] and scenario == 'doctor-fails':
    print('probe: failed');sys.exit(1)
"""
    for path in (live, target):
        _executable(path, release_stub)
    _executable(
        bin_dir / "git", '#!/bin/sh\ncase "$*" in\n*rev-parse*) echo abc1234567890000 ;;\nesac\n'
    )
    _executable(bin_dir / "sleep", "#!/bin/sh\nexit 0\n")
    _executable(bin_dir / "uv", "#!/bin/sh\nexit 0\n")
    _executable(
        bin_dir / "plutil",
        f"""#!{sys.executable}
import json,pathlib,sys
p=pathlib.Path(sys.argv[-1]); data=json.loads(p.read_text())
if '-extract' in sys.argv:
 print(data['exe'] if 'ProgramArguments.0' in sys.argv else data['path'])
""",
    )
    buddy = bin_dir / "PlistBuddy"
    _executable(
        buddy,
        f"""#!{sys.executable}
import json,pathlib,sys
p=pathlib.Path(sys.argv[-1]);data=json.loads(p.read_text())
command=sys.argv[2].split(' ',2)
data['exe' if command[1] == ':ProgramArguments:0' else 'path']=command[2]
p.write_text(json.dumps(data))
""",
    )
    removed = home / "removed"
    bootstrapped = home / "bootstrapped"
    _executable(
        bin_dir / "launchctl",
        f"""#!{sys.executable}
import pathlib,sys
args=sys.argv[1:]; scenario={scenario!r}
with pathlib.Path({str(log)!r}).open('a') as out: out.write('launchctl '+' '.join(args)+'\\n')
removed=pathlib.Path({str(removed)!r}); bootstrapped=pathlib.Path({str(bootstrapped)!r})
if args[0] == 'bootout':
 if scenario != 'unload-fails': removed.touch()
elif args[0] == 'bootstrap':
 if scenario == 'bootstrap-fails': sys.exit(7)
 bootstrapped.touch()
elif args[0] == 'print':
 if removed.exists() and not bootstrapped.exists(): sys.exit(1)
 print('program = '+({str(target)!r} if bootstrapped.exists() else {str(live)!r}))
 state='spawn scheduled' if scenario == 'resident-fails' and bootstrapped.exists() else 'running'
 print('state = '+state)
""",
    )
    script = scripts / "deploy.sh"
    script.write_text(script.read_text().replace("/usr/libexec/PlistBuddy", str(buddy)))
    result = subprocess.run(
        ["bash", str(script), "--no-runner", "--no-validator", "old"],
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "AGENT_FACTORY_SERVICE_CLONE": str(base),
            "AGENT_FACTORY_RELEASES": str(releases),
            "AGENT_FACTORY_CONFIG": str(config),
            "AGENT_FACTORY_PLIST": str(plist),
        },
        capture_output=True,
        text=True,
        # A deploy starts many Python stubs; allow headroom when parallel suites share the host.
        timeout=90,
    )
    pointers = json.loads(plist.read_text())["exe"]
    assert (releases / "current").resolve() == (
        releases / ("abc123456789" if result.returncode == 0 else "live")
    )
    return result, log.read_text().splitlines(), pointers, config.read_text()


@pytest.mark.parametrize(
    "scenario",
    [
        "busy",
        "success",
        "restore-fails",
        "doctor-fails",
        "unload-fails",
        "bootstrap-fails",
        "resident-fails",
        "target-lanes",
        "old-live",
    ],
)
def test_deploy_guard_and_recovery(tmp_path: Path, scenario: str) -> None:
    result, calls, pointer, config = _deploy(tmp_path, scenario)
    downgrade = [call for call in calls if "lanes downgrade" in call]
    enables = [call for call in calls if "lanes enable" in call]
    pause = next((i for i, call in enumerate(calls) if call.endswith(" pause")), None)
    if scenario == "busy":
        assert result.returncode == 1 and pause is None
        assert "example/work#1" in result.stderr and "example/work#2" in result.stderr
        assert "pause; let attempts settle" in result.stderr
        assert "/live/" in pointer and "/live/" in config
        assert len(downgrade) == 1 and downgrade[0].endswith("--check")
    elif scenario in {"target-lanes", "old-live"}:
        assert result.returncode == 0, result.stderr
        assert not downgrade and not enables
    else:
        assert pause is not None and len(downgrade) == 2
        assert calls.index(downgrade[0]) < pause < calls.index(downgrade[1])
        if scenario == "success":
            assert result.returncode == 0, result.stderr
            assert "/abc123456789/" in pointer and "/abc123456789/" in config
        else:
            assert result.returncode != 0
        if scenario in {"doctor-fails", "unload-fails"}:
            assert len(enables) == 1 and enables[0].startswith("live ")
            assert "/live/" in pointer and "/live/" in config
            assert result.returncode == 1
        else:
            assert not enables
        if scenario == "restore-fails":
            assert "/live/" in pointer and "/live/" in config
        if scenario in {"bootstrap-fails", "resident-fails"}:
            assert "/abc123456789/" in pointer
            assert (tmp_path / "home/guard").read_text() == "kind"
