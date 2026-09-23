from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from agent_factory.fly.api import FlyMachinesClient
from agent_factory.operations import render_launch_agent


def test_fly_claude_token_skips_keychain_and_file(tmp_path: Path) -> None:
    from agent_factory.fly.transport import resolve_claude_login

    env = tmp_path / "suite.env"
    env.write_text("CLAUDE_CODE_OAUTH_TOKEN=from-suite\n")
    with patch("subprocess.run") as run:
        login = resolve_claude_login([env], user="service", home=tmp_path, platform="darwin")
    assert login.kind == "token"
    assert "from-suite" not in repr(login)
    run.assert_not_called()


def test_fly_claude_token_uses_suite_export_syntax(tmp_path: Path) -> None:
    from agent_factory.fly.transport import resolve_claude_login

    env = tmp_path / "suite.env"
    env.write_text("  export\tCLAUDE_CODE_OAUTH_TOKEN=value\n")
    with patch("subprocess.run") as run:
        login = resolve_claude_login([env], home=tmp_path, platform="darwin")
    assert login.kind == "token"
    run.assert_not_called()


def test_fly_empty_quoted_claude_token_uses_file(tmp_path: Path) -> None:
    from agent_factory.fly.transport import environment_text, resolve_claude_login

    env = tmp_path / "suite.env"
    env.write_text('CLAUDE_CODE_OAUTH_TOKEN=""\n')
    credentials = tmp_path / ".claude/.credentials.json"
    credentials.parent.mkdir()
    credentials.write_text("credential")
    assert resolve_claude_login([env], home=tmp_path, platform="linux").kind == "file"
    assert (
        resolve_claude_login(
            [], home=tmp_path, platform="linux", env_text=environment_text([env], [])
        ).kind
        == "file"
    )


def test_fly_claude_keychain_login_is_resolved_without_a_host_file(tmp_path: Path) -> None:
    from agent_factory.fly.transport import resolve_claude_login

    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            [],
            0,
            b'{"claudeAiOauth":{"accessToken":"access","refreshToken":"refresh","expiresAt":123}}',
        ),
    ) as run:
        login = resolve_claude_login([], user="service", home=tmp_path, platform="darwin")
    assert login.kind == "keychain"
    assert b'"accessToken":"access"' in login.data
    assert "claudeAiOauth" not in repr(login)
    assert run.call_args.args[0][-3:] == ["-a", "service", "-w"]


def test_fly_empty_keychain_oauth_is_unavailable(tmp_path: Path) -> None:
    from agent_factory.fly.transport import resolve_claude_login

    with patch(
        "subprocess.run", return_value=subprocess.CompletedProcess([], 0, b'{"claudeAiOauth": {}}')
    ):
        login = resolve_claude_login([], user="service", home=tmp_path, platform="darwin")
    assert login.kind == "unavailable"


def test_fly_claude_keychain_timeout_does_not_fall_back(tmp_path: Path) -> None:
    from agent_factory.fly.transport import resolve_claude_login

    credentials = tmp_path / ".claude" / ".credentials.json"
    credentials.parent.mkdir()
    credentials.write_text("{}")
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("security", 10)):
        login = resolve_claude_login([], user="service", home=tmp_path, platform="darwin")
    assert login.kind == "unavailable"
    assert "Claude Code-credentials" in login.reason and "service" in login.reason


def test_fly_claude_keychain_missing_uses_file(tmp_path: Path) -> None:
    from agent_factory.fly.transport import resolve_claude_login

    credentials = tmp_path / ".claude" / ".credentials.json"
    credentials.parent.mkdir()
    credentials.write_text("{}")
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 44, b"")):
        login = resolve_claude_login([], user="service", home=tmp_path, platform="darwin")
    assert login.kind == "file" and login.path == credentials


