"""Trip-update selection. The feed bytes are built here. No network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from weather_kiosk.select import TORONTO
import io
import zipfile

from weather_kiosk.fetch import TORONTO_CITYPAGE_URL
from weather_kiosk.__main__ import build_page
from weather_kiosk.render import render_page
from weather_kiosk.transit import (
    SURFACE_GTFS_URL,
    RouteBoard,
    route_matches,
    TransitConfig,
    TransitError,
    board_routes,
    current_board,
    format_arrival,
    install_schedule,
    stop_code_table,
    parse_config,
    parse_trip_updates,
    resolve_stop_id,
    routes_serving,
)


def _varint(number):
    out = bytearray()
    while True:
        piece = number & 0x7F
        number >>= 7
        if number:
            out.append(piece | 0x80)
        else:
            out.append(piece)
            return bytes(out)


def _key(field, wire):
    return _varint((field << 3) | wire)


def _text(field, value):
    raw = value.encode("utf-8")
    return _key(field, 2) + _varint(len(raw)) + raw


def _num(field, value):
    return _key(field, 0) + _varint(value)


def _msg(field, payload):
    return _key(field, 2) + _varint(len(payload)) + payload


def _stop_time(stop_id, when, relation=0, kind="arrival"):
    body = _text(4, stop_id) + _num(5, relation)
    event = _msg(2 if kind == "arrival" else 3, _num(2, when))
    return _msg(2, body + event)


def _entity(route_id, stops, relation=0, deleted=False):
    trip = _text(5, route_id)
    if relation:
        trip += _num(4, relation)
    update = _msg(1, trip)
    for stop in stops:
        update += stop
    entity = _text(1, route_id)
    if deleted:
        entity += _num(2, 1)
    entity += _msg(3, update)
    return _msg(2, entity)


def _feed(*entities):
    return b"".join(entities)


def test_config_keeps_one_stop_and_unique_routes():
    text = """
# home stop
stop 4505
route 52
route 97

route 52
route 501
"""
    config = parse_config(text)
    assert config is not None
    assert config.stop_id == "4505"
    assert config.routes == ("52", "97", "501")
    assert parse_config("route 52\n") is None
    assert parse_config("") is None


def test_route_match_is_exact_or_one_letter_for_any_number():
    samples = (
        ("11", "11", True),
        ("11", "11A", True),
        ("11", "11B", True),
        ("11", "11C", True),
        ("11", "111", False),
        ("11", "11AB", False),
        ("11", "11a", False),
        ("28", "28", True),
        ("28", "28A", True),
        ("28", "281", False),
        ("28", "128", False),
        ("501", "501", True),
        ("501", "501C", True),
        ("501", "5011", False),
        ("7", "7B", True),
        ("7", "70", False),
        ("7", "71", False),
    )
    for configured, feed_id, expected in samples:
        assert route_matches(configured, feed_id) is expected


def test_letter_suffix_arrivals_collect_under_the_configured_number():
    now = datetime(2026, 11, 14, 9, 0, tzinfo=TORONTO)
    first = int((now + timedelta(minutes=8)).timestamp())
    second = int((now + timedelta(minutes=21)).timestamp())
    later = int((now + timedelta(minutes=30)).timestamp())
    updates = [
        ("11A", [("100", first)]),
        ("11B", [("100", second)]),
        ("111", [("100", later)]),
        ("28", [("100", first)]),
        ("281", [("100", later)]),
    ]
    config = parse_config("stop 100\nroute 11\nroute 28\n")
    board = board_routes(
        config,
        updates,
        now,
        serving={"11A", "11B", "111", "28", "281"},
    )
    assert [item.route_id for item in board] == ["11", "28"]
    assert [format_arrival(item) for item in board[0].arrivals] == ["9:08 am", "9:21 am"]
    assert [format_arrival(item) for item in board[1].arrivals] == ["9:08 am"]


def test_board_keeps_the_next_two_future_arrivals():
    now = datetime(2026, 11, 14, 9, 0, tzinfo=TORONTO)
    first = int((now + timedelta(minutes=8)).timestamp())
    second = int((now + timedelta(minutes=21)).timestamp())
    third = int((now + timedelta(minutes=40)).timestamp())
    past = int((now - timedelta(minutes=2)).timestamp())
    payload = _feed(
        _entity(
            "501",
            [
                _stop_time("14152", past),
                _stop_time("14152", second),
                _stop_time("9999", first),
                _stop_time("14152", first),
                _stop_time("14152", third),
                _stop_time("14152", first, relation=2),
            ],
        ),
        _entity("501", [_stop_time("14152", first)], relation=3),
        _entity("29", [_stop_time("14152", second)], deleted=True),
        _entity("29", [_stop_time("14152", second, kind="departure")]),
    )
    updates = parse_trip_updates(payload)
    config = parse_config("stop 14152\nroute 501\nroute 29\nroute 504\n")
    board = board_routes(config, updates, now, serving={"501", "29"})
    assert [item.route_id for item in board] == ["501", "29", "504"]
    assert board[0].state == "times"
    assert [format_arrival(item) for item in board[0].arrivals] == ["9:08 am", "9:21 am"]
    assert board[1].state == "times"
    assert format_arrival(board[1].arrivals[0]) == "9:21 am"
    assert board[2].state == "not_at_stop"
    assert board[2].arrivals == ()


def test_missing_service_list_says_no_bus_instead_of_not_at_stop():
    now = datetime(2026, 11, 14, 9, 0, tzinfo=TORONTO)
    config = parse_config("stop 14152\nroute 52\n")
    board = board_routes(config, [], now)
    assert board[0].state == "no_bus"


def test_broken_payload_is_rejected():
    try:
        parse_trip_updates(b"\xff")
    except TransitError:
        return
    raise AssertionError("expected TransitError")


def test_static_schedule_uses_the_official_surface_gtfs_zip():
    assert SURFACE_GTFS_URL == (
        "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/"
        "bd4809dd-e289-4de8-bbde-c5c00dafbf4f/resource/"
        "28514055-d011-4ed7-8bb0-97961dfe2b66/download/surfacegtfs.zip"
    )
    assert "gtfsrt/trips" not in SURFACE_GTFS_URL


def _gtfs_zip():
    """Tiny surface schedule. Route 11's public number differs from its id."""
    files = {
        "feed/routes.txt": (
            "route_id,route_short_name\n"
            "28,28\n"
            "11A,11\n"
            "501,501\n"
            "74,74\n"
        ),
        "feed/trips.txt": (
            "trip_id,route_id\n"
            "t-28,28\n"
            "t-11,11A\n"
            "t-501,501\n"
            "t-74,74\n"
        ),
        "feed/stops.txt": (
            "stop_id,stop_code,stop_name\n"
            "100,100,Internal\n"
            "8512,1874,Davisville Ave at Forman Ave\n"
            "73,1873,Davisville Ave at Forman Ave\n"
            "2123,5811,Mt Pleasant Rd at Davisville Ave\n"
        ),
        "feed/stop_times.txt": (
            "trip_id,stop_id\n"
            "t-28,100\n"
            "t-11,100\n"
            "t-501,999\n"
            "t-missing,100\n"
            "t-28,8512\n"
            "t-11,8512\n"
            "t-74,2123\n"
        ),
    }
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return blob.getvalue()


