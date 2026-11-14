"""TTC trip updates for a configured stop and routes.

The binary feed is GTFS-realtime. The static surface schedule says whether a
route ever calls at that stop. A failed live feed keeps the last good board
for five minutes.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union

from weather_kiosk.select import TORONTO

TRIPS_URL = "https://bustime.ttc.ca/gtfsrt/trips"
# City of Toronto open data: Surface Routes and Schedules for BusTime.
# Static GTFS for buses and streetcars. This is the schedule that matches
# the BusTime trip-update feed, not that live feed itself.
SURFACE_GTFS_URL = (
    "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/"
    "bd4809dd-e289-4de8-bbde-c5c00dafbf4f/resource/"
    "28514055-d011-4ed7-8bb0-97961dfe2b66/download/surfacegtfs.zip"
)
TRANSIT_CONFIG_PATH = Path.home() / ".config" / "weather-kiosk" / "transit.conf"
BOARD_CACHE_PATH = Path.home() / ".config" / "weather-kiosk" / "transit-board.json"
SCHEDULE_DIR = Path.home() / ".config" / "weather-kiosk" / "surface-gtfs"
_HOLD = timedelta(minutes=5)
_SCHEDULE_AGE = timedelta(days=14)
_SCHEDULE_FILES = ("routes.txt", "trips.txt", "stop_times.txt", "stops.txt")

# TripDescriptor.ScheduleRelationship values worth showing.
_KEEP_TRIP = {0, 1, 2, 5, 6, 8}
# StopTimeUpdate.ScheduleRelationship values with no usable time.
_DROP_STOP = {1, 2}

Field = Union[int, bytes]


class TransitError(Exception):
    """The trip feed could not be read."""


@dataclass(frozen=True)
class TransitConfig:
    """Stops and the route numbers to watch, in file order.

    `stops` is `(stop code or id, routes)` for each stop. `stop_id` and
    `routes` repeat the first stop so a one-stop file keeps its old shape.
    """

    stop_id: str
    routes: Tuple[str, ...]
    stops: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        if not self.stops:
            object.__setattr__(self, "stops", ((self.stop_id, tuple(self.routes)),))


@dataclass(frozen=True)
class RouteBoard:
    """Up to two future arrivals for one configured route."""

    route_id: str
    arrivals: Tuple[datetime, ...]
    state: str


def load_config(path: Optional[Path] = None) -> Optional[TransitConfig]:
    """Read the transit file. Missing or incomplete config means no board."""
    target = path if path is not None else TRANSIT_CONFIG_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_config(text)


def parse_config(text: str) -> Optional[TransitConfig]:
    """Each `stop` line, then the `route` lines that belong to it.

    Comments and blanks are ignored. A route before the first stop is skipped.
    The same stop later in the file adds routes to the earlier group.
    A route value may be one id or a comma-separated list.
    """
    groups = []
    current = None
    for line in text.splitlines():
        raw = line.split("#", 1)[0].strip()
        if not raw:
            continue
        parts = raw.split(None, 1)
        if len(parts) != 2:
            continue
        key = parts[0].lower()
        value = parts[1].strip()
        if key == "stop":
            if any(ch.isspace() for ch in value):
                continue
            current = None
            for group in groups:
                if group[0] == value:
                    current = group
                    break
            if current is None:
                current = [value, []]
                groups.append(current)
        elif key == "route" and current is not None:
            for token in value.split(","):
                route = token.strip().upper()
                if route and not any(ch.isspace() for ch in route) and route not in current[1]:
                    current[1].append(route)
    stops = tuple((stop_id, tuple(routes)) for stop_id, routes in groups if routes)
    if not stops:
        return None
    return TransitConfig(stops[0][0], stops[0][1], stops)


def fetch_trips(timeout: float = 20.0, url: str = TRIPS_URL) -> bytes:
    """Download the public trip-update feed. No API key."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/x-protobuf",
            "User-Agent": "weather-kiosk/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TransitError(str(exc)) from exc


def parse_trip_updates(payload: bytes) -> List[Tuple[str, List[Tuple[str, int]]]]:
    """Route id and (stop id, unix time) pairs from a FeedMessage."""
    if payload[:2] == b"\x1f\x8b":
        try:
            payload = gzip.decompress(payload)
        except OSError as exc:
            raise TransitError("trip feed is not a readable protobuf") from exc
    found = []
    try:
        for field, value in _fields(payload):
            if field != 2 or not isinstance(value, bytes):
                continue
            trip = _entity_trip(value)
            if trip is not None:
                found.append(trip)
    except (IndexError, ValueError) as exc:
        raise TransitError("trip feed is not a readable protobuf") from exc
    return found