def test_fly_claude_readiness_checks_frozen_roles(tmp_path: Path) -> None:
    from agent_factory.fly.transport import ClaudeLogin
    from agent_factory.work_kinds.eval.handler import fly_claude_readiness

    with patch(
        "agent_factory.work_kinds.eval.handler.resolve_claude_login",
        return_value=ClaudeLogin("unavailable", reason="missing"),
    ):
        assert fly_claude_readiness({"lead": "codex:x:medium"}, tmp_path) is None
        assert "missing" in str(fly_claude_readiness({"lead": "claude:x:medium"}, tmp_path))


def test_fly_credential_validation_precedes_machine_claim(tmp_path: Path) -> None:
    from agent_factory.fly.transport import ClaudeLogin, JobRequest, Lifecycle

    manifest = {
        "run_id": "r",
        "claim_id": "c",
        "unit_key": "rep-1",
        "nonce": "n",
        "image": "registry.fly.io/app:base",
        "deadline": {"total_seconds": 1, "collection_grace_seconds": 1},
        "fly": {"app": "app", "token_file": str(tmp_path / "token")},
    }
    lifecycle = Lifecycle(manifest, tmp_path)
    request = JobRequest(tmp_path, "", None, "", False, True)
    with (
        patch(
            "agent_factory.fly.transport.resolve_claude_login",
            return_value=ClaudeLogin("unavailable", reason="missing"),
        ),
        patch.object(lifecycle, "_claim", side_effect=AssertionError("created")),
    ):
        assert lifecycle.run(request) == 70


def test_fly_token_still_delivers_optional_claude_file(tmp_path: Path) -> None:
    from agent_factory.fly.transport import FlyTransportError, JobRequest, Lifecycle

    credentials = tmp_path / ".claude/.credentials.json"
    credentials.parent.mkdir()
    credentials.write_text("optional")
    manifest = {
        "run_id": "r",
        "claim_id": "c",
        "unit_key": "rep-1",
        "nonce": "n",
        "image": "registry.fly.io/app:base",
        "fly": {"app": "app", "token_file": str(tmp_path / "token")},
    }
    lifecycle = Lifecycle(manifest, tmp_path)
    request = JobRequest(tmp_path, "", None, "CLAUDE_CODE_OAUTH_TOKEN=token\n", False, True)
    with (
        patch("pathlib.Path.home", return_value=tmp_path),
        patch.object(lifecycle, "_claim", side_effect=FlyTransportError("stop after validation")),
    ):
        assert lifecycle.run(request) == 70
    assert (credentials, "claude/.credentials.json") in lifecycle._resolved_credentials  # pyright: ignore[reportPrivateUsage]


def test_fly_relaunch_clears_old_presuite_stage(tmp_path: Path) -> None:
    from agent_factory.fly.transport import JobRequest, Lifecycle

    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "launch-stage.json").write_text('{"failure_stage":"pre-suite","stage":"old"}')
    lifecycle = Lifecycle({"fly": {"app": "app", "token_file": str(tmp_path / "token")}}, factory)
    request = JobRequest(tmp_path, "", None, "CLAUDE_CODE_OAUTH_TOKEN=token\n", False, False)
    with (
        patch.object(lifecycle, "_claim", return_value=("machine", 100)),
        patch.object(lifecycle, "_next_job", return_value=(1, True)),
        patch.object(lifecycle, "_follow", return_value=0),
    ):
        assert lifecycle.run(request) == 0
    assert not (factory / "launch-stage.json").exists()


def test_fly_transport_streams_login_bytes_to_guest_stdin() -> None:
    from agent_factory.fly.transport import FlyTransport

    transport = FlyTransport("app", "machine")
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"")) as run:
        transport.put_bytes(b"secret", "/host-home/claude/.credentials.json")
    assert run.call_args.kwargs["input"] == b"secret"
    assert "--pty=false" in run.call_args.args[0]


