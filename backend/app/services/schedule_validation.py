"""Pure schedule validation helpers shared by API and PDF code."""

from __future__ import annotations


def entry_room_names(entry) -> list[str]:
    """Return normalized, unique room names for an ORM entry or plan."""
    return list(dict.fromkeys(
        name.strip()
        for name in (getattr(entry, "room_name", None), getattr(entry, "room2_name", None))
        if name and name.strip()
    ))

def validate_period(start_date, end_date) -> None:
    if start_date and end_date and end_date < start_date:
        raise ValueError("Дата окончания раньше даты начала")
