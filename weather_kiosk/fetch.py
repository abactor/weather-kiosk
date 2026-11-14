"""Load an Environment and Climate Change Canada city-page forecast.

Parsing is separate from the HTTP GET so tests can feed a saved document.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence

# Toronto city page. Toronto Island is a different item (on-128).
TORONTO_ITEM_ID = "on-143"
TORONTO_CITYPAGE_URL = (
    "https://api.weather.gc.ca/collections/citypageweather-realtime/items/"
    "%s?f=json&lang=en" % TORONTO_ITEM_ID
)
DEFAULT_TIMEOUT_S = 20.0


class ForecastError(Exception):
    """The forecast could not be fetched or read."""


class ForecastFetchError(ForecastError):
    """The GeoMet request failed or timed out."""


class ForecastParseError(ForecastError):
    """The body was not the expected city-page shape."""


@dataclass(frozen=True)
class HourlyRecord:
    """One hourly forecast reduced to the fields the selector needs."""

    timestamp: datetime
    temperature_c: float
    condition: str
    precip_percent: int
    wind_kmh: Optional[float] = None
    humidity_percent: Optional[float] = None


def fetch_citypage(
    url: str = TORONTO_CITYPAGE_URL,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Any:
    """GET a GeoMet city-page document and return the decoded JSON value."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "weather-kiosk/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise ForecastFetchError(
            "weather service returned HTTP %s" % exc.code
        ) from exc
    except urllib.error.URLError as exc:
        raise ForecastFetchError(
            "could not fetch the forecast: %s" % exc.reason
        ) from exc
    except TimeoutError as exc:
        raise ForecastFetchError("forecast request timed out") from exc
    except OSError as exc:
        raise ForecastFetchError("could not fetch the forecast: %s" % exc) from exc
    return decode_json(raw)