def test_fly_transport_stream_is_byte_exact_with_stand_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_factory.fly.transport import FlyTransport
    from tests.fixtures.fly.flyctl import write_guest_flyctl

    bin_dir = tmp_path / "bin"
    guest = tmp_path / "guest"
    guest.mkdir()
    (guest / "host-home/claude").mkdir(parents=True)
    write_guest_flyctl(bin_dir, guest, tmp_path / "flyctl.log", tmp_path / "record.json")
    monkeypatch.setenv("PATH", str(bin_dir) + ":" + os.environ["PATH"])
    transport = FlyTransport("app", "machine")
    payload = b'{"claudeAiOauth":{"refreshToken":"a\\nb"}}\x00\xff'
    transport.put_bytes(payload, "/host-home/claude/.credentials.json")
    assert (guest / "host-home/claude/.credentials.json").read_bytes() == payload


def test_machine_image_digest_never_returns_tag() -> None:
    from agent_factory.fly.transport import image_digest

    assert image_digest({"image_ref": {"digest": "sha256:abc", "tag": "base"}}) == "sha256:abc"
    assert image_digest({"image_ref": "registry.fly.io/app:base"}) == "unavailable"
    assert image_digest({"image_ref": "registry.fly.io/app@sha256:def"}) == "sha256:def"


def test_fly_backend_provenance_uses_top_level_digest(tmp_path: Path) -> None:
    from agent_factory.fly.backend import FlyMachineBackend
    from tests.integration.test_fly_backend import (
        FakeClient,
        _identity,  # pyright: ignore[reportPrivateUsage]
    )

    token = tmp_path / "token"
    token.write_text("secret")
    machine: dict[str, object] = {
        "id": "machine-1",
        "region": "ewr",
        "image_ref": {"digest": "sha256:abc", "tag": "base"},
        "config": {
            "image": "registry.fly.io/factory:base",
            "guest": {},
            "metadata": {
                "factory-owner": "agent-factory",
                "run_id": "run-1",
                "claim_id": "claim-1",
                "unit_key": "rep-1",
                "nonce": "nonce",
            },
        },
    }
    backend = FlyMachineBackend(client_factory=lambda app, path: FakeClient(machine))
    plan, run = _identity(tmp_path, token)
    identity = backend.identity_from_plan(plan, run)
    assert identity is not None
    assert backend.provenance(identity)["image_digest"] == "sha256:abc"


def test_launch_agent_sets_service_account_and_templates_match(tmp_path: Path) -> None:
    rendered = render_launch_agent(
        *(tmp_path / name for name in ("exe", "config", "root", "log", "key")), user="service&user"
    )
    environment = plistlib.loads(rendered.encode())["EnvironmentVariables"]
    assert environment["USER"] == environment["LOGNAME"] == "service&user"
    template = Path("packaging/launchd/com.codagent.agent-factory.plist").read_text()
    from agent_factory.operations import _PLIST_TEMPLATE  # pyright: ignore[reportPrivateUsage]

    assert template == _PLIST_TEMPLATE


def test_launch_agent_identity_diagnostic_rejects_missing_user(tmp_path: Path) -> None:
    from agent_factory.operations import launch_agent_identity_diagnostic

    plist = tmp_path / "service.plist"
    with plist.open("wb") as stream:
        plistlib.dump({"EnvironmentVariables": {"PATH": "/usr/bin"}}, stream)
    result = launch_agent_identity_diagnostic(plist)
    assert not result.available
    assert "USER" in result.detail
    assert "bootstrap" in result.action


def test_stale_unknown_keychain_item_is_informational() -> None:
    from agent_factory.operations import stale_unknown_keychain_diagnostic

    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"")):
        result = stale_unknown_keychain_diagnostic(platform="darwin")
    assert result.available
    assert "unknown" in result.detail
    assert result.action == ""


def test_fix_and_eval_acceptance_use_kind_specific_wording() -> None:
    from agent_factory.work_kinds.eval.handler import EvalHandler
    from agent_factory.work_kinds.fix.handler import FixHandler

    assert EvalHandler.accepted_message() == "Evaluation inputs accepted and frozen."
    assert FixHandler.accepted_message() == "Fix inputs accepted and frozen."


