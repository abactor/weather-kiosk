"""HTTP fetch tests. The server is local; nothing calls api.weather.gc.ca."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from weather_kiosk.__main__ import main
from weather_kiosk.fetch import ForecastFetchError, fetch_citypage, parse_hourly

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "toronto_on-143.json"
NOW = "2026-11-13T10:30:00-05:00"


class _QuietHandler(BaseHTTPRequestHandler):
    body = b""
    status = 200
    delay = 0.0
    user_agents = []

    def do_GET(self):
        agent = self.headers.get("User-Agent")
        type(self).user_agents.append(agent)
        if type(self).delay:
            import time

            time.sleep(type(self).delay)
        payload = type(self).body
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


@pytest.fixture
def citypage_server():
    handler = type("Handler", (_QuietHandler,), {})
    handler.body = FIXTURE.read_bytes()
    handler.status = 200
    handler.delay = 0.0
    handler.user_agents = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        yield base, handler
    finally:
        server.shutdown()
        server.server_close()


def test_fetch_citypage_reads_local_geomet_document(citypage_server):
    base, handler = citypage_server
    payload = fetch_citypage(base + "/on-143", timeout=2)
    records = parse_hourly(payload)
    assert len(records) == 47
    assert handler.user_agents == ["weather-kiosk/1.0"]


def test_fetch_citypage_reports_http_error(citypage_server):
    base, handler = citypage_server
    handler.status = 500
    handler.body = b"no"
    with pytest.raises(ForecastFetchError, match="HTTP 500"):
        fetch_citypage(base + "/fail", timeout=2)


def test_cli_url_writes_page_from_local_server(citypage_server, tmp_path):
    base, _handler = citypage_server
    output = tmp_path / "page.html"
    code = main(
        ["--url", base + "/on-143", "--now", NOW, "--output", str(output)]
    )
    assert code == 0
    text = output.read_text(encoding="utf-8")
    assert "12:00 PM" in text
    assert "60% PoP" in text
    assert not (tmp_path / "page.html.tmp").exists()


def test_failed_fetch_leaves_the_existing_page(citypage_server, tmp_path, capsys):
    base, handler = citypage_server
    handler.status = 503
    handler.body = b"down"
    output = tmp_path / "page.html"
    output.write_text("KEEP", encoding="utf-8")
    code = main(["--url", base + "/fail", "--now", NOW, "--output", str(output)])
    assert code == 1
    assert output.read_text(encoding="utf-8") == "KEEP"
    assert "HTTP 503" in capsys.readouterr().err


def test_interval_requires_output_and_sleeps_after_a_good_fetch(citypage_server, tmp_path, monkeypatch):
    base, _handler = citypage_server
    output = tmp_path / "nested" / "page.html"
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr("weather_kiosk.__main__.time.sleep", fake_sleep)
    code = main(
        [
            "--url",
            base + "/on-143",
            "--now",
            NOW,
            "--output",
            str(output),
            "--interval",
            "30",
        ]
    )
    assert code == 0
    assert slept == [30]
    assert "60% PoP" in output.read_text(encoding="utf-8")
    missing = main(["--interval", "30"])
    assert missing == 1


def test_swob_reads_temperature_and_recent_precipitation():
    from weather_kiosk.fetch import latest_minute_swob, parse_swob_current

    dry = parse_swob_current(
        {
            "properties": {
                "air_temp": 4.0,
                "pcpn_amt_pst10mts": 0,
                "pcpn_amt_pst1hr": 1.2,
                "rel_hum": 65,
                "avg_wnd_spd_pcpn_gag_pst1mt": 1.2,
                "avg_wnd_spd_pcpn_gag_pst10mts": 0.6,
            }
        }
    )
    assert dry.temperature_c == 4.0
    assert dry.condition == ""
    assert dry.precip_mm == 0.0
    assert dry.humidity_percent == 65
    assert dry.wind_kmh == 1.2
    hourly = parse_swob_current({"properties": {"air_temp": "4", "pcpn_amt_pst1hr": 1.2}})
    assert hourly.precip_mm == 1.2
    assert hourly.humidity_percent is None
    assert hourly.wind_kmh is None
    fallback = parse_swob_current(
        {
            "properties": {
                "air_temp": 4,
                "rel_hum": "MSNG",
                "avg_wnd_spd_pcpn_gag_pst1mt": None,
                "avg_wnd_spd_pcpn_gag_pst10mts": 0.6,
            }
        }
    )
    assert fallback.humidity_percent is None
    assert fallback.wind_kmh == 0.6
    calm = parse_swob_current(
        {"properties": {"air_temp": 4, "rel_hum": 0, "avg_wnd_spd_pcpn_gag_pst1mt": 0}}
    )
    assert calm.humidity_percent == 0
    assert calm.wind_kmh == 0
    assert parse_swob_current({"properties": {}}) is None
    assert parse_swob_current("nope") is None
    assert latest_minute_swob(
        [
            "2026-11-14-0144-CXTO-AUTO-minute-swob.xml",
            "2026-11-14-0100-CXTO-AUTO-swob.xml",
            "dir/2026-11-14-0145-CXTO-AUTO-minute-swob.xml",
        ]
    ) == "2026-11-14-0145-CXTO-AUTO-minute-swob.xml"
    assert latest_minute_swob(["2026-11-14-0100-CXTO-AUTO-swob.xml"]) is None
