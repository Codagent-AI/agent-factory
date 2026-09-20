"""Reusable local Fly test doubles; they never make network requests."""

from tests.fixtures.fly.api import FakeMachinesApi
from tests.fixtures.fly.flyctl import write_flyctl

__all__ = ["FakeMachinesApi", "write_flyctl"]
