"""Removal of the per-run sandbox images a claim's attempts were built under."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from typing import cast

from agent_factory.store import ClaimStore


def run_image_tags(store: ClaimStore, claim_id: str) -> list[str]:
    """Every image tag recorded in the claim's run plans, oldest first, without duplicates."""
    tags: list[str] = []
    for run in store.runs_for_claim(claim_id):
        hints = run.plan.get("ownership_hints")
        tag = (
            cast(Mapping[str, object], hints).get("image_tag")
            if isinstance(hints, Mapping)
            else None
        )
        if isinstance(tag, str) and tag and tag not in tags:
            tags.append(tag)
    return tags


def remove_images(tags: list[str], *, docker: str = "docker") -> dict[str, str]:
    """`docker rmi` each tag, tolerating already-removed images; return failures by tag."""
    errors: dict[str, str] = {}
    for tag in tags:
        try:
            result = subprocess.run(
                [docker, "rmi", tag], capture_output=True, text=True, check=False, timeout=120
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            errors[f"image:{tag}"] = f"cannot run docker: {error}"
            continue
        if result.returncode != 0 and "No such image" not in result.stderr:
            errors[f"image:{tag}"] = (
                result.stderr.strip() or f"docker rmi exited {result.returncode}"
            )
    return errors
