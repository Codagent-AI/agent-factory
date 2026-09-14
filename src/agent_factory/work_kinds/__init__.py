"""Static work-kind registrations shipped by the factory."""

from __future__ import annotations

from collections.abc import Mapping

from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.work_kinds.base import WorkKindHandler


def handlers(shared: SharedConfig, local: LocalConfig) -> Mapping[str, WorkKindHandler]:
    from agent_factory.work_kinds.eval.handler import EvalHandler
    from agent_factory.work_kinds.fix.handler import FixHandler

    return {
        "eval": EvalHandler.from_config(shared, local),
        "fix": FixHandler.from_config(shared, local),
    }
