# On the Pi, run ./weather-kiosk with the system python3. Chromium opens the HTML file.

"""CLI for the Toronto forecast kiosk page."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from weather_kiosk.alerts import USE_EXAMPLE_ALERT, alerts_for_page, fetch_toronto_alerts
from weather_kiosk.calendar import fetch_reminder
from weather_kiosk.fetch import (
    ForecastError,
    ForecastParseError,
    TORONTO_CITYPAGE_URL,
    decode_json,
    fetch_citypage,
    fetch_toronto_city_current,
    parse_current,
    parse_hourly,
)
from weather_kiosk.render import render_page
from weather_kiosk.transit import current_board
from weather_kiosk.select import TORONTO, forecast_day, select_slots
from weather_kiosk.stash import augment_observation, recall_hour, remember_hourly


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render the Toronto daytime forecast as an HTML page."
    )
    parser.add_argument(
        "--now",
        help="ISO8601 clock override. A value with no offset is America/Toronto.",
    )
    parser.add_argument(
        "--fixture",
        help="Read a saved GeoMet JSON file instead of the network.",
    )
    parser.add_argument(
        "--url",
        default=TORONTO_CITYPAGE_URL,
        help="GeoMet city-page URL. Ignored when --fixture is set.",
    )
    parser.add_argument(
        "--output",
        help="Write HTML to this path. Default is stdout.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Seconds between refreshes. 0 fetches once. Requires --output when greater than 0.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.interval < 0:
        print("weather-kiosk: --interval must be 0 or greater", file=sys.stderr)
        return 1
    if args.interval > 0 and not args.output:
        print("weather-kiosk: --interval needs --output", file=sys.stderr)
        return 1

    while True:
        try:
            page = build_page(args.now, args.fixture, args.url, args.output)
            emit_page(page, args.output)
        except ForecastError as exc:
            print("weather-kiosk: %s" % exc, file=sys.stderr)
            if args.interval <= 0:
                return 1
        except ValueError as exc:
            print("weather-kiosk: %s" % exc, file=sys.stderr)
            return 1
        except OSError as exc:
            print("weather-kiosk: %s" % exc, file=sys.stderr)
            if args.interval <= 0:
                return 1
        else:
            if args.interval <= 0:
                return 0
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


def build_page(
    now_text: Optional[str],
    fixture: Optional[str],
    url: str,
    output: Optional[str] = None,
) -> str:
    """Fetch or load the forecast and return the HTML page for this clock."""
    now = resolve_now(now_text)
    payload = load_payload(fixture, url)
    records = parse_hourly(payload)
    current = parse_current(payload)
    alerts = []
    reminder = ""
    boards = []
    if fixture is None and url == TORONTO_CITYPAGE_URL:
        current = _local_current(current, records, now, output)
        alerts = alerts_for_page(fetch_toronto_alerts(), USE_EXAMPLE_ALERT)
        reminder = fetch_reminder(now)
        boards = current_board(now)
    return render_page(
        forecast_day(now),
        select_slots(now, records, count=None, current=current),
        alerts,
        reminder,
        boards,
    )


def _local_current(current, records, now, output: Optional[str]):
    """Toronto City observation, with a saved sky line when we have one.

    The hourly forecast is stashed beside the page. If the station or the
    stash is missing, the city-page observation is kept and the page
    still renders.
    """
    stash_path = None
    if output:
        stash_path = Path(output).with_suffix(".hourly.json")
        remember_hourly(stash_path, records, now)
    local = None
    try:
        local = fetch_toronto_city_current(now)
    except ForecastError as exc:
        print(
            "weather-kiosk: Toronto City observation unavailable: %s" % exc,
            file=sys.stderr,
        )
    if local is None:
        return current
    saved = recall_hour(stash_path, now) if stash_path is not None else None
    return augment_observation(local, saved)


def emit_page(page: str, output: Optional[str]) -> None:
    """Write the page atomically, or send it to stdout."""
    if output:
        write_text_atomic(Path(output), page)
        return
    sys.stdout.buffer.write(page.encode("utf-8"))
    sys.stdout.buffer.flush()


def write_text_atomic(path: Path, text: str) -> None:
    """Replace path only after the full page is on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def resolve_now(text: Optional[str]) -> datetime:
    """Use the override clock, or the current time in America/Toronto."""
    if text is None or not str(text).strip():
        return datetime.now(TORONTO)
    return parse_user_now(text)


def parse_user_now(text: str) -> datetime:
    """Parse an ISO8601 timestamp. Naive values are America/Toronto."""
    raw = text.strip()
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("could not read --now as ISO8601: %s" % text) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=TORONTO)
    return parsed


def load_payload(fixture: Optional[str], url: str = TORONTO_CITYPAGE_URL) -> Any:
    """Load GeoMet JSON from a fixture path, or fetch the live city page."""
    if fixture:
        try:
            raw = Path(fixture).read_bytes()
        except OSError as exc:
            raise ForecastError("could not read fixture: %s" % exc) from exc
        try:
            return decode_json(raw)
        except ForecastParseError as exc:
            raise ForecastParseError("could not read fixture: %s" % exc) from exc
    return fetch_citypage(url)


if __name__ == "__main__":
    sys.exit(main())
