"""Today's reminder from a private iCalendar feed.

The URL stays in ~/.config/weather-kiosk/calendar.url, one line, and is
not part of the program. A missing file or a failed fetch leaves the
white line empty, or keeps the last reminder saved for this local day.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from weather_kiosk.select import TORONTO

CALENDAR_URL_PATH = Path.home() / ".config" / "weather-kiosk" / "calendar.url"
REMINDER_CACHE_PATH = Path.home() / ".config" / "weather-kiosk" / "reminder.json"
_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS_SHORT = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclass(frozen=True)
class _Occurrence:
    summary: str
    start: datetime
    end: datetime
    all_day: bool
    description: str = ""


def calendar_url(path: Optional[Path] = None) -> Optional[str]:
    """Secret iCal address from the environment, or from the config file."""
    env = os.environ.get("WEATHER_KIOSK_CALENDAR_URL", "").strip()
    if env:
        return env
    target = path if path is not None else CALENDAR_URL_PATH
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in raw.splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            return text
    return None


def fetch_reminder(
    now: datetime,
    url: Optional[str] = None,
    cache_path: Optional[Path] = None,
    timeout: float = 20.0,
) -> str:
    """Reminder text for this local day. Failures reuse today's saved line."""
    target = url if url is not None else calendar_url()
    cache = cache_path if cache_path is not None else REMINDER_CACHE_PATH
    if not target:
        return ""
    try:
        text = reminder_text(_download(target, timeout), now)
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeError, ValueError):
        return _cached(cache, now)
    _store(cache, now, text)
    return text


def reminder_text(ics: str, now: datetime) -> str:
    """Every event for today, then every event for tomorrow."""
    local_now = now.astimezone(TORONTO)
    today = [item for item in _on_day(ics, local_now) if item.end > local_now]
    tomorrow = [
        item
        for item in _on_day(ics, local_now + timedelta(days=1))
        if not any(_same_occurrence(item, kept) for kept in today)
    ]
    today_line = _day_line(today)
    tomorrow_line = _day_line(tomorrow)
    if today_line and tomorrow_line:
        return "%s · Tomorrow: %s" % (today_line, tomorrow_line)
    if today_line:
        return today_line
    if tomorrow_line:
        return "Tomorrow: " + tomorrow_line
    return ""


def _on_day(ics: str, moment: datetime) -> List[_Occurrence]:
    found = []
    for event in _events(ics):
        found.extend(_occurrences_today(event, moment))
    return found


def _same_occurrence(item: _Occurrence, other: _Occurrence) -> bool:
    return (
        item.summary == other.summary
        and item.description == other.description
        and item.all_day == other.all_day
        and item.start == other.start
    )


def _day_line(occurrences: Sequence[_Occurrence]) -> str:
    """All-day events, then every timed event in start order."""
    allday = _all_day_line(occurrences)
    timed = [item for item in occurrences if not item.all_day]
    timed.sort(key=lambda item: (item.start, item.summary))
    timed_line = " · ".join(_event_line(item) for item in timed)
    if allday and timed_line:
        return "%s · %s" % (allday, timed_line)
    return allday or timed_line


def _download(url: str, timeout: float) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/calendar, text/plain;q=0.9",
            "User-Agent": "weather-kiosk/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8", "replace")


def _events(ics: str) -> List[dict]:
    found = []
    for block in _blocks(_unfold(ics)):
        summary = _unescape(block.get("SUMMARY", "")).strip()
        status = block.get("STATUS", "").strip().upper()
        if not summary or status == "CANCELLED":
            continue
        start = _parse_stamp(block.get("DTSTART", ""), block.get("DTSTART_PARAMS", {}))
        if start is None:
            continue
        end = _parse_stamp(block.get("DTEND", ""), block.get("DTEND_PARAMS", {}))
        start_value, all_day = start
        if all_day:
            start_dt = datetime.combine(start_value, time.min, tzinfo=TORONTO)
            if end is None:
                end_dt = start_dt + timedelta(days=1)
            else:
                end_value, _end_all_day = end
                end_dt = datetime.combine(end_value, time.min, tzinfo=TORONTO)
        else:
            start_dt = start_value.astimezone(TORONTO)
            if end is None:
                end_dt = start_dt + timedelta(hours=1)
            else:
                end_value, end_all_day = end
                if end_all_day:
                    end_dt = datetime.combine(end_value, time.min, tzinfo=TORONTO)
                else:
                    end_dt = end_value.astimezone(TORONTO)
        if end_dt <= start_dt:
            end_dt = start_dt + (timedelta(days=1) if all_day else timedelta(hours=1))
        description = " ".join(_unescape(block.get("DESCRIPTION", "")).split())
        found.append(
            {
                "summary": " ".join(summary.split()),
                "description": description,
                "start": start_dt,
                "end": end_dt,
                "all_day": all_day,
                "rrule": _rrule(block.get("RRULE", "")),
                "exdates": block.get("EXDATES", []),
            }
        )
    return found



