"""Choose Toronto forecast slots for the panel.

Samples are hourly from 06:00 through 22:00, including odd hours.
The leftmost sample is the hour it is now. At 16:51 that is 16:00.
23:00 through 05:00 is never shown. After 22:00 the next sample is
06:00 the next day. At 22:30 the current hour is still 22:00. At 23:30
the present sample is 06:00 the next morning.

The default call returns seven hours. count=None returns every later
awake hour the forecast still covers.

This module does not read the clock, the network, or the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo("America/Toronto")

# Hourly samples from 06:00 through 22:00. After 22:00 the next is 06:00.
AWAKE_HOURS: Tuple[int, ...] = tuple(range(6, 23))
WINDOW_SLOTS = 7
STEP_HOURS = 1


@dataclass(frozen=True)
class ForecastSlot:
    """One panel row sampled from a single hourly record."""

    local_time: datetime
    temperature_c: float
    condition: str
    precip_percent: Optional[int]
    precip_mm: Optional[float] = None
    humidity_percent: Optional[float] = None
    wind_kmh: Optional[float] = None

    @property
    def label_date(self) -> date:
        return self.local_time.date()


def forecast_day(now: datetime) -> date:
    """Calendar date of the first slot in the sliding window."""
    return _first_awake(_as_toronto(now)).date()


def select_slots(
    now: datetime,
    hourly: Sequence,
    count: Optional[int] = WINDOW_SLOTS,
    current: object = None,
) -> List[ForecastSlot]:
    """Return hourly samples, with the current observation on the left.

    Each hourly record must expose timestamp (timezone-aware datetime),
    temperature_c, condition, and precip_percent. A clock slot with no
    matching hourly timestamp is omitted. Temperatures are not interpolated.
    count=None keeps every later awake hour covered by the records.
    When current is set, the leftmost slot is that observation at the
    clock time in now, and hourly samples at or before now are left out.
    """
    local_now = _as_toronto(now)
    if count is None:
        if hourly:
            latest = max(
                _record_timestamp(record).astimezone(TORONTO) for record in hourly
            )
        else:
            latest = local_now
        slot_times = _times_through_forecast(local_now, latest)
    else:
        if count < 1:
            raise ValueError("count must be at least 1")
        slot_times = _window_times(local_now, count)
    by_clock = _index_by_local_clock(hourly)
    selected: List[ForecastSlot] = []
    for slot_time in slot_times:
        if current is not None and slot_time <= local_now:
            continue
        record = by_clock.get((slot_time.date(), slot_time.hour, 0))
        if record is None:
            continue
        selected.append(
            ForecastSlot(
                local_time=slot_time,
                temperature_c=_temperature(record),
                condition=_condition(record),
                precip_percent=_precip(record),
            )
        )
    if current is not None:
        selected.insert(0, _current_slot(local_now, current))
        if count is not None:
            selected = selected[:count]
    return selected


def _current_slot(local_now: datetime, current: object) -> ForecastSlot:
    """Observation at the clock time, not the next forecast hour.

    Temperature, precipitation, humidity, and wind come from the station.
    The current column does not show a probability of precipitation.
    """
    temperature = getattr(current, "temperature_c", None)
    condition = getattr(current, "condition", None)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise ValueError("current temperature_c must be numeric")
    if not isinstance(condition, str):
        raise ValueError("current condition must be a string")
    precip_mm = _optional_measure(current, "precip_mm")
    humidity = _optional_measure(current, "humidity_percent")
    wind = _optional_measure(current, "wind_kmh")
    stamped = local_now.replace(second=0, microsecond=0)
    return ForecastSlot(
        local_time=stamped,
        temperature_c=float(temperature),
        condition=condition,
        precip_percent=None,
        precip_mm=precip_mm,
        humidity_percent=humidity,
        wind_kmh=wind,
    )


def _optional_measure(current: object, name: str) -> Optional[float]:
    value = getattr(current, name, None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("current %s must be numeric" % name)
    return float(value)


def _window_times(local_now: datetime, count: int) -> List[datetime]:
    """count hourly clock times, wrapping 22:00 to 06:00."""
    cursor = _first_awake(local_now)
    times = [cursor]
    while len(times) < count:
        cursor = _next_awake(cursor)
        times.append(cursor)
    return times


def _times_through_forecast(local_now: datetime, until: datetime) -> List[datetime]:
    """Awake hours from the current hour through the last forecast time."""
    cursor = _first_awake(local_now)
    times: List[datetime] = []
    for _ in range(48):
        if cursor > until:
            break
        times.append(cursor)
        cursor = _next_awake(cursor)
    if not times:
        times.append(_first_awake(local_now))
    return times


def _first_awake(local_now: datetime) -> datetime:
    """Hour containing local_now when that hour is 06:00 through 22:00."""
    hour = local_now.hour
    if 6 <= hour <= 22:
        return datetime.combine(local_now.date(), time(hour, 0), tzinfo=TORONTO)
    if hour < 6:
        return datetime.combine(local_now.date(), time(6, 0), tzinfo=TORONTO)
    return datetime.combine(local_now.date() + timedelta(days=1), time(6, 0), tzinfo=TORONTO)


def _next_awake(slot_time: datetime) -> datetime:
    """Next hour. 22:00 is followed by 06:00 the next day."""
    if slot_time.hour >= 22:
        return datetime.combine(
            slot_time.date() + timedelta(days=1), time(6, 0), tzinfo=TORONTO
        )
    return datetime.combine(
        slot_time.date(), time(slot_time.hour + STEP_HOURS, 0), tzinfo=TORONTO
    )


def _as_toronto(now: datetime) -> datetime:
    if not isinstance(now, datetime):
        raise ValueError("now must be a datetime")
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(TORONTO)


def _index_by_local_clock(hourly: Sequence) -> Dict[Tuple[date, int, int], object]:
    indexed: Dict[Tuple[date, int, int], object] = {}
    for record in hourly:
        stamp = _record_timestamp(record)
        local_stamp = stamp.astimezone(TORONTO)
        key = (local_stamp.date(), local_stamp.hour, local_stamp.minute)
        # The first record for a local clock time is the one we sample.
        if key not in indexed:
            indexed[key] = record
    return indexed


def _record_timestamp(record: object) -> datetime:
    stamp = getattr(record, "timestamp", None)
    if not isinstance(stamp, datetime):
        raise ValueError("hourly timestamp must be a datetime")
    if stamp.tzinfo is None or stamp.tzinfo.utcoffset(stamp) is None:
        raise ValueError("hourly timestamp must be timezone-aware")
    return stamp


def _temperature(record: object) -> float:
    value = getattr(record, "temperature_c", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("temperature_c must be numeric")
    return float(value)


def _condition(record: object) -> str:
    text = getattr(record, "condition", None)
    if not isinstance(text, str):
        raise ValueError("condition must be a string")
    return text


def _precip(record: object) -> int:
    value = getattr(record, "precip_percent", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("precip_percent must be numeric")
    return int(round(float(value)))