def test_registry_lookup_uses_get_for_fresh_tag(tmp_path: Path) -> None:
    from tests.fixtures.fly.api import FakeMachinesApi

    token = tmp_path / "token"
    token.write_text("deploy-token")
    with FakeMachinesApi(manifest_digest="sha256:abc") as api:
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        assert client.resolve_manifest("registry.fly.io/app:claim-123") == "sha256:abc"
        assert next(r for r in api.requests if "/manifests/" in str(r["path"]))["method"] == "GET"


def test_registry_lookup_accepts_digest_reference(tmp_path: Path) -> None:
    from tests.fixtures.fly.api import FakeMachinesApi

    token = tmp_path / "token"
    token.write_text("deploy-token")
    with FakeMachinesApi(manifest_digest="sha256:abc") as api:
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        assert client.resolve_manifest("registry.fly.io/app@sha256:abc") == "sha256:abc"
        request = next(r for r in api.requests if "/manifests/" in str(r["path"]))
        assert request["path"] == "/v2/app/manifests/sha256:abc"


def test_fly_claim_image_build_records_digest_and_refreshes_cli_layer(tmp_path: Path) -> None:
    from agent_factory.fly.transport import build_claim_image

    runner = tmp_path / "runner"
    runner.mkdir()
    factory = tmp_path / ".factory"
    factory.mkdir()
    output = (
        b"#14 pushing manifest for registry.fly.io/app:claim-abcdef123456@sha256:"
        + b"a" * 64
        + b" 0.4s done\n"
    )

    class Builder:
        def __init__(self, *args: object, stdout: object, **kwargs: object) -> None:
            stdout.write(output)  # type: ignore[attr-defined]

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def poll(self) -> int:
            return 0

    with patch("subprocess.Popen", side_effect=Builder) as run:
        image = build_claim_image(
            "app",
            "registry.fly.io/app",
            "abcdef123456789",
            runner,
            factory,
            {"FLY_ACCESS_TOKEN": "secret"},
        )
    assert image == "registry.fly.io/app@sha256:" + "a" * 64
    argv = run.call_args.args[0]
    assert "--remote-only" in argv and "--build-only" in argv and "--push" in argv
    assert "FACTORY_CLI_REFRESH=abcdef123456789" in argv
    assert run.call_args.kwargs["cwd"] == runner
    assert "secret" not in repr(argv)
    assert "sha256:" in (factory / "image-build.json").read_text()
    assert (factory / "image-build.log").read_bytes() == output


def test_fly_claim_image_reads_buildkit_manifest_push_line(tmp_path: Path) -> None:
    from agent_factory.fly.transport import build_claim_image

    runner = tmp_path / "runner"
    runner.mkdir()
    factory = tmp_path / ".factory"
    factory.mkdir()
    # BuildKit's plain progress, as Fly's remote builder streams it.
    output = (
        b"#14 exporting to image\n"
        b"#14 exporting layers 2.1s done\n"
        b"#14 exporting manifest sha256:" + b"c" * 64 + b" done\n"
        b"#14 pushing layers 4.0s done\n"
        b"#14 pushing manifest for registry.fly.io/app:claim-abcdef123456@sha256:"
        + b"a"
        * 64
        + b" 0.4s done\n"
        b"#14 DONE 6.8s\n"
    )

    class Builder:
        def __init__(self, *args: object, stdout: object, **kwargs: object) -> None:
            stdout.write(output)  # type: ignore[attr-defined]

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def poll(self) -> int:
            return 0

    with (
        patch("subprocess.Popen", side_effect=Builder),
        patch.object(
            FlyMachinesClient, "resolve_manifest", return_value="sha256:" + "f" * 64
        ) as lookup,
    ):
        client = FlyMachinesClient("app", tmp_path / "token")
        image = build_claim_image(
            "app", "registry.fly.io/app", "abcdef123456789", runner, factory, {}, client
        )
    assert image == "registry.fly.io/app@sha256:" + "a" * 64
    lookup.assert_not_called()


