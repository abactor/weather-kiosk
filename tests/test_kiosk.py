"""Selection, parse, and HTML tests. No network."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from weather_kiosk.__main__ import main
from weather_kiosk.fetch import ForecastParseError, parse_hourly
from weather_kiosk.render import format_window, render_page
from weather_kiosk.select import TORONTO, forecast_day, select_slots

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "toronto_on-143.json"
DAY_HOURS = (6, 8, 10, 12, 14, 16, 18)


@pytest.fixture(scope="module")
def records():
    return parse_hourly(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _clock(text):
    return datetime.fromisoformat(text)


def _hours(slots):
    return [slot.local_time.hour for slot in slots]


def test_mid_morning_starts_at_the_current_hour(records):
    now = _clock("2026-11-13T10:30:00-05:00")
    slots = select_slots(now, records)
    assert _hours(slots) == [10, 11, 12, 13, 14, 15, 16]
    assert {slot.label_date.isoformat() for slot in slots} == {"2026-11-13"}
    assert slots[0].temperature_c == 4
    assert slots[0].condition == "Cloudy"
    noon = slots[2]
    assert noon.local_time.hour == 12
    assert noon.local_time.minute == 0
    assert noon.temperature_c == 6
    assert noon.condition == "Light rain"
    assert noon.precip_percent == 60


def test_exact_noon_includes_noon(records):
    slots = select_slots(_clock("2026-11-13T12:00:00-05:00"), records)
    assert _hours(slots) == [12, 13, 14, 15, 16, 17, 18]
    assert slots[0].label_date.isoformat() == "2026-11-13"
    assert slots[-1].label_date.isoformat() == "2026-11-13"
    assert slots[0].temperature_c == 6


def test_exact_ten_includes_ten(records):
    slots = select_slots(_clock("2026-11-13T10:00:00-05:00"), records)
    assert _hours(slots) == [10, 11, 12, 13, 14, 15, 16]
    assert slots[0].temperature_c == 4
    assert slots[0].condition == "Cloudy"


def test_exact_six_shows_full_today(records):
    slots = select_slots(_clock("2026-11-13T06:00:00-05:00"), records)
    assert {slot.label_date.isoformat() for slot in slots} == {"2026-11-13"}
    assert _hours(slots) == [6, 7, 8, 9, 10, 11, 12]
    assert [
        (slot.temperature_c, slot.condition, slot.precip_percent) for slot in slots
    ] == [
        (1, "Clear", 10),
        (8, "Cloudy", 15),
        (2, "Mainly sunny", 10),
        (8, "Cloudy", 15),
        (4, "Cloudy", 20),
        (8, "Cloudy", 15),
        (6, "Light rain", 60),
    ]


def test_mid_afternoon_slides_forward(records):
    slots = select_slots(_clock("2026-11-13T15:30:00-05:00"), records)
    assert _hours(slots) == [15, 16, 17, 18, 19, 20, 21]
    assert slots[0].label_date.isoformat() == "2026-11-13"
    assert slots[0].temperature_c == 8
    assert slots[0].condition == "Cloudy"
    assert slots[-1].label_date.isoformat() == "2026-11-13"


def test_six_pm_continues_at_six_am(records):
    now = _clock("2026-11-13T18:00:00-05:00")
    slots = select_slots(now, records)
    assert forecast_day(now).isoformat() == "2026-11-13"
    assert [
        (slot.local_time.date().isoformat(), slot.local_time.hour) for slot in slots
    ] == [
        ("2026-11-13", 18),
        ("2026-11-13", 19),
        ("2026-11-13", 20),
        ("2026-11-13", 21),
        ("2026-11-13", 22),
        ("2026-11-14", 6),
        ("2026-11-14", 7),
    ]


def test_late_afternoon_keeps_the_current_hour(records):
    now = _clock("2026-11-13T16:51:00-05:00")
    slots = select_slots(now, records)
    assert _hours(slots) == [16, 17, 18, 19, 20, 21, 22]
    assert slots[0].temperature_c == 5
    assert slots[0].condition == "Rain"


def test_four_pm_includes_the_current_hour(records):
    slots = select_slots(_clock("2026-11-13T16:00:00-05:00"), records)
    assert _hours(slots) == [16, 17, 18, 19, 20, 21, 22]
    assert slots[0].temperature_c == 5
    assert slots[0].condition == "Rain"


def test_seven_pm_drops_six(records):
    now = _clock("2026-11-13T19:00:00-05:00")
    slots = select_slots(now, records)
    assert forecast_day(now).isoformat() == "2026-11-13"
    assert _hours(slots) == [19, 20, 21, 22, 6, 7, 8]


def test_ten_thirty_pm_keeps_the_current_hour(records):
    now = _clock("2026-11-13T22:30:00-05:00")
    slots = select_slots(now, records)
    assert forecast_day(now).isoformat() == "2026-11-13"
    assert _hours(slots) == [22, 6, 7, 8, 9, 10, 11]
    assert slots[0].temperature_c == 2
    assert slots[0].condition == "Cloudy"
    assert slots[1].label_date.isoformat() == "2026-11-14"
    assert all(slot.condition != "Night fog" for slot in slots)


def test_after_midnight_shows_coming_daytime(records):
    now = _clock("2026-11-14T02:00:00-05:00")
    slots = select_slots(now, records)
    assert forecast_day(now).isoformat() == "2026-11-14"
    assert _hours(slots) == [6, 7, 8, 9, 10, 11, 12]
    assert {slot.label_date.isoformat() for slot in slots} == {"2026-11-14"}
    assert slots[0].temperature_c == -1
    assert slots[0].condition == "Partly cloudy"
    assert slots[0].local_time.astimezone(TORONTO).hour == 6


def test_missing_slot_is_omitted(records):
    kept = []
    for record in records:
        local = record.timestamp.astimezone(TORONTO)
        if local.date().isoformat() == "2026-11-13" and local.hour == 14 and local.minute == 0:
            continue
        kept.append(record)
    slots = select_slots(_clock("2026-11-13T10:30:00-05:00"), kept)
    assert _hours(slots) == [10, 11, 12, 13, 15, 16]
    assert [slot.temperature_c for slot in slots] == [4, 8, 6, 8, 8, 5]


def test_overnight_hours_are_never_selected(records):
    moments = [
        "2026-11-13T10:30:00-05:00",
        "2026-11-13T16:51:00-05:00",
        "2026-11-13T18:00:00-05:00",
        "2026-11-13T22:30:00-05:00",
        "2026-11-14T02:00:00-05:00",
        "2026-11-13T00:30:00-05:00",
    ]
    saw_overnight_record = False
    for record in records:
        local = record.timestamp.astimezone(TORONTO)
        if local.hour in (0, 2, 4):
            saw_overnight_record = True
            assert record.condition == "Night fog"
    assert saw_overnight_record
    for moment in moments:
        slots = select_slots(_clock(moment), records)
        assert slots
        for slot in slots:
            assert 6 <= slot.local_time.hour <= 22
            assert slot.local_time.minute == 0
            assert slot.condition != "Night fog"


def test_html_has_forecast_and_no_clothing_copy(records):
    now = _clock("2026-11-13T10:30:00-05:00")
    page = render_page(forecast_day(now), select_slots(now, records))
    assert "Friday, November 13, 2026" in page
    assert "10:00 AM" in page
    assert "12:00 PM" in page
    assert ">Fri<" in page
    assert "2:00 PM" in page
    assert "14:00" not in page
    assert 'content="60"' in page
    assert "rgba(176, 188, 201, 0.55)" in page
    assert page.count("<polyline") == 1
    assert "6\u00b0C" in page
    assert "Light rain" in page
    assert "60% PoP" in page
    assert "chance of precipitation" not in page
    assert 'class="icon icon-rain"' in page
    assert 'class="plot"' in page
    assert "background: #0c1218" in page
    lowered = page.lower()
    for banned in ("clothing", "wear", "outfit"):
        assert banned not in lowered
    assert "http://" not in lowered
    assert "https://" not in lowered


def test_parse_reads_english_hourly_fields():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parsed = parse_hourly(payload)
    assert len(parsed) == 47
    friday_noon = [
        record
        for record in parsed
        if record.timestamp.astimezone(TORONTO).hour == 12
        and record.timestamp.astimezone(TORONTO).minute == 0
        and record.timestamp.astimezone(TORONTO).date().isoformat() == "2026-11-13"
    ]
    assert len(friday_noon) == 1
    assert friday_noon[0].temperature_c == 6
    assert friday_noon[0].precip_percent == 60
    assert friday_noon[0].timestamp.utcoffset().total_seconds() == 0


def test_parse_accepts_numeric_strings_and_rejects_bad_shape():
    payload = {
        "properties": {
            "hourlyForecastGroup": {
                "hourlyForecasts": [
                    {
                        "timestamp": "2026-11-13T17:00:00Z",
                        "temperature": {"value": {"en": "6"}},
                        "condition": {"en": "Light rain"},
                        "lop": {"value": {"en": "60"}},
                    }
                ]
            }
        }
    }
    parsed = parse_hourly(payload)
    assert parsed[0].temperature_c == 6
    assert parsed[0].precip_percent == 60
    with pytest.raises(ForecastParseError, match="hourlyForecastGroup"):
        parse_hourly({"properties": {}})


def test_naive_now_is_rejected(records):
    with pytest.raises(ValueError, match="timezone-aware"):
        select_slots(datetime(2026, 11, 13, 10, 30), records)


def test_cli_fixture_writes_html(tmp_path):
    output = tmp_path / "page.html"
    code = main(
        [
            "--fixture",
            str(FIXTURE),
            "--now",
            "2026-11-13T10:30:00-05:00",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    text = output.read_text(encoding="utf-8")
    assert "Light rain" in text
    assert "6\u00b0C" in text
    assert "60% PoP" in text
    assert 'class="icon icon-rain"' in text


def test_cli_bad_fixture_exits_nonzero(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    code = main(
        [
            "--fixture",
            str(bad),
            "--now",
            "2026-11-13T10:30:00-05:00",
        ]
    )
    assert code == 1
    captured = capsys.readouterr()
    assert "weather-kiosk:" in captured.err
    assert captured.out == ""


def test_icon_kinds_cover_common_conditions():
    from weather_kiosk.icons import icon_kind

    assert icon_kind("Clear") == "clear"
    assert icon_kind("Mainly sunny") == "mainly_sunny"
    assert icon_kind("Partly cloudy") == "partly_cloudy"
    assert icon_kind("Cloudy") == "cloudy"
    assert icon_kind("Light rain") == "rain"
    assert icon_kind("Snow") == "snow"
    assert icon_kind("Flurries") == "flurries"
    assert icon_kind("Chance of rain showers or flurries") == "mix"
    assert icon_kind("Fog") == "fog"




def test_overnight_line_has_a_gap(records):
    now = _clock("2026-11-13T18:00:00-05:00")
    page = render_page(forecast_day(now), select_slots(now, records))
    assert page.count("<polyline") == 2


def test_full_forecast_starts_now_and_can_return_to_present(records):
    now = _clock("2026-11-13T10:30:00-05:00")
    slots = select_slots(now, records, count=None)
    hours = _hours(slots)
    assert hours[0] == 10
    assert 17 in hours
    assert 22 in hours
    assert 1 not in hours
    page = render_page(forecast_day(now), slots)
    assert 'data-visible="7"' in page
    assert "index = maxIndex" in page
    assert "index = index + 2" in page
    assert "index = index - 2" in page
    assert "index = 0" in page
    assert page.count("<polyline") >= 2




def test_current_observation_is_the_leftmost_dot(records):
    from weather_kiosk.fetch import CurrentConditions

    now = _clock("2026-11-13T20:13:00-05:00")
    current = CurrentConditions(temperature_c=3.0, condition="Cloudy")
    slots = select_slots(now, records, current=current)
    assert (slots[0].local_time.hour, slots[0].local_time.minute) == (20, 13)
    assert slots[0].temperature_c == 3.0
    assert slots[0].condition == "Cloudy"
    assert slots[0].precip_percent is None
    assert slots[1].local_time.hour == 21
    assert slots[1].precip_percent == 15
    page = render_page(forecast_day(now), slots)
    assert "8:13 PM" in page
    assert "9:00 PM" in page
    pops = [part.split("<", 1)[0] for part in page.split('class="pop">')[1:]]
    assert pops[0] == ""
    assert pops[1] == "15% PoP"
    body = page.split("<body>", 1)[1]
    assert body.index('class="time-day"') < body.index('class="time">')
    assert body.index('class="time">') < body.index('class="pop"')
    assert "user-select: none" in page
    assert "selectstart" in page
    assert page.count("<polyline") == 2


def test_current_time_does_not_borrow_the_next_forecast(records):
    from weather_kiosk.fetch import CurrentConditions

    now = _clock("2026-11-13T20:13:00-05:00")
    remaining = [
        record
        for record in records
        if not (
            record.timestamp.astimezone(TORONTO).hour == 20
            and record.timestamp.astimezone(TORONTO).date().isoformat() == "2026-11-13"
        )
    ]
    current = CurrentConditions(temperature_c=3.0, condition="Cloudy")
    slots = select_slots(now, remaining, current=current)
    assert slots[0].precip_percent is None
    assert slots[0].temperature_c == 3.0
    assert slots[1].precip_percent == 15


def test_parse_current_reads_observation_and_allows_absence():
    from weather_kiosk.fetch import parse_current

    assert parse_current({"properties": {}}) is None
    parsed = parse_current(
        {
            "properties": {
                "currentConditions": {
                    "temperature": {"value": {"en": 3.0}},
                    "condition": {"en": "Cloudy"},
                }
            }
        }
    )
    assert parsed.temperature_c == 3.0
    assert parsed.condition == "Cloudy"
    assert parsed.humidity_percent is None
    assert parsed.wind_kmh is None
    detailed = parse_current(
        {
            "properties": {
                "currentConditions": {
                    "temperature": {"value": {"en": 3.0}},
                    "condition": {"en": "Cloudy"},
                    "relativeHumidity": {"value": {"en": 72}},
                    "wind": {"speed": {"value": {"en": 20}}},
                }
            }
        }
    )
    assert detailed.humidity_percent == 72
    assert detailed.wind_kmh == 20
    skipped = parse_current(
        {
            "properties": {
                "currentConditions": {
                    "temperature": {"value": {"en": 3.0}},
                    "condition": {"en": "Cloudy"},
                    "relativeHumidity": {"value": {"en": "n/a"}},
                    "wind": {"speed": {"value": {}}},
                }
            }
        }
    )
    assert skipped.humidity_percent is None
    assert skipped.wind_kmh is None




def test_current_column_shows_measured_precipitation_not_pop(records):
    from weather_kiosk.fetch import CurrentConditions

    now = _clock("2026-11-13T20:13:00-05:00")
    current = CurrentConditions(
        temperature_c=4.0,
        condition="Cloudy",
        precip_mm=0.0,
        humidity_percent=65,
        wind_kmh=12,
    )
    slots = select_slots(now, records, current=current)
    assert slots[0].temperature_c == 4.0
    assert slots[0].condition == "Cloudy"
    assert slots[0].precip_percent is None
    assert slots[0].precip_mm == 0.0
    assert slots[0].humidity_percent == 65
    assert slots[0].wind_kmh == 12
    assert slots[1].precip_percent == 15
    assert slots[1].humidity_percent is None
    assert slots[1].wind_kmh is None
    page = render_page(forecast_day(now), slots)
    pops = [part.split("<", 1)[0] for part in page.split('class="pop">')[1:]]
    assert pops[0] == "0 mm"
    assert pops[1] == "15% PoP"
    humidity = [part.split("<", 1)[0] for part in page.split('class="humidity">')[1:]]
    wind = [part.split("<", 1)[0] for part in page.split('class="wind">')[1:]]
    assert humidity[0] == "65% humidity"
    assert humidity[1:] == [""] * (len(humidity) - 1)
    assert wind[0] == "12 km/h wind"
    assert wind[1:] == [""] * (len(wind) - 1)


def test_saved_forecast_fills_the_sky_and_not_the_probability(tmp_path, records):
    from datetime import datetime, timezone

    from weather_kiosk.fetch import CurrentConditions, HourlyRecord
    from weather_kiosk.stash import augment_observation, recall_hour, remember_hourly

    now = datetime(2026, 11, 13, 20, 48, tzinfo=TORONTO)
    path = tmp_path / "toronto-forecast.hourly.json"
    remember_hourly(
        path,
        [
            HourlyRecord(
                timestamp=datetime(2026, 11, 14, 1, 0, tzinfo=timezone.utc),
                temperature_c=2.0,
                condition="Cloudy",
                precip_percent=30,
                wind_kmh=18,
            ),
            HourlyRecord(
                timestamp=datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc),
                temperature_c=3.0,
                condition="Rain",
                precip_percent=10,
            ),
        ],
        now,
    )
    recalled = recall_hour(path, now)
    assert recalled.condition == "Cloudy"
    assert recall_hour(path, datetime(2026, 11, 13, 19, 10, tzinfo=TORONTO)) is None
    filled = augment_observation(
        CurrentConditions(temperature_c=4.0, condition="", precip_mm=0.4),
        recalled,
    )
    assert filled.temperature_c == 4.0
    assert filled.condition == "Cloudy"
    assert filled.precip_mm == 0.4
    assert filled.humidity_percent is None
    assert filled.wind_kmh == 18
    kept = augment_observation(
        CurrentConditions(
            temperature_c=4.0,
            condition="",
            precip_mm=0.4,
            humidity_percent=61,
            wind_kmh=4,
        ),
        recalled,
    )
    assert kept.humidity_percent == 61
    assert kept.wind_kmh == 4
    slots = select_slots(now, records, current=filled)
    assert slots[0].precip_percent is None
    assert slots[0].precip_mm == 0.4
    assert slots[0].condition == "Cloudy"
    assert slots[0].wind_kmh == 18
    assert slots[0].humidity_percent is None
    page = render_page(forecast_day(now), slots)
    wind = [part.split("<", 1)[0] for part in page.split('class="wind">')[1:]]
    assert wind[0] == "18 km/h wind"
    assert wind[1:] == [""] * (len(wind) - 1)
    assert 'class="humidity"' not in page


def test_missing_and_broken_stash_are_ignored(tmp_path):
    from datetime import datetime, timezone

    from weather_kiosk.fetch import CurrentConditions, HourlyRecord
    from weather_kiosk.stash import augment_observation, recall_hour, remember_hourly

    now = datetime(2026, 11, 13, 20, 48, tzinfo=TORONTO)
    assert recall_hour(tmp_path / "missing.json", now) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert recall_hour(bad, now) is None
    filled = augment_observation(CurrentConditions(4.0, "", precip_mm=None), None)
    assert filled.temperature_c == 4.0
    assert filled.condition == ""
    assert filled.precip_mm is None
    assert filled.humidity_percent is None
    assert filled.wind_kmh is None
    page = render_page(forecast_day(now), select_slots(now, [], current=filled))
    assert 'class="humidity"' not in page
    assert 'class="wind"' not in page
    remember_hourly(
        bad,
        [
            HourlyRecord(
                timestamp=datetime(2026, 11, 14, 1, 0, tzinfo=timezone.utc),
                temperature_c=2.0,
                condition="Cloudy",
                precip_percent=30,
            )
        ],
        now,
    )
    assert recall_hour(bad, now).condition == "Cloudy"


def test_blank_condition_draws_no_icon():
    from weather_kiosk.icons import icon_svg

    assert icon_svg("") == ""
    assert icon_svg("   ") == ""


def test_date_line_is_the_leftmost_day_only():
    from datetime import datetime
    from types import SimpleNamespace
    from weather_kiosk.select import TORONTO, ForecastSlot

    same_month = [
        SimpleNamespace(local_time=datetime(2026, 11, 14, 10, 0)),
        SimpleNamespace(local_time=datetime(2026, 11, 15, 15, 0)),
    ]
    assert format_window(same_month[0].local_time.date(), same_month) == (
        "Saturday, November 14, 2026"
    )
    slots = []
    for hour in (16, 18, 20, 22):
        slots.append(ForecastSlot(datetime(2026, 11, 14, hour, tzinfo=TORONTO), 4.0, "Cloudy", 10))
    for hour in (6, 8, 10, 12):
        slots.append(ForecastSlot(datetime(2026, 11, 15, hour, tzinfo=TORONTO), 5.0, "Cloudy", 10))
    page = render_page(slots[0].local_time.date(), slots)
    heading = page.split('class="day">', 1)[1].split("</p>", 1)[0]
    assert heading == "Saturday, November 14, 2026"
    assert " to " not in heading
    dates = page.split('data-dates="', 1)[1].split('"', 1)[0]
    assert "Saturday, November 14, 2026" in dates
    assert dates.index("Saturday, November 14, 2026") < dates.index("Sunday, November 15, 2026")
    assert "dayLine.textContent = shown" in page