def decode_json(raw: bytes) -> Any:
    """Decode a response body as UTF-8 JSON."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ForecastParseError("forecast body is not UTF-8 text") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ForecastParseError("forecast body is not JSON") from exc


@dataclass(frozen=True)
class CurrentConditions:
    """Latest station observation.

    precip_mm is measured precipitation, not a probability. The city-page
    observation leaves it unset. Humidity and wind are the station values
    when that feed includes them, and stay unset when it does not.
    A station with no sky condition uses an empty condition string.
    """

    temperature_c: float
    condition: str
    precip_mm: Optional[float] = None
    humidity_percent: Optional[float] = None
    wind_kmh: Optional[float] = None


def parse_current(payload: Any) -> Optional[CurrentConditions]:
    """Read properties.currentConditions, or None when that section is absent."""
    if not isinstance(payload, dict):
        return None
    properties = payload.get("properties")
    if not isinstance(properties, dict) or "currentConditions" not in properties:
        return None
    current = properties.get("currentConditions")
    if not isinstance(current, dict):
        raise ForecastParseError("currentConditions is not an object")
    temperature = current.get("temperature")
    if not isinstance(temperature, dict):
        raise ForecastParseError("currentConditions is missing temperature")
    condition = current.get("condition")
    if not isinstance(condition, dict):
        raise ForecastParseError("currentConditions is missing condition")
    condition_en = condition.get("en")
    if not isinstance(condition_en, str):
        raise ForecastParseError("currentConditions is missing condition.en")
    value = temperature.get("value")
    if not isinstance(value, dict) or "en" not in value:
        raise ForecastParseError("currentConditions is missing temperature.value.en")
    wind = current.get("wind")
    wind_speed = wind.get("speed") if isinstance(wind, dict) else None
    return CurrentConditions(
        temperature_c=_as_float(value["en"], "temperature.value.en", 0),
        condition=condition_en,
        humidity_percent=_optional_metric_en(current.get("relativeHumidity")),
        wind_kmh=_optional_metric_en(wind_speed),
    )


def parse_hourly(payload: Any) -> List[HourlyRecord]:
    """Pull English hourly records out of a city-page feature document.

    Expected path: properties.hourlyForecastGroup.hourlyForecasts.
    Each hour needs timestamp, temperature.value.en, condition.en,
    and lop.value.en.
    """
    if not isinstance(payload, dict):
        raise ForecastParseError("forecast JSON must be an object")
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        raise ForecastParseError("forecast JSON is missing properties")
    group = properties.get("hourlyForecastGroup")
    if not isinstance(group, dict):
        raise ForecastParseError("forecast JSON is missing hourlyForecastGroup")
    hours = group.get("hourlyForecasts")
    if not isinstance(hours, list):
        raise ForecastParseError("forecast JSON is missing hourlyForecasts")

    records: List[HourlyRecord] = []
    for index, entry in enumerate(hours):
        records.append(_parse_hour(entry, index))
    return records


def _parse_hour(entry: Any, index: int) -> HourlyRecord:
    if not isinstance(entry, dict):
        raise ForecastParseError("hourlyForecasts[%s] is not an object" % index)
    timestamp = _parse_timestamp(entry.get("timestamp"), index)
    temperature = entry.get("temperature")
    if not isinstance(temperature, dict):
        raise ForecastParseError(
            "hourlyForecasts[%s] is missing temperature" % index
        )
    condition = entry.get("condition")
    if not isinstance(condition, dict):
        raise ForecastParseError(
            "hourlyForecasts[%s] is missing condition" % index
        )
    lop = entry.get("lop")
    if not isinstance(lop, dict):
        raise ForecastParseError("hourlyForecasts[%s] is missing lop" % index)

    condition_en = condition.get("en")
    if not isinstance(condition_en, str):
        raise ForecastParseError(
            "hourlyForecasts[%s] is missing condition.en" % index
        )
    return HourlyRecord(
        timestamp=timestamp,
        temperature_c=_as_float(_value_en(temperature, "temperature", index), "temperature.value.en", index),
        condition=condition_en,
        precip_percent=_as_percent(_value_en(lop, "lop", index), index),
        wind_kmh=_optional_metric_en(
            entry.get("wind").get("speed") if isinstance(entry.get("wind"), dict) else None
        ),
    )


def _value_en(parent: dict, field: str, index: int) -> Any:
    value = parent.get("value")
    if not isinstance(value, dict) or "en" not in value:
        raise ForecastParseError(
            "hourlyForecasts[%s] is missing %s.value.en" % (index, field)
        )
    return value["en"]


def _parse_timestamp(value: Any, index: int) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ForecastParseError(
            "hourlyForecasts[%s] is missing timestamp" % index
        )
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ForecastParseError(
            "hourlyForecasts[%s] has an unreadable timestamp" % index
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForecastParseError(
            "hourlyForecasts[%s] timestamp must include a timezone" % index
        )
    return parsed.astimezone(timezone.utc)


def _as_float(value: Any, what: str, index: int) -> float:
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError as exc:
            raise ForecastParseError(
                "hourlyForecasts[%s] has a non-numeric %s" % (index, what)
            ) from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForecastParseError(
            "hourlyForecasts[%s] has a non-numeric %s" % (index, what)
        )
    return float(value)


def _as_percent(value: Any, index: int) -> int:
    return int(round(_as_float(value, "lop.value.en", index)))


# Toronto City, near the university. Closest live station to Yonge-Eglinton.
# The Brick Works partner gauge is not publishing observations.
TORONTO_CITY_STATION = "CXTO"
_SWOB_LIST = (
    "https://dd.weather.gc.ca/today/observations/swob-ml/%s/"
    + TORONTO_CITY_STATION
    + "/"
)
_SWOB_ITEM = "https://api.weather.gc.ca/collections/swob-realtime/items/%s?f=json"


def latest_minute_swob(names: Sequence[str]) -> Optional[str]:
    """Basename of the newest minute observation, ignoring hourly files."""
    found = []
    for name in names:
        base = str(name).rsplit("/", 1)[-1]
        if base.endswith("-AUTO-minute-swob.xml"):
            found.append(base)
    if not found:
        return None
    return max(found)


def parse_swob_current(payload: Any) -> Optional[CurrentConditions]:
    """Read the Toronto City minute observation from one SWOB feature.

    The 10-minute amount is the current precipitation. The past-hour
    amount is used only when the 10-minute field is absent. Humidity is
    rel_hum. This station's minute file has no 10 m anemometer; the
    precipitation-gauge wind is the speed it measures. The past-minute
    speed is preferred, and the 10-minute speed fills that gap. A missing
    or unreadable humidity or wind is left unset. There is no probability
    and usually no sky condition.
    """
    if not isinstance(payload, dict):
        return None
    props = payload.get("properties")
    if not isinstance(props, dict) or "air_temp" not in props:
        return None
    temperature = _plain_float(props.get("air_temp"))
    if temperature is None:
        return None
    return CurrentConditions(
        temperature_c=temperature,
        condition="",
        precip_mm=_recent_precip_mm(props),
        humidity_percent=_first_number(props, ("rel_hum",)),
        wind_kmh=_first_number(
            props,
            ("avg_wnd_spd_pcpn_gag_pst1mt", "avg_wnd_spd_pcpn_gag_pst10mts"),
        ),
    )


def fetch_toronto_city_current(
    moment: Optional[datetime] = None,
) -> Optional[CurrentConditions]:
    """Latest Toronto City minute observation, or None when it is unreachable."""
    when = moment or datetime.now(timezone.utc)
    when = when.astimezone(timezone.utc)
    for days_back in (0, 1):
        day = (when - timedelta(days=days_back)).strftime("%Y%m%d")
        try:
            names = _swob_names(day)
        except ForecastFetchError:
            continue
        name = latest_minute_swob(names)
        if name is None:
            continue
        try:
            payload = fetch_citypage(_SWOB_ITEM % name)
        except ForecastError:
            continue
        parsed = parse_swob_current(payload)
        if parsed is not None:
            return parsed
    return None


def _recent_precip_mm(props: dict) -> Optional[float]:
    if "pcpn_amt_pst10mts" in props and props["pcpn_amt_pst10mts"] is not None:
        return _plain_float(props.get("pcpn_amt_pst10mts"))
    if "pcpn_amt_pst1hr" in props and props["pcpn_amt_pst1hr"] is not None:
        return _plain_float(props.get("pcpn_amt_pst1hr"))
    return None


def _optional_metric_en(node: Any) -> Optional[float]:
    """English metric value, or None when that reading is absent."""
    if not isinstance(node, dict):
        return None
    value = node.get("value")
    if not isinstance(value, dict) or "en" not in value:
        return None
    return _plain_float(value.get("en"))


def _first_number(props: dict, keys: Sequence[str]) -> Optional[float]:
    """First readable number among keys. Zero is a reading, not a gap."""
    for key in keys:
        if key not in props or props.get(key) is None:
            continue
        number = _plain_float(props.get(key))
        if number is not None:
            return number
    return None


def _plain_float(value: Any) -> Optional[float]:
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _swob_names(day: str) -> List[str]:
    request = urllib.request.Request(
        _SWOB_LIST % day,
        headers={
            "Accept": "text/html",
            "User-Agent": "weather-kiosk/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_S) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise ForecastFetchError(
            "observation list returned HTTP %s" % exc.code
        ) from exc
    except urllib.error.URLError as exc:
        raise ForecastFetchError(
            "could not list observations: %s" % exc.reason
        ) from exc
    except TimeoutError as exc:
        raise ForecastFetchError("observation list timed out") from exc
    except OSError as exc:
        raise ForecastFetchError("could not list observations: %s" % exc) from exc
    return re.findall(r'href="([^"]+)"', raw.decode("utf-8", "replace"))
