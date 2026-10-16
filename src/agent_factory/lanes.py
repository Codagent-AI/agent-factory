"""Priority vocabulary shared by board ranking and durable lane occupancy."""

PRIORITIES = ("urgent", "high", "medium", "low")
LANES = ("high", "medium", "low")
KINDS = ("eval", "fix", "feature", "task")


def lane_for(priority: str | None) -> str:
    """Urgent shares the High lane; unset and unknown values use the Low lane."""
    value = priority.strip().lower() if priority is not None else "low"
    if value == "urgent":
        return "high"
    return value if value in LANES else "low"


def rank(lane: str) -> int:
    return LANES.index(lane)


def priority_rank(name: str | None) -> int:
    if not name:
        return len(PRIORITIES)
    try:
        return PRIORITIES.index(name.strip().lower())
    except ValueError:
        return len(PRIORITIES)
