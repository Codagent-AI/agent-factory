from __future__ import annotations

import subprocess
from pathlib import Path

GUARD = Path(__file__).resolve().parents[2] / "scripts/fixture-guard.sh"


def _release(path: Path, *, honors: bool, claims: str = "", fails: bool = False) -> Path:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ $1 == honored-revisions ]]; then\n"
        + ("  printf 'runner\\nfixture\\n'\n" if honors else "  exit 2\n")
        + "elif [[ $3 == pinned-claims ]]; then\n"
        + ("  exit 1\n" if fails else f"  printf '%s' '{claims}'\n")
        + "fi\n"
    )
    path.chmod(0o755)
    return path


def _guard(target: Path, live: Path, stage: str) -> subprocess.CompletedProcess[str]:
    script = (
        "config=/tmp/test-local.toml\n"
        'warn() { printf "warning: %s\\n" "$*" >&2; }\n'
        'die() { printf "error: %s\\n" "$*" >&2; exit 1; }\n'
        f'source "{GUARD}"\n'
        'fixture_guard "$1" "$2" "$3"\n'
    )
    return subprocess.run(
        ["bash", "-c", script, "guard-test", str(target), str(live), stage],
        capture_output=True,
        text=True,
        check=False,
    )


def test_fixture_guard_decision_table(tmp_path: Path) -> None:
    target = _release(tmp_path / "target", honors=False)
    live = _release(tmp_path / "live", honors=True, claims="claim-1\texample/evals#91\n")
    for stage, ending in (("before", "nothing is deployed"), ("after", "the factory stays paused")):
        result = _guard(target, live, stage)
        assert result.returncode == 1
        assert "claim-1" in result.stderr and "example/evals#91" in result.stderr
        assert (
            "pause; let each claim settle, or cancel it; deploy the older release" in result.stderr
        )
        assert "cannot accept fixture_ref" in result.stderr
        assert result.stderr.rstrip().endswith(ending)
    assert _guard(_release(tmp_path / "new", honors=True), live, "before").returncode == 0
    assert _guard(target, _release(tmp_path / "old", honors=False), "before").returncode == 0
    assert _guard(target, _release(tmp_path / "empty", honors=True), "after").returncode == 0
    failed = _guard(target, _release(tmp_path / "fail", honors=True, fails=True), "before")
    assert failed.returncode == 1 and "cannot list" in failed.stderr
    broken = tmp_path / "broken"
    broken.write_text("#!/usr/bin/env bash\nexit 1\n")
    broken.chmod(0o755)
    unknown = _guard(target, broken, "before")
    assert unknown.returncode == 1 and "cannot determine" in unknown.stderr


def test_deploy_checks_both_sides_of_pause() -> None:
    script = (GUARD.parent / "deploy.sh").read_text()
    assert (
        script.index('touch "$release/.release-complete"')
        < script.index('fixture_guard "$executable" "$running" before')
        < script.index('"$running" --config "$config" pause')
    )
    assert (
        script.index('"$running" --config "$config" pause')
        < script.index('fixture_guard "$executable" "$running" after')
        < script.index('point_at "$executable"')
    )
