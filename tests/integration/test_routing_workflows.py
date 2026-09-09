from __future__ import annotations

from pathlib import Path


def test_reusable_workflow_uses_explicit_revision_and_app_secret() -> None:
    workflow = Path(".github/workflows/route-work.yml").read_text(encoding="utf-8")

    assert "workflow_call:" in workflow
    assert "factory-revision:" in workflow
    assert "factory-app-private-key:" in workflow
    assert "actions/create-github-app-token" in workflow
    assert "FACTORY_APP_PRIVATE_KEY" not in workflow
    assert "python -m agent_factory.routing" in workflow
    assert "github.event.pull_request.head" not in workflow