def route_matches(configured: str, feed_id: str) -> bool:
    """True for the same id. A bare number also matches one trailing A-Z letter.

    11 matches 11, 11A, 11B, and 11C. It does not match 111 or 11AB.
    11A matches 11A only. It does not match 11, 11B, or 11AA.
    """
    if feed_id == configured:
        return True
    if not configured.isdigit():
        return False
    if len(feed_id) != len(configured) + 1 or not feed_id.startswith(configured):
        return False
    suffix = feed_id[-1]
    return "A" <= suffix <= "Z"


def board_routes(
    config: TransitConfig,
    updates: Sequence[Tuple[str, Sequence[Tuple[str, int]]]],
    now: datetime,
    serving: Optional[Iterable[str]] = None,
) -> List[RouteBoard]:
    """Next two arrivals per configured route.

    `serving` is the set of route ids known to call at this stop. Without it,
    a route with no future time is "no_bus" rather than "not_at_stop".
    A configured number also accepts that number plus one trailing letter.
    """
    known = None if serving is None else set(serving)
    cutoff = int(now.timestamp())
    by_route = {route: [] for route in config.routes}
    for feed_id, stops in updates:
        matched = [route for route in config.routes if route_matches(route, feed_id)]
        if not matched:
            continue
        for stop_id, when in stops:
            if stop_id == config.stop_id and when > cutoff:
                for route in matched:
                    by_route[route].append((when, feed_id))
    boards = []
    for route_id in config.routes:
        if known is not None and not any(route_matches(route_id, item) for item in known):
            boards.append(RouteBoard(route_id, (), "not_at_stop"))
            continue
        chosen = _next_two(by_route[route_id])
        if not chosen:
            boards.append(RouteBoard(route_id, (), "no_bus"))
            continue
        moments = tuple(
            datetime.fromtimestamp(when, timezone.utc).astimezone(TORONTO)
            for when, _feed_id in chosen
        )
        boards.append(RouteBoard(chosen[0][1], moments, "times"))
    return boards



def _next_two(pairs):
    """Earliest two distinct times, each tagged with the feed id that produced it."""
    chosen = []
    seen = set()
    for when, feed_id in sorted(pairs):
        if when in seen:
            continue
        seen.add(when)
        chosen.append((when, feed_id))
        if len(chosen) == 2:
            break
    return chosen


def format_arrival(moment: datetime) -> str:
    """Toronto clock, matching the rest of the panel."""
    local = moment.astimezone(TORONTO)
    hour = local.hour
    suffix = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    return "%d:%02d %s" % (hour12, local.minute, suffix)



def current_board(
    now: datetime,
    config_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    schedule_dir: Optional[Path] = None,
    download: bool = True,
    fetch: Optional[Callable[[], bytes]] = None,
    timeout: float = 20.0,
) -> List[RouteBoard]:
    """Live board for the configured stop. Feed errors reuse a recent board."""
    config = load_config(config_path)
    if config is None:
        return []
    cache = cache_path if cache_path is not None else BOARD_CACHE_PATH
    directory = schedule_dir if schedule_dir is not None else SCHEDULE_DIR
    if download:
        directory = ensure_schedule(directory, now=now)
    try:
        payload = fetch() if fetch is not None else fetch_trips(timeout=timeout)
        updates = parse_trip_updates(payload)
    except TransitError:
        return recall_board(cache, config, now)
    board = []
    for stop_code, routes in config.stops:
        stop_id = resolve_stop_id(directory, stop_code)
        serving = routes_serving(directory, stop_id) if directory is not None else None
        board.extend(
            board_routes(TransitConfig(stop_id, routes), updates, now, serving)
        )
    remember_board(cache, config, now, board)
    return board


def ensure_schedule(
    directory: Optional[Path] = None,
    url: str = SURFACE_GTFS_URL,
    now: Optional[datetime] = None,
    timeout: float = 60.0,
) -> Optional[Path]:
    """Keep a local surface schedule. Refresh when it is older than 14 days."""
    target = directory if directory is not None else SCHEDULE_DIR
    moment = now if now is not None else datetime.now(TORONTO)
    if _schedule_fresh(target, moment):
        return target
    try:
        install_schedule(target, _download_bytes(url, timeout), moment)
    except (TransitError, OSError):
        if (target / "stop_times.txt").is_file():
            return target
        return None
    return target