def test_schedule_scan_names_routes_that_call_at_the_stop(tmp_path):
    when = datetime(2026, 11, 14, 9, 0, tzinfo=TORONTO)
    install_schedule(tmp_path, _gtfs_zip(), when)
    serving = routes_serving(tmp_path, "100")
    assert serving == {"28", "11A", "11"}
    assert "501" not in serving
    config = TransitConfig("100", ("28", "11", "501"))
    board = board_routes(config, [], when, serving)
    assert [item.state for item in board] == ["no_bus", "no_bus", "not_at_stop"]
    assert routes_serving(tmp_path / "missing", "100") is None


def test_stop_line_must_be_one_word():
    assert parse_config("stop Davisville and Foreman\nroute 28\n") is None
    kept = parse_config("stop 100\nroute 28\nroute 11\n")
    assert kept.stop_id == "100"
    assert kept.routes == ("28", "11")


def test_failed_feed_reuses_the_last_board_for_five_minutes(tmp_path):
    config_path = tmp_path / "transit.conf"
    config_path.write_text("stop 100\nroute 28\nroute 11\n", encoding="utf-8")
    other = tmp_path / "other.conf"
    other.write_text("stop 200\nroute 28\nroute 11\n", encoding="utf-8")
    cache = tmp_path / "board.json"
    schedule = tmp_path / "schedule"
    now = datetime(2026, 11, 14, 9, 0, tzinfo=TORONTO)
    arrival = int(datetime(2026, 11, 14, 14, 8, tzinfo=timezone.utc).timestamp())
    payload = _feed(_entity("28", [_stop_time("100", arrival)]))
    steps = ["fail", "ok", "fail", "fail", "ok", "fail"]

    def fetch():
        step = steps.pop(0)
        if step == "fail":
            raise TransitError("down")
        return payload

    common = dict(
        config_path=config_path,
        cache_path=cache,
        schedule_dir=schedule,
        download=False,
        fetch=fetch,
    )
    assert current_board(now, **common) == []
    live = current_board(now, **common)
    assert [(item.route_id, item.state) for item in live] == [
        ("28", "times"),
        ("11", "no_bus"),
    ]
    assert format_arrival(live[0].arrivals[0]) == "9:08 am"
    held = current_board(now + timedelta(minutes=5), **common)
    assert format_arrival(held[0].arrivals[0]) == "9:08 am"
    assert held[1].state == "no_bus"
    assert current_board(now + timedelta(minutes=5, seconds=1), **common) == []
    current_board(now, **common)
    assert current_board(now + timedelta(minutes=1), config_path=other, cache_path=cache, schedule_dir=schedule, download=False, fetch=fetch) == []


