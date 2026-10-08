"""Errors shared by and-scene input resolution and execution."""


class WorktreeError(RuntimeError):
    """A pinned worktree could not be prepared or released safely."""


class ReadinessError(RuntimeError):
    """The selected pinned suite cannot run its approved contract."""


class RecoveryStateError(RuntimeError):
    """Saved suite evidence does not permit a safe recovery invocation."""