def install_schedule(directory: Path, blob: bytes, now: datetime) -> None:
    """Unpack routes, trips, stop times, and stops from a GTFS zip."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise TransitError("schedule zip is unreadable") from exc
    names = {}
    for name in archive.namelist():
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        if base in _SCHEDULE_FILES and base not in names:
            names[base] = name
    if any(base not in names for base in _SCHEDULE_FILES):
        raise TransitError("schedule zip is missing tables")
    incoming = directory / "incoming"
    if incoming.exists():
        shutil.rmtree(incoming)
    incoming.mkdir(parents=True)
    try:
        for base in _SCHEDULE_FILES:
            (incoming / base).write_bytes(archive.read(names[base]))
        directory.mkdir(parents=True, exist_ok=True)
        for base in _SCHEDULE_FILES:
            os.replace(str(incoming / base), str(directory / base))
        (directory / "fetched-at").write_text(now.isoformat(), encoding="utf-8")
    finally:
        if incoming.exists():
            shutil.rmtree(incoming, ignore_errors=True)


def stop_code_table(directory: Optional[Path]) -> dict:
    """Map each public stop code and each GTFS stop id to that stop id.

    The table is saved beside the schedule and rebuilt when the schedule is
    newer. A stop id wins if it collides with some other stop's public code.
    """
    if directory is None:
        return {}
    path = directory / "stops.txt"
    if not path.is_file():
        return {}
    cache = directory / "stop-codes.json"
    stamp = directory / "fetched-at"
    if cache.is_file() and (not stamp.is_file() or cache.stat().st_mtime >= stamp.stat().st_mtime):
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            saved = None
        if isinstance(saved, dict) and all(
            isinstance(key, str) and isinstance(value, str) for key, value in saved.items()
        ):
            return saved
    table = {}
    ids = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            stop_id = (row.get("stop_id") or "").strip()
            code = (row.get("stop_code") or "").strip()
            if code and stop_id and code not in table:
                table[code] = stop_id
            if stop_id:
                ids.append(stop_id)
    for stop_id in ids:
        table[stop_id] = stop_id
    try:
        cache.write_text(json.dumps(table, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    return table


def resolve_stop_id(directory: Optional[Path], configured: str) -> str:
    """Return the GTFS stop_id for a public stop code or an existing stop_id."""
    return stop_code_table(directory).get(configured, configured)


def routes_serving(directory: Optional[Path], stop_id: str) -> Optional[Set[str]]:
    """Route ids and public numbers that call at this stop. None if unknown."""
    if directory is None or not (directory / "stop_times.txt").is_file():
        return None
    cache = directory / ("serving-%s.json" % stop_id)
    stamp = directory / "fetched-at"
    if cache.is_file() and (not stamp.is_file() or cache.stat().st_mtime >= stamp.stat().st_mtime):
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            saved = None
        if isinstance(saved, list):
            return set(str(item) for item in saved)
    found = _scan_serving(directory, stop_id)
    try:
        cache.write_text(json.dumps(sorted(found)), encoding="utf-8")
    except OSError:
        pass
    return found


def remember_board(
    path: Path,
    config: TransitConfig,
    now: datetime,
    board: Sequence[RouteBoard],
) -> None:
    """Store a board that came from a successful feed read."""
    payload = {
        "saved_at": now.isoformat(),
        "stop_id": config.stop_id,
        "routes": list(config.routes),
        "stops": [
            {"stop_id": stop_id, "routes": list(routes)}
            for stop_id, routes in config.stops
        ],
        "board": [
            {
                "route_id": item.route_id,
                "state": item.state,
                "arrivals": [moment.isoformat() for moment in item.arrivals],
            }
            for item in board
        ],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(str(temporary), str(path))
    except OSError:
        return


def _same_stops(payload: dict, config: TransitConfig) -> bool:
    """True when the saved board was for this same list of stops and routes."""
    saved = payload.get("stops")
    if isinstance(saved, list):
        got = []
        for item in saved:
            if not isinstance(item, dict) or not isinstance(item.get("stop_id"), str):
                return False
            routes = item.get("routes") or ()
            if not isinstance(routes, list) or not all(isinstance(route, str) for route in routes):
                return False
            got.append((item["stop_id"], tuple(routes)))
        return tuple(got) == config.stops
    return (
        len(config.stops) == 1
        and payload.get("stop_id") == config.stop_id
        and tuple(payload.get("routes") or ()) == config.routes
    )


def recall_board(path: Path, config: TransitConfig, now: datetime) -> List[RouteBoard]:
    """Last board for this stop and route list, if it is still within five minutes."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    if not isinstance(payload, dict):
        return []
    if not _same_stops(payload, config):
        return []
    saved = _saved_at(payload.get("saved_at"))
    if saved is None or now - saved > _HOLD:
        return []
    board = []
    for item in payload.get("board") or []:
        if not isinstance(item, dict):
            continue
        route_id = item.get("route_id")
        state = item.get("state")
        if not isinstance(route_id, str) or state not in ("times", "no_bus", "not_at_stop"):
            continue
        arrivals = []
        for text in item.get("arrivals") or []:
            moment = _saved_at(text)
            if moment is not None:
                arrivals.append(moment.astimezone(TORONTO))
        board.append(RouteBoard(route_id, tuple(arrivals), state))
    return board