def _all_day_line(occurrences: Sequence[_Occurrence]) -> str:
    """One title, or every overlapping all-day event on one line."""
    chosen = []
    for item in occurrences:
        if not item.all_day:
            continue
        if any(_same_all_day(item, prev) for prev in chosen):
            continue
        chosen.append(item)
    if not chosen:
        return ""
    if len(chosen) == 1:
        return _event_line(chosen[0])
    return " · ".join(_all_day_detail(item) for item in chosen)


def _same_all_day(item: _Occurrence, prev: _Occurrence) -> bool:
    return (
        item.summary == prev.summary
        and item.description == prev.description
        and item.start.astimezone(TORONTO).date() == prev.start.astimezone(TORONTO).date()
    )


def _all_day_detail(item: _Occurrence) -> str:
    """Time, date, name, and description for one all-day event."""
    local = item.start.astimezone(TORONTO)
    text = "%s %s %d: %s" % (
        _WEEKDAY_SHORT[local.weekday()],
        _MONTHS_SHORT[local.month - 1],
        local.day,
        item.summary,
    )
    if item.description:
        return "%s — %s" % (text, item.description)
    return text


def _event_line(item: _Occurrence) -> str:
    """Title, with a Toronto weekday and clock time on a timed event."""
    if item.all_day:
        text = item.summary
    else:
        local = item.start.astimezone(TORONTO)
        text = "%s %s: %s" % (_WEEKDAY_SHORT[local.weekday()], _clock(local), item.summary)
    if item.description:
        return "%s — %s" % (text, item.description)
    return text


def _clock(local: datetime) -> str:
    hour = local.hour
    suffix = "am" if hour < 12 else "pm"
    hour12 = hour % 12
    if hour12 == 0:
        hour12 = 12
    return "%d:%02d %s" % (hour12, local.minute, suffix)


def _occurrences_today(event: dict, local_now: datetime) -> List[_Occurrence]:
    day_start = datetime.combine(local_now.date(), time.min, tzinfo=TORONTO)
    day_end = day_start + timedelta(days=1)
    duration = event["end"] - event["start"]
    found = []
    for start in _starts(event, day_end):
        if _is_excluded(start, event["all_day"], event["exdates"]):
            continue
        end = start + duration
        if start < day_end and end > day_start:
            found.append(
                _Occurrence(
                    event["summary"],
                    start,
                    end,
                    event["all_day"],
                    event.get("description", ""),
                )
            )
    return found


def _starts(event: dict, day_end: datetime) -> Iterator[datetime]:
    rule = event["rrule"]
    start = event["start"]
    if not rule:
        yield start
        return
    freq = rule.get("FREQ", "").upper()
    interval = _positive(rule.get("INTERVAL"), 1)
    count = _positive(rule.get("COUNT"), 0)
    until = _until(rule.get("UNTIL", ""))
    produced = 0
    if freq == "DAILY":
        cursor = start
        step = timedelta(days=interval)
        while cursor < day_end and produced < 5000:
            if until is not None and cursor > until:
                return
            produced += 1
            if count and produced > count:
                return
            yield cursor
            cursor += step
        return
    if freq == "WEEKLY":
        byday = _byday(rule.get("BYDAY", ""), start)
        week0 = start.date() - timedelta(days=start.weekday())
        week_index = 0
        while produced < 5000:
            week_monday = week0 + timedelta(days=7 * week_index)
            if week_monday > day_end.date():
                return
            if week_index % interval == 0:
                for weekday in byday:
                    occ_date = week_monday + timedelta(days=weekday)
                    occ = datetime.combine(occ_date, start.timetz())
                    if occ < start:
                        continue
                    if until is not None and occ > until:
                        return
                    produced += 1
                    if count and produced > count:
                        return
                    if occ < day_end:
                        yield occ
            week_index += 1
            if week_index > 5200:
                return
        return
    if freq in ("MONTHLY", "YEARLY"):
        months = interval if freq == "MONTHLY" else 12 * interval
        cursor = start
        while cursor < day_end and produced < 5000:
            if until is not None and cursor > until:
                return
            produced += 1
            if count and produced > count:
                return
            yield cursor
            cursor = _add_months(cursor, months)
        return
    yield start


