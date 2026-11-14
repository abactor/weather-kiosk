"""Alert selection and the line under the date. No network."""

from __future__ import annotations

from datetime import date

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from weather_kiosk.alerts import (
    EXAMPLE_ALERT,
    TORONTO_CITY_ALERTS_URL,
    USE_EXAMPLE_ALERT,
    WeatherAlert,
    alert_line,
    alerts_for_page,
    fetch_toronto_alerts,
    parse_alerts,
    parse_city_alert,
)
from weather_kiosk.render import render_page


def _feature(area, status, kind, name, text):
    return {
        "properties": {
            "feature_name_en": area,
            "status_en": status,
            "alert_type": kind,
            "alert_name_en": name,
            "alert_text_en": text,
        }
    }


def test_parse_keeps_active_city_of_toronto_alerts_in_priority_order():
    payload = {
        "features": [
            _feature("City of Toronto", "in effect", "advisory", "Fog advisory", "Dense fog."),
            _feature(
                "City of Toronto",
                "in effect",
                "warning",
                "Rainfall warning",
                "Heavy rain.",
            ),
            _feature(
                "City of Mississauga",
                "in effect",
                "warning",
                "Wind warning",
                "Stay off the lake.",
            ),
            _feature("City of Toronto", "ended", "warning", "Old warning", "Over."),
            _feature("city of toronto", "active", "watch", "Snow squall watch", "Bursts."),
        ]
    }
    parsed = parse_alerts(payload)
    assert [item.name for item in parsed] == [
        "Rainfall warning",
        "Snow squall watch",
        "Fog advisory",
    ]
    assert alert_line(parsed).startswith("Rainfall warning: Heavy rain.")
    assert "Wind warning" not in alert_line(parsed)
    assert "Old warning" not in alert_line(parsed)


def test_example_fills_an_empty_live_list_and_live_alerts_win():
    live = [
        WeatherAlert("warning", "Rainfall warning", "Heavy rain.", "City of Toronto")
    ]
    assert alerts_for_page([], use_example=True) == [EXAMPLE_ALERT]
    assert alerts_for_page([], use_example=False) == []
    assert alerts_for_page(live, use_example=True) == live


def test_alert_line_is_red_bold_and_scrolls_when_long():
    long_alert = WeatherAlert(
        "warning",
        "Rainfall warning",
        "Heavy downpours may cause ponding on roads across the City of Toronto.",
        "City of Toronto",
    )
    page = render_page(date(2026, 11, 13), [], [long_alert])
    assert 'class="day"' in page
    assert page.index('class="day"') < page.index('class="alert"')
    assert "color: #ff2a2a" in page
    assert "font-weight: 700" in page
    assert 'class="alert-text alert-marquee"' not in page
    assert "text.scrollWidth > line.clientWidth" in page
    assert "Rainfall warning: Heavy downpours" in page
    short = render_page(
        date(2026, 11, 13),
        [],
        [WeatherAlert("advisory", "Fog", "", "City of Toronto")],
    )
    assert 'class="alert-text"' in short
    assert 'class="alert-text alert-marquee"' not in short
    assert ">Fog<" in short
    quiet = render_page(date(2026, 11, 13), [], [])
    assert 'class="alert"' not in quiet
    assert 'class="reminder"' in quiet
    assert "color: #ffffff" in quiet


_EMPTY_CITY = (
    "<style> #home-alertbox, #global-alertbox { display: none; } </style> "
)
_CITY_BANNER = """<a class='hidden' href='/?WT.ac=Heat-abc123'></a>
<h2>Heat warning</h2>
<p>Cooling centres are open. Details at <a href="https://www.toronto.ca/">toronto.ca</a>.</p>
<style>
#home-alertbox, #global-alertbox { display: block; }
</style>
"""
_HOME_HIDDEN = """<h2>COVID-19</h2>
<p>Stay home except for essential reasons.</p>
<style>
#global-alertbox { display: block; }
#home-alertbox { display: none; }
</style>
"""


def test_city_banner_is_read_and_a_hidden_banner_is_not():
    banner = parse_city_alert(_CITY_BANNER)
    assert banner is not None
    assert banner.name == "Heat warning"
    assert banner.text == "Cooling centres are open. Details at toronto.ca."
    assert "WT.ac" not in banner.text
    assert parse_city_alert(_EMPTY_CITY) is None
    assert parse_city_alert(_HOME_HIDDEN) is None
    assert parse_city_alert("") is None