def test_panel_draws_the_board_and_omits_an_empty_corner():
    arrival = datetime(2026, 11, 14, 14, 8, tzinfo=timezone.utc)
    later = datetime(2026, 11, 14, 14, 21, tzinfo=timezone.utc)
    board = (
        RouteBoard("28", (arrival, later), "times"),
        RouteBoard("11", (), "no_bus"),
        RouteBoard("501", (), "not_at_stop"),
    )
    page = render_page(datetime(2026, 11, 14, tzinfo=TORONTO).date(), [], [], "", board)
    assert 'class="buses"' in page
    assert 'class="badge">28<' in page
    assert 'class="next">9:08 am<' in page
    assert 'class="later">9:21 am<' in page
    assert 'class="note">no bus<' in page
    assert 'class="missing">does not serve that stop<' in page
    buses = page.split('class="buses"', 1)[1].split("</ul>", 1)[0]
    assert "marquee" not in buses
    assert "z-index: 2" in page
    assert "z-index: 10000" in page
    assert "prefers-reduced-motion" in page
    empty = render_page(datetime(2026, 11, 14, tzinfo=TORONTO).date(), [])
    assert 'class="buses"' not in empty


def test_a_single_arrival_keeps_the_second_time_column():
    arrival = datetime(2026, 11, 14, 14, 8, tzinfo=timezone.utc)
    later = datetime(2026, 11, 14, 14, 21, tzinfo=timezone.utc)
    page = render_page(
        datetime(2026, 11, 14, tzinfo=TORONTO).date(),
        [],
        [],
        "",
        (
            RouteBoard("28", (arrival,), "times"),
            RouteBoard("11", (arrival, later), "times"),
        ),
    )
    buses = page.split('class="buses"', 1)[1].split("</ul>", 1)[0]
    assert 'class="later"></span>' in buses
    assert 'class="later">9:21 am</span>' in buses
    assert "grid-template-columns: 40px 90px 72px" in page


def test_third_route_shortens_the_reminder_so_it_does_not_run_under_that_row():
    boards = tuple(RouteBoard(str(route), (), "no_bus") for route in (28, 11, 74))
    day = datetime(2026, 11, 14, tzinfo=TORONTO).date()
    reminder = "Sat 11:00 am: dentist"
    page = render_page(day, [], [], reminder, boards)
    assert 'class="reminder beside-route"' in page
    assert "margin-right: 260px" in page
    two = render_page(day, [], [], reminder, boards[:2])
    assert 'class="reminder beside-route"' not in two
    from weather_kiosk.alerts import WeatherAlert
    alert = WeatherAlert("warning", "Rainfall warning", "Heavy rain.", "City of Toronto")
    alert_page = render_page(day, [], [alert], reminder, boards)
    assert 'class="alert beside-route"' in alert_page


def test_routes_stack_one_per_line_and_overflow_fills_the_top_line():
    boards = tuple(RouteBoard(str(route), (), "no_bus") for route in (28, 11, 74, 75, 76))
    page = render_page(datetime(2026, 11, 14, tzinfo=TORONTO).date(), [], [], "", boards)
    buses = page.split('class="buses"', 1)[1].split("</ul>", 1)[0]
    rows = [part.split("</li>", 1)[0] for part in buses.split('class="route-row"')[1:]]
    assert len(rows) == 3
    assert rows[0].index(">76<") < rows[0].index(">75<") < rows[0].index(">28<")
    assert ">11<" in rows[1] and ">28<" not in rows[1]
    assert ">74<" in rows[2] and ">11<" not in rows[2]