def test_fly_claim_image_ignores_unrelated_digest_after_manifest_push(tmp_path: Path) -> None:
    from agent_factory.fly.transport import build_claim_image

    runner = tmp_path / "runner"
    runner.mkdir()
    factory = tmp_path / ".factory"
    factory.mkdir()
    output = (
        b"#14 pushing manifest for registry.fly.io/app:claim-abcdef123456@sha256:"
        + b"a" * 64
        + b" 0.4s done\n#14 exporting manifest sha256:"
        + b"b" * 64
        + b"\n"
    )

    class Builder:
        def __init__(self, *args: object, stdout: object, **kwargs: object) -> None:
            stdout.write(output)  # type: ignore[attr-defined]

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def poll(self) -> int:
            return 0

    with patch("subprocess.Popen", side_effect=Builder):
        image = build_claim_image(
            "app", "registry.fly.io/app", "abcdef123456789", runner, factory, {}
        )
    assert image == "registry.fly.io/app@sha256:" + "a" * 64


def test_fly_claim_image_uses_manifest_push_after_earlier_tag_digest(tmp_path: Path) -> None:
    from agent_factory.fly.transport import build_claim_image

    runner = tmp_path / "runner"
    runner.mkdir()
    factory = tmp_path / ".factory"
    factory.mkdir()
    output = (
        b"cached image registry.fly.io/app:claim-abcdef123456@sha256:"
        + b"b" * 64
        + b"\n#14 pushing manifest for registry.fly.io/app:claim-abcdef123456@sha256:"
        + b"a" * 64
        + b" 0.4s done\n"
    )

    class Builder:
        def __init__(self, *args: object, stdout: object, **kwargs: object) -> None:
            stdout.write(output)  # type: ignore[attr-defined]

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def poll(self) -> int:
            return 0

    with patch("subprocess.Popen", side_effect=Builder):
        image = build_claim_image(
            "app", "registry.fly.io/app", "abcdef123456789", runner, factory, {}
        )
    assert image == "registry.fly.io/app@sha256:" + "a" * 64


def test_fly_claim_image_uses_registry_when_output_has_no_manifest_push(tmp_path: Path) -> None:
    from agent_factory.fly.transport import build_claim_image

    runner = tmp_path / "runner"
    runner.mkdir()
    factory = tmp_path / ".factory"
    factory.mkdir()

    class Builder:
        def __init__(self, *args: object, stdout: object, **kwargs: object) -> None:
            stdout.write(  # type: ignore[attr-defined]
                b"cached image registry.fly.io/app:claim-abcdef123456@sha256:" + b"b" * 64 + b"\n"
            )

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def poll(self) -> int:
            return 0

    with (
        patch("subprocess.Popen", side_effect=Builder),
        patch.object(
            FlyMachinesClient, "resolve_manifest", return_value="sha256:" + "a" * 64
        ) as lookup,
    ):
        client = FlyMachinesClient("app", tmp_path / "token")
        image = build_claim_image(
            "app", "registry.fly.io/app", "abcdef123456789", runner, factory, {}, client
        )
    assert image == "registry.fly.io/app@sha256:" + "a" * 64
    lookup.assert_called_once_with("registry.fly.io/app:claim-abcdef123456")


def test_fly_claim_image_opens_log_before_process(tmp_path: Path) -> None:
    from agent_factory.fly.transport import build_claim_image

    runner = tmp_path / "runner"
    runner.mkdir()
    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "image-build.log").mkdir()
    with patch("subprocess.Popen") as run, pytest.raises(OSError):
        build_claim_image("app", "registry.fly.io/app", "abcdef123456789", runner, factory, {})
    run.assert_not_called()