class _AlertHandler(BaseHTTPRequestHandler):
    weather = b'{"features":[]}'
    city = _EMPTY_CITY.encode("utf-8")
    status = {"weather": 200, "city": 200}
    seen = []

    def do_GET(self):
        kind = "city" if "globalalert" in self.path else "weather"
        type(self).seen.append((kind, self.headers.get("User-Agent")))
        body = type(self).city if kind == "city" else type(self).weather
        self.send_response(type(self).status[kind])
        self.send_header("Content-Type", "text/html" if kind == "city" else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def _alert_server():
    handler = type("Handler", (_AlertHandler,), {})
    handler.weather = b'{"features":[]}'
    handler.city = _EMPTY_CITY.encode("utf-8")
    handler.status = {"weather": 200, "city": 200}
    handler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    return server, base, handler


def test_fetch_reads_both_sources_and_keeps_toronto_only():
    """Local stand-in for GeoMet and the City banner. No external host."""
    weather = {
        "features": [
            {
                "properties": {
                    "feature_name_en": "City of Toronto",
                    "status_en": "in effect",
                    "alert_type": "warning",
                    "alert_name_en": "Rainfall warning",
                    "alert_text_en": "Heavy rain.",
                }
            },
            {
                "properties": {
                    "feature_name_en": "City of Mississauga",
                    "status_en": "in effect",
                    "alert_type": "warning",
                    "alert_name_en": "Wind warning",
                    "alert_text_en": "Stay off the lake.",
                }
            },
            {
                "properties": {
                    "feature_name_en": "City of Toronto",
                    "status_en": "ended",
                    "alert_type": "warning",
                    "alert_name_en": "Old warning",
                    "alert_text_en": "Over.",
                }
            },
        ]
    }
    server, base, handler = _alert_server()
    handler.weather = json.dumps(weather).encode("utf-8")
    handler.city = _CITY_BANNER.encode("utf-8")
    try:
        found = fetch_toronto_alerts(
            timeout=2,
            weather_url=base + "/collections/weather-alerts/items?f=json",
            city_url=base + "/cot/globalalert/",
        )
    finally:
        server.shutdown()
        server.server_close()
    assert [item.name for item in found] == ["Rainfall warning", "Heat warning"]
    assert "Wind warning" not in alert_line(found)
    assert "Old warning" not in alert_line(found)
    assert alert_line(found).startswith("Rainfall warning: Heavy rain.")
    assert "Heat warning: Cooling centres are open." in alert_line(found)
    assert [kind for kind, _agent in handler.seen] == ["weather", "city"]
    assert {agent for _kind, agent in handler.seen} == {"weather-kiosk/1.0"}
    page = render_page(date(2026, 11, 13), [], found)
    assert page.index('class="day"') < page.index('class="alert"')
    assert "color: #ff2a2a" in page
    assert "font-weight: 700" in page


def test_empty_feeds_hide_the_alert_line():
    assert USE_EXAMPLE_ALERT is False
    assert TORONTO_CITY_ALERTS_URL.endswith("/cot/globalalert/")
    server, base, handler = _alert_server()
    try:
        found = fetch_toronto_alerts(
            timeout=2,
            weather_url=base + "/collections/weather-alerts/items?f=json",
            city_url=base + "/cot/globalalert/",
        )
    finally:
        server.shutdown()
        server.server_close()
    assert found == []
    assert alerts_for_page(found, USE_EXAMPLE_ALERT) == []
    page = render_page(date(2026, 11, 13), [], alerts_for_page(found, USE_EXAMPLE_ALERT))
    assert 'class="alert"' not in page
    assert 'class="reminder"' in page
    assert "color: #ffffff" in page
    assert "Example only" not in page


def test_alert_takes_the_line_and_a_reminder_is_white():
    from weather_kiosk.alerts import WeatherAlert

    reminder = "Pack the library books before school tomorrow morning"
    waiting = render_page(date(2026, 11, 13), [], [], reminder)
    assert 'class="reminder"' in waiting
    assert 'class="alert"' not in waiting
    assert "Pack the library books" in waiting
    assert 'class="alert-text alert-marquee"' not in waiting
    assert "text.scrollWidth > line.clientWidth" in waiting
    live = render_page(
        date(2026, 11, 13),
        [],
        [WeatherAlert("warning", "Rainfall warning", "Heavy rain.", "City of Toronto")],
        reminder,
    )
    assert 'class="alert"' in live
    assert 'class="reminder"' not in live
    assert "Pack the library books" not in live
    assert "Rainfall warning: Heavy rain." in live


def test_one_failed_source_does_not_drop_the_other():
    server, base, handler = _alert_server()
    handler.status = {"weather": 500, "city": 200}
    handler.city = _CITY_BANNER.encode("utf-8")
    try:
        city_only = fetch_toronto_alerts(
            timeout=2,
            weather_url=base + "/collections/weather-alerts/items?f=json",
            city_url=base + "/cot/globalalert/",
        )
    finally:
        server.shutdown()
        server.server_close()
    assert [item.name for item in city_only] == ["Heat warning"]

    fog = {
        "features": [
            _feature(
                "City of Toronto",
                "in effect",
                "advisory",
                "Fog advisory",
                "Dense fog.",
            )
        ]
    }
    server, base, handler = _alert_server()
    handler.weather = json.dumps(fog).encode("utf-8")
    try:
        kept = fetch_toronto_alerts(
            timeout=0.5,
            weather_url=base + "/collections/weather-alerts/items?f=json",
            city_url="http://127.0.0.1:1/cot/globalalert/",
        )
    finally:
        server.shutdown()
        server.server_close()
    assert [item.name for item in kept] == ["Fog advisory"]
