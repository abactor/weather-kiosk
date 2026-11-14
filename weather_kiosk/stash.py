"""Remember hourly forecasts for the hour already in progress.

Environment Canada drops that hour from the live feed. The saved hour
can fill a gap in the station observation: sky words, and humidity or
wind when the station did not report them. A missing or unreadable
file is ignored.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Sequence

from weather_kiosk.fetch import CurrentConditions
from weather_kiosk.select import TORONTO


@dataclass(frozen=True)
class StashedHour:
    """One hourly forecast kept after the feed has dropped it."""

    condition: str
    precip_percent: int
    temperature_c: float
    humidity_percent: Optional[float] = None
    wind_kmh: Optional[float] = None


def remember_hourly(path: Path, records: Sequence, now: datetime) -> None:
    """Merge hourly records into path. Disk errors leave the page running."""
    try:
        stored = _read(path)
        for record in records:
            stamp = getattr(record, "timestamp", None)
            if not isinstance(stamp, datetime):
                continue
            condition = getattr(record, "condition", None)
            precip = getattr(record, "precip_percent", None)
            temp = getattr(record, "temperature_c", None)
            if not isinstance(condition, str):
                continue
            if isinstance(precip, bool) or not isinstance(precip, (int, float)):
                continue
            if isinstance(temp, bool) or not isinstance(temp, (int, float)):
                continue
            entry = {
                "condition": condition,
                "precip_percent": int(round(float(precip))),
                "temperature_c": float(temp),
            }
            humidity = _number(getattr(record, "humidity_percent", None))
            wind = _number(getattr(record, "wind_kmh", None))
            if humidity is not None:
                entry["humidity_percent"] = humidity
            if wind is not None:
                entry["wind_kmh"] = wind
            stored[_hour_key(stamp.astimezone(TORONTO))] = entry
        local_now = now.astimezone(TORONTO)
        kept = {}
        for key, value in stored.items():
            parsed = _parse_key(key)
            if parsed is None:
                continue
            if parsed < local_now - timedelta(hours=36):
                continue
            if parsed > local_now + timedelta(hours=48):
                continue
            kept[key] = value
        _write(Path(path), kept)
    except OSError:
        return


def recall_hour(path: Path, now: datetime) -> Optional[StashedHour]:
    """Return the saved forecast for the hour containing now, or None."""
    try:
        stored = _read(Path(path))
    except OSError:
        return None
    value = stored.get(_hour_key(now.astimezone(TORONTO)))
    if not isinstance(value, dict):
        return None
    condition = value.get("condition")
    precip = value.get("precip_percent")
    temp = value.get("temperature_c")
    if not isinstance(condition, str):
        return None
    if isinstance(precip, bool) or not isinstance(precip, (int, float)):
        return None
    if isinstance(temp, bool) or not isinstance(temp, (int, float)):
        return None
    return StashedHour(
        condition=condition,
        precip_percent=int(round(float(precip))),
        temperature_c=float(temp),
        humidity_percent=_number(value.get("humidity_percent")),
        wind_kmh=_number(value.get("wind_kmh")),
    )


def augment_observation(
    observed: CurrentConditions,
    saved: Optional[StashedHour],
) -> CurrentConditions:
    """Keep station temperature, precipitation, humidity, and wind.

    The saved forecast supplies a condition, humidity, or wind speed
    only when the station left that field unset. Its probability of
    precipitation is not copied onto the observation. A missing forecast
    leaves the gaps unset.
    """
    condition = observed.condition.strip()
    humidity = observed.humidity_percent
    wind = observed.wind_kmh
    if saved is not None:
        if not condition and saved.condition.strip():
            condition = saved.condition.strip()
        if humidity is None and saved.humidity_percent is not None:
            humidity = saved.humidity_percent
        if wind is None and saved.wind_kmh is not None:
            wind = saved.wind_kmh
    return CurrentConditions(
        temperature_c=observed.temperature_c,
        condition=condition,
        precip_mm=observed.precip_mm,
        humidity_percent=humidity,
        wind_kmh=wind,
    )


def _number(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _hour_key(local: datetime) -> str:
    return "%04d-%02d-%02dT%02d" % (local.year, local.month, local.day, local.hour)


def _parse_key(key: str) -> Optional[datetime]:
    try:
        parsed = datetime.strptime(str(key), "%Y-%m-%dT%H")
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=TORONTO)


def _read(path: Path) -> Dict[str, dict]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    hours = payload.get("hours") if isinstance(payload, dict) else None
    if not isinstance(hours, dict):
        return {}
    cleaned = {}
    for key, value in hours.items():
        if isinstance(key, str) and isinstance(value, dict):
            cleaned[key] = value
    return cleaned


def _write(path: Path, hours: Dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps({"hours": hours}, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)