def test_fly_claim_image_timeout_terminates_process(tmp_path: Path) -> None:
    from agent_factory.fly.transport import FlyTransportError, build_claim_image

    runner = tmp_path / "runner"
    runner.mkdir()
    factory = tmp_path / ".factory"
    factory.mkdir()

    class Builder:
        terminated = False

        def wait(self, timeout: float | None = None) -> int:
            if not self.terminated:
                raise subprocess.TimeoutExpired("flyctl", timeout or 1)
            return -15

        def poll(self) -> int | None:
            return -15 if self.terminated else None

        def terminate(self) -> None:
            self.terminated = True

    builder = Builder()
    with (
        patch("subprocess.Popen", return_value=builder),
        pytest.raises(FlyTransportError, match="timed out"),
    ):
        build_claim_image("app", "registry.fly.io/app", "abcdef123456789", runner, factory, {})
    assert builder.terminated


def test_fly_claim_build_reuses_durable_record_without_manifest_digest(tmp_path: Path) -> None:
    from agent_factory.fly.transport import Lifecycle

    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "image-build.json").write_text(
        json.dumps(
            {
                "repository": "registry.fly.io/app",
                "tag": "claim-abcdef123456",
                "digest": "sha256:" + "a" * 64,
            }
        )
    )
    manifest = {
        "claim_id": "abcdef123456789",
        "image_repository": "registry.fly.io/app",
        "fly": {"app": "app", "token_file": str(tmp_path / "token")},
    }
    lifecycle = Lifecycle(manifest, factory)
    with patch("agent_factory.fly.transport.build_claim_image") as build:
        assert lifecycle._machine_image() == "registry.fly.io/app@sha256:" + "a" * 64  # pyright: ignore[reportPrivateUsage]
    build.assert_not_called()


def test_fly_claim_build_reuses_pinned_digest(tmp_path: Path) -> None:
    from agent_factory.fly.transport import Lifecycle

    manifest = {
        "run_id": "r",
        "claim_id": "abcdef123456789",
        "unit_key": "rep-1",
        "nonce": "n",
        "image_repository": "registry.fly.io/app",
        "image_digest": "sha256:" + "a" * 64,
        "fly": {"app": "app", "token_file": str(tmp_path / "token")},
    }
    lifecycle = Lifecycle(manifest, tmp_path)
    with patch("agent_factory.fly.transport.build_claim_image") as build:
        assert lifecycle._machine_image() == "registry.fly.io/app@sha256:" + "a" * 64  # pyright: ignore[reportPrivateUsage]
    build.assert_not_called()


def test_fly_image_repository_removes_tag_or_digest() -> None:
    from agent_factory.fly.transport import image_repository

    assert image_repository("registry.fly.io/app:base") == "registry.fly.io/app"
    assert image_repository("registry.fly.io/app@sha256:abc") == "registry.fly.io/app"


def test_fly_repository_must_match_app() -> None:
    from agent_factory.fly.backend import fly_repository_diagnostic

    assert fly_repository_diagnostic("registry.fly.io/app:base", "app").available
    result = fly_repository_diagnostic("registry.fly.io/other:base", "app")
    assert not result.available
    assert "registry.fly.io/app" in result.action


def test_claim_image_digest_recovers_from_prior_run_directory(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from agent_factory.store import Run
    from agent_factory.work_kinds.eval.handler import (
        _claim_image_digest,  # pyright: ignore[reportPrivateUsage]
    )

    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "image-build.json").write_text('{"digest":"sha256:abc"}')
    run = SimpleNamespace(progress={}, evidence_path=str(tmp_path))
    assert _claim_image_digest([cast(Run, run)]) == "sha256:abc"


