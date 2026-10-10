"""Priority vocabulary shared by board ranking and durable lane occupancy."""

LANES = ("urgent", "high", "medium", "low")


def lane_for(priority: str | None) -> str:
    value = priority.strip().lower() if priority is not None else "low"
    return value if value in LANES else "low"


def rank(lane: str) -> int:
    return LANES.index(lane)


def priority_rank(name: str | None) -> int:
    if not name:
        return len(LANES)
    try:
        return LANES.index(name.strip().lower())
    except ValueError:
        return len(LANES)
