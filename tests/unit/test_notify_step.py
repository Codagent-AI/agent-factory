"""Acceptance F-2: a broken notify submodule must not escape the cycle."""

from __future__ import annotations

import logging
import sys
from types import SimpleNamespace
from typing import Any

import pytest

import agent_factory.notify as notify
from agent_factory import runtime


def _break_deliver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(notify, "deliver", raising=False)
    monkeypatch.setitem(sys.modules, "agent_factory.notify.deliver", None)


def test_step_logs_a_submodule_import_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _break_deliver(monkeypatch)
    shared: Any = SimpleNamespace(notify=SimpleNamespace(enabled=True))
    with caplog.at_level(logging.ERROR, logger="agent_factory.notify"):
        notify.step(None, None, shared, None, [])  # type: ignore[arg-type]
    assert "notify import failed" in caplog.text


def test_watch_finally_survives_a_broken_notify_submodule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _break_deliver(monkeypatch)
    watched: list[str] = []

    def watch_step(*_args: object) -> None:
        watched.append("watch")

    def begin(*_args: object) -> None:
        return None

    monkeypatch.setattr(runtime.watch, "step", watch_step)
    monkeypatch.setattr(runtime.notify, "begin", begin)
    shared: Any = SimpleNamespace(notify=SimpleNamespace(enabled=True))
    with runtime._watch_finally(None, None, shared, None, None, None) as view:  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        view.cards = []
    assert watched == ["watch"]