def test_supervisor_copies_build_digest_into_durable_progress(tmp_path: Path) -> None:
    from agent_factory.controller import ExecutionPlan
    from agent_factory.supervisor import (
        _copy_fly_image_build,  # pyright: ignore[reportPrivateUsage]
    )

    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "image-build.json").write_text(
        '{"repository":"registry.fly.io/app","tag":"claim-abc","digest":"sha256:abc"}'
    )
    plan = ExecutionPlan((), str(tmp_path), {}, (), (), {"artifact_path": str(tmp_path)}, False)
    progress: dict[str, object] = {}
    _copy_fly_image_build(plan, progress)
    assert progress["image_build"] == {
        "repository": "registry.fly.io/app",
        "tag": "claim-abc",
        "digest": "sha256:abc",
    }


def test_presuite_failures_relaunch_same_unit_then_settle(tmp_path: Path) -> None:
    from agent_factory.controller import AttemptResult, Controller, ReportingClient
    from agent_factory.store import ClaimDraft, ClaimStore
    from agent_factory.work_kinds.eval.handler import EvalDefaults, EvalHandler

    store = ClaimStore(tmp_path / "state.sqlite3")
    handler = EvalHandler(
        EvalDefaults(
            "main",
            "main",
            {"lead": "codex:x:medium", "implementor": "codex:x:medium", "tester": "codex:x:medium"},
            False,
            1,
        )
    )
    controller = Controller(
        store, cast(ReportingClient, object()), {"eval": handler}, artifact_root=tmp_path
    )
    claim = store.create_claim(
        ClaimDraft("example/evals", 1, "I1", "P1", "eval", "fp", {"settings": {"repetitions": 1}})
    )
    first = store.reserve_run(
        claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path / "one")
    )
    controller.record_result(
        first.id,
        AttemptResult(
            "failed",
            None,
            {"reason": "build failed", "failure_stage": "pre-suite", "stage": "build"},
        ),
    )
    current = store.get_claim(claim.id)
    assert current is not None and current.lifecycle == "waiting"
    assert handler.next_unit(current, store.runs_for_claim(claim.id)) == ("rep-1", "initial")
    second = store.reserve_run(
        claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path / "two")
    )
    controller.record_result(
        second.id,
        AttemptResult(
            "failed",
            None,
            {"reason": "build failed", "failure_stage": "pre-suite", "stage": "build"},
        ),
    )
    current = store.get_claim(claim.id)
    assert current is not None and current.lifecycle == "settled"


def test_fly_guest_marks_setup_complete_and_records_cli_versions() -> None:
    from agent_factory.fly.guest import job_script

    manifest = {
        "repositories": {
            "runner": "https://github.com/example/runner",
            "skills": "https://github.com/example/skills",
        },
        "commits": {"runner": "a" * 40, "skills": "b" * 40},
    }
    script = job_script(manifest, "exec /suite/run")
    assert script.index("setup-complete") < script.index("exec /suite/run")
    assert 'for name in ("claude", "codex")' in script and '"--version"' in script
    assert "unavailable" in script


def test_fly_launcher_stage_file_marks_failure_before_suite(tmp_path: Path) -> None:
    from agent_factory.supervisor import (
        _fly_launcher_failure,  # pyright: ignore[reportPrivateUsage]
    )

    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "launch-stage.json").write_text(
        '{"failure_stage":"pre-suite","stage":"build","detail":"builder rejected Dockerfile"}'
    )
    assert _fly_launcher_failure(tmp_path, 70) == {
        "reason": "Fly launcher failed",
        "failure_stage": "pre-suite",
        "stage": "build",
        "error": "builder rejected Dockerfile",
    }


def test_guest_exit_without_setup_marker_is_presuite_even_with_zero_status(tmp_path: Path) -> None:
    from agent_factory.fly.transport import Lifecycle

    artifact = tmp_path / "artifact"
    factory = artifact / ".factory"
    factory.mkdir(parents=True)
    lifecycle = Lifecycle({"fly": {"app": "app", "token_file": str(tmp_path / "token")}}, factory)

    def collect(_remote: str, destination: Path) -> None:
        job = destination / ".factory/job/1"
        job.mkdir(parents=True)
        (job / "files.txt").write_text("")
        (job / "exit-code").write_text("0")

    with (
        patch.object(lifecycle.transport, "command"),
        patch.object(lifecycle.transport, "tar_get", side_effect=collect),
    ):
        assert lifecycle._collect(artifact, 1) == 0  # pyright: ignore[reportPrivateUsage]
    assert (factory / "launch-stage.json").is_file()