def _is_excluded(start: datetime, all_day: bool, exdates: Sequence) -> bool:
    local = start.astimezone(TORONTO)
    for value, ex_all_day in exdates:
        if ex_all_day:
            ex_day = value if not isinstance(value, datetime) else value.astimezone(TORONTO).date()
            if all_day and local.date() == ex_day:
                return True
            continue
        if isinstance(value, datetime) and not all_day:
            if abs((local - value.astimezone(TORONTO)).total_seconds()) < 60:
                return True
    return False


def _blocks(text: str) -> List[dict]:
    blocks = []
    current = None
    for line in text.splitlines():
        if line.strip() == "BEGIN:VEVENT":
            current = {}
            continue
        if line.strip() == "END:VEVENT":
            if current is not None:
                blocks.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        left, value = line.split(":", 1)
        name, params = _name_params(left)
        if name == "EXDATE":
            current.setdefault("EXDATES", [])
            for part in value.split(","):
                stamp = _parse_stamp(part, params)
                if stamp is not None:
                    current["EXDATES"].append(stamp)
            continue
        if name in ("DTSTART", "DTEND"):
            current[name] = value
            current[name + "_PARAMS"] = params
            continue
        if name in ("SUMMARY", "DESCRIPTION", "STATUS", "RRULE"):
            current[name] = value
    return blocks


def _name_params(left: str) -> Tuple[str, Dict[str, str]]:
    pieces = left.split(";")
    params = {}
    for piece in pieces[1:]:
        if "=" not in piece:
            continue
        key, raw = piece.split("=", 1)
        params[key.upper()] = raw.strip().strip('"')
    return pieces[0].upper(), params


def _parse_stamp(value: str, params: Dict[str, str]):
    text = value.strip()
    if not text:
        return None
    if params.get("VALUE", "").upper() == "DATE" or (len(text) == 8 and "T" not in text):
        try:
            return datetime.strptime(text[:8], "%Y%m%d").date(), True
        except ValueError:
            return None
    if text.endswith("Z") or text.endswith("z"):
        try:
            parsed = datetime.strptime(text[:-1], "%Y%m%dT%H%M%S")
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc), False
    try:
        parsed = datetime.strptime(text[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    tzname = params.get("TZID")
    try:
        tz = ZoneInfo(tzname) if tzname else TORONTO
    except (ZoneInfoNotFoundError, ValueError):
        tz = TORONTO
    return parsed.replace(tzinfo=tz), False


def _rrule(value: str) -> Dict[str, str]:
    rule = {}
    for part in value.split(";"):
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        rule[key.upper()] = raw
    return rule


def _byday(value: str, start: datetime) -> List[int]:
    days = []
    for token in value.split(","):
        name = token.strip()[-2:].upper()
        if name in _WEEKDAYS and not token.strip()[:-2]:
            days.append(_WEEKDAYS[name])
    if not days:
        return [start.weekday()]
    return sorted(set(days))


def _until(value: str) -> Optional[datetime]:
    stamp = _parse_stamp(value, {})
    if stamp is None:
        return None
    parsed, all_day = stamp
    if all_day:
        return datetime.combine(parsed, time.max, tzinfo=TORONTO)
    return parsed.astimezone(TORONTO)


def _positive(value: Optional[str], default: int) -> int:
    if not value:
        return default
    try:
        number = int(value)
    except ValueError:
        return default
    if number < 1:
        return default
    return number


def _add_months(moment: datetime, months: int) -> datetime:
    index = moment.month - 1 + months
    year = moment.year + index // 12
    month = index % 12 + 1
    day = min(moment.day, monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def _unfold(text: str) -> str:
    return text.replace("\r\n ", "").replace("\r\n\t", "").replace("\n ", "").replace("\n\t", "")


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", " ")
        .replace("\\N", " ")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _cached(path: Path, now: datetime) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    if payload.get("date") != now.astimezone(TORONTO).date().isoformat():
        return ""
    text = payload.get("text")
    return text if isinstance(text, str) else ""


def _store(path: Path, now: datetime, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps({"date": now.astimezone(TORONTO).date().isoformat(), "text": text}),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError:
        return
