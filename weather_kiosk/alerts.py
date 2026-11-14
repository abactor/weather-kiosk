"""Toronto alerts for the line under the date.

Environment Canada GeoMet weather alerts are limited to the City of Toronto.
The City of Toronto homepage banner is checked as well. The line stays off
when neither source has an active alert. EXAMPLE_ALERT is the approved
sample wording; USE_EXAMPLE_ALERT keeps it off the live page.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional, Sequence

# City bounds. The name is checked again so a neighbour inside the box is left out.
TORONTO_ALERTS_URL = (
    "https://api.weather.gc.ca/collections/weather-alerts/items"
    "?f=json&limit=200&bbox=-79.70,43.58,-79.10,43.90"
    "&feature_name_en=City%20of%20Toronto"
)
TORONTO_CITY_ALERTS_URL = "https://www.toronto.ca/cot/globalalert/"
TORONTO_ALERT_AREA = "city of toronto"
_CLOSED = {"ended", "cancelled", "canceled", "expired", "terminated"}
_RANK = {"warning": 0, "watch": 1, "advisory": 2, "statement": 3}
_MARQUEE_AT = 48
_HOME_BOX = re.compile(r"(?:^|[\s,+>~])#home-alertbox(?![A-Za-z0-9_-])")
_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DISPLAY_NONE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_DISPLAY_BLOCK = re.compile(r"display\s*:\s*block", re.IGNORECASE)

# Approved sample for a layout check. Live pages leave the line hidden
# unless Environment Canada or the City has an alert in effect.
USE_EXAMPLE_ALERT = False


@dataclass(frozen=True)
class WeatherAlert:
    """One alert reduced to the words the panel can show."""

    kind: str
    name: str
    text: str
    area: str


EXAMPLE_ALERT = WeatherAlert(
    kind="warning",
    name="Rainfall warning",
    text=(
        "Example only. Heavy downpours may cause ponding on roads and poor "
        "visibility in the City of Toronto. This sample stays on the line "
        "until live advisories are turned on."
    ),
    area="City of Toronto",
)


def fetch_toronto_alerts(
    timeout: float = 20.0,
    weather_url: str = TORONTO_ALERTS_URL,
    city_url: str = TORONTO_CITY_ALERTS_URL,
) -> List[WeatherAlert]:
    """Active Toronto alerts. A failed source is skipped, not fatal."""
    found = _fetch_weather_alerts(weather_url, timeout)
    city = _fetch_city_alert(city_url, timeout)
    if city is not None and not _already_shown(found, city):
        found.append(city)
    return found


def parse_alerts(payload: object) -> List[WeatherAlert]:
    """Keep active City of Toronto alerts, warnings ahead of watches."""
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        return []
    found = []
    for feature in features:
        alert = _one_alert(feature)
        if alert is not None:
            found.append(alert)
    found.sort(key=lambda item: (_RANK.get(item.kind, 9), item.name.lower()))
    return found


def parse_city_alert(html: str) -> Optional[WeatherAlert]:
    """City homepage banner, or None when that banner is hidden or empty."""
    if not isinstance(html, str) or _home_alert_hidden(html):
        return None
    parser = _CityHTML()
    parser.feed(html)
    parser.close()
    parser.flush()
    name = parser.headings[0] if parser.headings else ""
    text = " ".join(parser.paragraphs)
    if not name and not text:
        return None
    return WeatherAlert(
        kind="statement",
        name=name,
        text=text,
        area="City of Toronto",
    )


def alerts_for_page(
    live: Sequence[WeatherAlert],
    use_example: bool = False,
) -> List[WeatherAlert]:
    """Live alerts win. The example is only a stand-in for an empty feed."""
    if live:
        return list(live)
    if use_example:
        return [EXAMPLE_ALERT]
    return []


def alert_line(alerts: Sequence[WeatherAlert]) -> str:
    """One scrolling sentence. Empty when there is nothing to say."""
    parts = []
    for alert in alerts:
        name = " ".join(alert.name.split())
        text = " ".join(alert.text.split())
        if name and text:
            parts.append("%s: %s" % (name, text))
        elif name or text:
            parts.append(name or text)
    return "   ·   ".join(parts)


def line_needs_marquee(line: str) -> bool:
    return len(line) > _MARQUEE_AT


def _fetch_weather_alerts(url: str, timeout: float) -> List[WeatherAlert]:
    raw = _read_url(url, timeout)
    if not raw:
        return []
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    return parse_alerts(payload)


def _fetch_city_alert(url: str, timeout: float) -> Optional[WeatherAlert]:
    raw = _read_url(url, timeout)
    if not raw:
        return None
    try:
        return parse_city_alert(raw.decode("utf-8", "replace"))
    except (UnicodeError, ValueError):
        return None


def _read_url(url: str, timeout: float) -> Optional[bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/html;q=0.9",
            "User-Agent": "weather-kiosk/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _already_shown(alerts: Sequence[WeatherAlert], city: WeatherAlert) -> bool:
    name = city.name.casefold()
    text = city.text.casefold()
    for alert in alerts:
        if name and name == alert.name.casefold():
            return True
        if text and text == alert.text.casefold():
            return True
    return False


def _home_alert_hidden(html: str) -> bool:
    """True when the City homepage would not show its alert box."""
    hidden = False
    seen = False
    for style in _STYLE_BLOCK.findall(html):
        for selector, body in _CSS_RULE.findall(style):
            if _HOME_BOX.search(selector) is None:
                continue
            seen = True
            if _DISPLAY_NONE.search(body):
                hidden = True
            elif _DISPLAY_BLOCK.search(body):
                hidden = False
    return seen and hidden


def _one_alert(feature: object) -> Optional[WeatherAlert]:
    if not isinstance(feature, dict):
        return None
    props = feature.get("properties")
    if not isinstance(props, dict):
        return None
    area = props.get("feature_name_en")
    if not isinstance(area, str) or area.strip().casefold() != TORONTO_ALERT_AREA:
        return None
    status = props.get("status_en")
    if isinstance(status, str) and status.strip().casefold() in _CLOSED:
        return None
    kind = props.get("alert_type")
    name = props.get("alert_name_en") or props.get("alert_short_name_en")
    text = props.get("alert_text_en")
    if not isinstance(kind, str) or not kind.strip():
        kind = "statement"
    if not isinstance(name, str):
        name = ""
    if not isinstance(text, str):
        text = ""
    name = " ".join(name.split())
    text = " ".join(text.split())
    if not name and not text:
        return None
    return WeatherAlert(
        kind=kind.strip().casefold(),
        name=name,
        text=text,
        area=area.strip(),
    )


class _CityHTML(HTMLParser):
    """Heading and paragraphs from the City banner, skipping its tracking link."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._mode = None
        self._buf = []
        self.headings = []
        self.paragraphs = []

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, void=False)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, void=True)

    def handle_endtag(self, tag):
        if self._skip:
            self._skip -= 1
            return
        if tag.lower() in ("h1", "h2", "h3", "p"):
            self.flush()
            self._mode = None

    def handle_data(self, data):
        if self._skip or self._mode is None:
            return
        self._buf.append(data)

    def flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if not text or self._mode is None:
            return
        if self._mode == "h":
            self.headings.append(text)
        else:
            self.paragraphs.append(text)

    def _open(self, tag, attrs, void: bool) -> None:
        tag = tag.lower()
        attr = {key.lower(): (value or "") for key, value in attrs}
        classes = attr.get("class", "").split()
        tracking = "WT.ac=" in attr.get("href", "")
        skip = tag in ("style", "script") or "hidden" in classes or tracking
        if self._skip:
            if not void:
                self._skip += 1
            return
        if skip:
            if not void:
                self._skip += 1
            return
        if tag in ("h1", "h2", "h3", "p"):
            self.flush()
            self._mode = "p" if tag == "p" else "h"