def test_live_refresh_draws_the_board_and_a_fixture_does_not(monkeypatch):
    arrival = datetime(2026, 11, 14, 14, 8, tzinfo=timezone.utc)
    board = (RouteBoard("28", (arrival,), "times"),)
    calls = []

    def fake_board(now):
        calls.append(now)
        return board

    monkeypatch.setattr("weather_kiosk.__main__.current_board", fake_board)
    monkeypatch.setattr("weather_kiosk.__main__.fetch_toronto_alerts", lambda: [])
    monkeypatch.setattr("weather_kiosk.__main__.fetch_reminder", lambda now: "")
    monkeypatch.setattr("weather_kiosk.__main__.fetch_toronto_city_current", lambda now: None)
    fixture = build_page(
        "2026-11-14T09:00:00-05:00",
        "tests/fixtures/toronto_on-143.json",
        TORONTO_CITYPAGE_URL,
    )
    assert calls == []
    assert 'class="buses"' not in fixture
    import json
    from pathlib import Path
    payload = json.loads(Path("tests/fixtures/toronto_on-143.json").read_text())
    monkeypatch.setattr("weather_kiosk.__main__.fetch_citypage", lambda url: payload)
    live = build_page("2026-11-14T09:00:00-05:00", None, TORONTO_CITYPAGE_URL)
    assert len(calls) == 1
    assert 'class="badge">28<' in live
    assert 'class="next">9:08 am<' in live


def test_public_stop_code_uses_the_gtfs_stop_id(tmp_path):
    when = datetime(2026, 11, 14, 9, 0, tzinfo=TORONTO)
    schedule = tmp_path / "schedule"
    install_schedule(schedule, _gtfs_zip(), when)
    assert (schedule / "stops.txt").is_file()
    assert resolve_stop_id(schedule, "1874") == "8512"
    assert resolve_stop_id(schedule, "100") == "100"
    config = tmp_path / "transit.conf"
    config.write_text("stop 1874\nroute 28\nroute 11\n", encoding="utf-8")
    first = int(datetime(2026, 11, 14, 14, 8, tzinfo=timezone.utc).timestamp())
    second = int(datetime(2026, 11, 14, 14, 21, tzinfo=timezone.utc).timestamp())
    payload = _feed(
        _entity("28", [_stop_time("8512", first)]),
        _entity("11", [_stop_time("8512", second)]),
    )
    board = current_board(
        when,
        config_path=config,
        cache_path=tmp_path / "board.json",
        schedule_dir=schedule,
        download=False,
        fetch=lambda: payload,
    )
    assert [(item.route_id, item.state) for item in board] == [
        ("28", "times"),
        ("11", "times"),
    ]
    assert format_arrival(board[0].arrivals[0]) == "9:08 am"
    assert format_arrival(board[1].arrivals[0]) == "9:21 am"


def test_stop_code_table_comes_from_stops_txt_and_rebuilds(tmp_path):
    import json
    import os
    when = datetime(2026, 11, 14, 9, 0, tzinfo=TORONTO)
    schedule = tmp_path / "schedule"
    install_schedule(schedule, _gtfs_zip(), when)
    table = stop_code_table(schedule)
    assert table["1874"] == "8512"
    assert table["1873"] == "73"
    assert table["8512"] == "8512"
    assert table["100"] == "100"
    saved = json.loads((schedule / "stop-codes.json").read_text(encoding="utf-8"))
    assert saved["1874"] == "8512"
    assert resolve_stop_id(schedule, "missing") == "missing"
    (schedule / "stop-codes.json").write_text('{"1874": "wrong"}', encoding="utf-8")
    future = (schedule / "stop-codes.json").stat().st_mtime + 5
    os.utime(schedule / "fetched-at", (future, future))
    assert resolve_stop_id(schedule, "1874") == "8512"


def test_each_stop_keeps_its_own_routes_and_arrivals(tmp_path):
    text = """
stop 1874
route 28
route 11
stop 5811
route 74
"""
    config = parse_config(text)
    assert config.stops == (("1874", ("28", "11")), ("5811", ("74",)))
    when = datetime(2026, 11, 14, 9, 0, tzinfo=TORONTO)
    schedule = tmp_path / "schedule"
    install_schedule(schedule, _gtfs_zip(), when)
    assert resolve_stop_id(schedule, "5811") == "2123"
    path = tmp_path / "transit.conf"
    path.write_text(text, encoding="utf-8")
    first = int(datetime(2026, 11, 14, 14, 8, tzinfo=timezone.utc).timestamp())
    second = int(datetime(2026, 11, 14, 14, 21, tzinfo=timezone.utc).timestamp())
    third = int(datetime(2026, 11, 14, 14, 30, tzinfo=timezone.utc).timestamp())
    payload = _feed(
        _entity("28", [_stop_time("8512", first)]),
        _entity("11", [_stop_time("8512", second)]),
        _entity("74", [_stop_time("2123", third)]),
        _entity("74", [_stop_time("8512", first)]),
    )
    board = current_board(
        when,
        config_path=path,
        cache_path=tmp_path / "board.json",
        schedule_dir=schedule,
        download=False,
        fetch=lambda: payload,
    )
    assert [(item.route_id, item.state, tuple(format_arrival(m) for m in item.arrivals)) for item in board] == [
        ("28", "times", ("9:08 am",)),
        ("11", "times", ("9:21 am",)),
        ("74", "times", ("9:30 am",)),
    ]