def test_fly_versions_are_loaded_from_collected_guest_evidence(tmp_path: Path) -> None:
    from agent_factory.work_kinds.eval.handler import (
        _fly_versions,  # pyright: ignore[reportPrivateUsage]
    )

    job = tmp_path / ".factory/job/1"
    job.mkdir(parents=True)
    (job / "versions.json").write_text('{"claude":"2.0","codex":"1.0"}')
    assert _fly_versions(tmp_path) == {"claude": "2.0", "codex": "1.0"}
    assert _fly_versions(tmp_path / "missing") == {"claude": "unavailable", "codex": "unavailable"}


def test_later_fly_attempt_provenance_includes_claim_build_digest(tmp_path: Path) -> None:
    from agent_factory.work_kinds.eval.handler import (
        _fly_image_build,  # pyright: ignore[reportPrivateUsage]
    )

    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "manifest.json").write_text(
        '{"image_repository":"registry.fly.io/app","image_digest":"sha256:abc"}'
    )
    assert _fly_image_build(tmp_path, {}) == {
        "repository": "registry.fly.io/app",
        "digest": "sha256:abc",
    }
    (factory / "image-build.json").write_text(
        '{"repository":"registry.fly.io/app","tag":"claim-1","digest":"sha256:def"}'
    )
    assert _fly_image_build(tmp_path, {})["digest"] == "sha256:def"


def test_fly_reconcile_knows_machine_from_run_metadata_before_record(tmp_path: Path) -> None:
    from agent_factory.fly.backend import FlyMachineBackend
    from agent_factory.store import ClaimDraft, ClaimStore
    from tests.integration.test_fly_backend import FakeClient

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "fp", {}))
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path))
    machine: dict[str, object] = {
        "id": "machine-1",
        "state": "started",
        "config": {
            "metadata": {
                "factory-owner": "agent-factory",
                "run_id": run.id,
                "deadline_epoch": "9999999999",
            }
        },
    }
    backend = FlyMachineBackend(
        client_factory=lambda app, path: FakeClient(machine),
        app="app",
        token_file=tmp_path / "token",
    )
    backend.reconcile(store)
    assert store.get_setting("runtime", "fly:unknown") == {}


def test_launcher_exit_before_machine_record_finishes_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_factory import supervisor
    from agent_factory.store import ClaimDraft, ClaimStore, Run
    from tests.integration.test_fly_supervision import (
        _fly_plan,  # pyright: ignore[reportPrivateUsage]
        _limits,  # pyright: ignore[reportPrivateUsage]
    )

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "fp", {}))
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path))
    store.mark_running(run.id, {})
    run = store.get_run(run.id)
    assert run is not None
    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "image-build.json").write_text('{"digest":"sha256:abc"}')
    statuses = iter(("alive", "missing", "missing"))

    def identity_status(_identity: object) -> str:
        return next(statuses)

    monkeypatch.setattr(supervisor, "_identity_status", identity_status)
    finished: list[str] = []

    def finish(_store: object, observed: Run, _plan: object) -> None:
        finished.append(observed.id)

    monkeypatch.setattr(
        supervisor,
        "_finish_fly_launcher_exit",
        finish,
    )
    supervisor._supervise_fly(store, run, _fly_plan(tmp_path), _limits())  # pyright: ignore[reportPrivateUsage]
    assert finished == [run.id]
    observed = store.get_run(run.id)
    assert observed is not None and observed.progress["image_build"] == {"digest": "sha256:abc"}