def _schedule_fresh(directory: Path, now: datetime) -> bool:
    stamp = directory / "fetched-at"
    if not stamp.is_file() or any(not (directory / name).is_file() for name in _SCHEDULE_FILES):
        return False
    saved = _saved_at(stamp.read_text(encoding="utf-8").strip())
    if saved is None:
        return False
    return now - saved <= _SCHEDULE_AGE


def _download_bytes(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "weather-kiosk/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TransitError(str(exc)) from exc


def _scan_serving(directory: Path, stop_id: str) -> Set[str]:
    route_by_trip = {}
    with (directory / "trips.txt").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            trip_id = (row.get("trip_id") or "").strip()
            route_id = (row.get("route_id") or "").strip()
            if trip_id and route_id:
                route_by_trip[trip_id] = route_id
    short_name = {}
    routes_path = directory / "routes.txt"
    if routes_path.is_file():
        with routes_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                route_id = (row.get("route_id") or "").strip()
                short = (row.get("route_short_name") or "").strip()
                if route_id and short:
                    short_name[route_id] = short
    found = set()
    with (directory / "stop_times.txt").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("stop_id") or "").strip() != stop_id:
                continue
            route_id = route_by_trip.get((row.get("trip_id") or "").strip())
            if not route_id:
                continue
            found.add(route_id)
            short = short_name.get(route_id)
            if short:
                found.add(short)
    return found


def _saved_at(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        saved = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if saved.tzinfo is None:
        saved = saved.replace(tzinfo=TORONTO)
    return saved


def _entity_trip(entity: bytes) -> Optional[Tuple[str, List[Tuple[str, int]]]]:
    deleted = False
    update = None
    for field, value in _fields(entity):
        if field == 2 and value:
            deleted = True
        elif field == 3 and isinstance(value, bytes):
            update = value
    if deleted or update is None:
        return None
    route_id = ""
    relation = 0
    stops = []
    for field, value in _fields(update):
        if field == 1 and isinstance(value, bytes):
            route_id, relation = _descriptor(value)
        elif field == 2 and isinstance(value, bytes):
            stop = _stop_time(value)
            if stop is not None:
                stops.append(stop)
    if not route_id or relation not in _KEEP_TRIP:
        return None
    return route_id, stops


def _descriptor(message: bytes) -> Tuple[str, int]:
    route_id = ""
    relation = 0
    for field, value in _fields(message):
        if field == 5 and isinstance(value, bytes):
            route_id = value.decode("utf-8", "replace")
        elif field == 4 and isinstance(value, int):
            relation = value
    return route_id, relation


def _stop_time(message: bytes) -> Optional[Tuple[str, int]]:
    stop_id = ""
    relation = 0
    arrival = None
    departure = None
    for field, value in _fields(message):
        if field == 4 and isinstance(value, bytes):
            stop_id = value.decode("utf-8", "replace")
        elif field == 5 and isinstance(value, int):
            relation = value
        elif field == 2 and isinstance(value, bytes):
            arrival = _event_time(value)
        elif field == 3 and isinstance(value, bytes):
            departure = _event_time(value)
    if relation in _DROP_STOP or not stop_id:
        return None
    when = arrival if arrival is not None else departure
    if when is None:
        return None
    return stop_id, when


def _event_time(message: bytes) -> Optional[int]:
    for field, value in _fields(message):
        if field == 2 and isinstance(value, int):
            return _signed(value)
    return None


def _signed(value: int) -> int:
    if value >= 2 ** 63:
        return value - 2 ** 64
    return value


def _fields(payload: bytes) -> Iterator[Tuple[int, Field]]:
    index = 0
    size = len(payload)
    while index < size:
        key, index = _varint(payload, index)
        field = key >> 3
        wire = key & 7
        if wire == 0:
            value, index = _varint(payload, index)
            yield field, value
        elif wire == 2:
            length, index = _varint(payload, index)
            end = index + length
            if end > size:
                raise ValueError("truncated length")
            yield field, payload[index:end]
            index = end
        elif wire == 5:
            index += 4
        elif wire == 1:
            index += 8
        else:
            raise ValueError("unsupported wire type")
        if index > size:
            raise ValueError("truncated field")


def _varint(payload: bytes, index: int) -> Tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if index >= len(payload) or shift > 63:
            raise ValueError("bad varint")
        byte = payload[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7
