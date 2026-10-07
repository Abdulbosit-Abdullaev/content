"""Posting slot planning in Tashkent time, and short time labels."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def next_free_slot(
    now: datetime, post_times: Sequence[time], taken: Iterable[datetime], tz: ZoneInfo, horizon_days: int = 60
) -> datetime:
    """Earliest posting time after `now` that no approved video holds yet (returned in UTC)."""
    taken_utc = {t.astimezone(UTC) for t in taken}
    local_now = now.astimezone(tz)
    for offset in range(horizon_days):
        day = local_now.date() + timedelta(days=offset)
        for slot_time in sorted(post_times):
            slot = datetime.combine(day, slot_time, tzinfo=tz)
            if slot > local_now and slot.astimezone(UTC) not in taken_utc:
                return slot.astimezone(UTC)
    raise RuntimeError(f"no free posting slot in the next {horizon_days} days")


def when_label(slot: datetime, now: datetime, tz: ZoneInfo) -> str:
    local = slot.astimezone(tz)
    if local.date() == now.astimezone(tz).date():
        return local.strftime("%H:%M")
    return local.strftime("%d.%m %H:%M")
