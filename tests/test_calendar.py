"""Reminder selection from an iCalendar feed. No network."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from weather_kiosk.calendar import calendar_url, fetch_reminder, reminder_text
from weather_kiosk.select import TORONTO


def _at(hour, minute=0, day=13):
    return datetime(2026, 11, day, hour, minute, tzinfo=TORONTO)


ALL_DAY = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261113
DTEND;VALUE=DATE:20261114
SUMMARY:Pack the library books
END:VEVENT
END:VCALENDAR
"""

TIMED = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261113
DTEND;VALUE=DATE:20261114
SUMMARY:Library books
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261113T200000
DTEND;TZID=America/Toronto:20261113T210000
SUMMARY:Call grandma
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261113T213000
DTEND;TZID=America/Toronto:20261113T214500
SUMMARY:Later errand
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

WEEKLY = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261109T073000
DTEND;TZID=America/Toronto:20261109T074500
RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR
EXDATE;TZID=America/Toronto:20261120T073000
SUMMARY:Leave for school
END:VEVENT
END:VCALENDAR
"""

CANCELLED = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261113
DTEND;VALUE=DATE:20261114
SUMMARY:Ignore me
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""


def test_all_day_event_is_todays_reminder():
    assert reminder_text(ALL_DAY, _at(21, 15)) == "Pack the library books"
    assert reminder_text(ALL_DAY, _at(21, 15, day=14)) == ""


def test_in_progress_event_beats_the_all_day_line():
    assert reminder_text(TIMED, _at(20, 15)) == (
        "Library books"
        " · Fri 8:00 pm: Call grandma"
        " · Fri 9:30 pm: Later errand"
    )


def test_all_day_line_stays_until_the_next_event_starts():
    assert reminder_text(TIMED, _at(19, 0)) == (
        "Library books"
        " · Fri 8:00 pm: Call grandma"
        " · Fri 9:30 pm: Later errand"
    )


def test_next_timed_event_shows_when_nothing_else_is_on_today():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20261114T013000Z
DTEND:20261114T020000Z
SUMMARY:Call grandma
END:VEVENT
END:VCALENDAR
"""
    assert reminder_text(ics, _at(19, 0)) == "Fri 8:30 pm: Call grandma"
    assert reminder_text(ics, _at(22, 0)) == ""


def test_weekly_rule_matches_friday_and_honours_an_exception():
    assert reminder_text(WEEKLY, _at(7, 0)) == "Fri 7:30 am: Leave for school"
    assert reminder_text(WEEKLY, _at(7, 35)) == "Fri 7:30 am: Leave for school"
    assert reminder_text(WEEKLY, _at(8, 0)) == ""
    assert reminder_text(WEEKLY, _at(7, 10, day=14)) == ""
    assert reminder_text(WEEKLY, datetime(2026, 11, 20, 7, 10, tzinfo=TORONTO)) == ""


def test_cancelled_event_is_ignored():
    assert reminder_text(CANCELLED, _at(12)) == ""


def test_calendar_file_supplies_the_url_and_a_miss_stays_blank(tmp_path, monkeypatch):
    monkeypatch.delenv("WEATHER_KIOSK_CALENDAR_URL", raising=False)
    path = tmp_path / "calendar.url"
    assert calendar_url(path) is None
    path.write_text("# secret lives here\n\n", encoding="utf-8")
    assert calendar_url(path) is None
    path.write_text("https://calendar.example/basic.ics\n", encoding="utf-8")
    assert calendar_url(path) == "https://calendar.example/basic.ics"
    assert fetch_reminder(_at(12), url="", cache_path=tmp_path / "reminder.json") == ""


def test_failed_fetch_reuses_todays_saved_reminder(tmp_path):
    cache = tmp_path / "reminder.json"
    assert (
        fetch_reminder(
            _at(12),
            url="http://127.0.0.1:1/basic.ics",
            cache_path=cache,
            timeout=0.2,
        )
        == ""
    )
    cache.write_text(
        '{"date": "2026-11-13", "text": "Library books"}',
        encoding="utf-8",
    )
    assert (
        fetch_reminder(
            _at(18),
            url="http://127.0.0.1:1/basic.ics",
            cache_path=cache,
            timeout=0.2,
        )
        == "Library books"
    )
    assert (
        fetch_reminder(
            _at(18, day=14),
            url="http://127.0.0.1:1/basic.ics",
            cache_path=cache,
            timeout=0.2,
        )
        == ""
    )


def test_empty_today_shows_tomorrows_all_day_event():
    """An empty today lists tomorrow's all-day event and its timed events on one line."""
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261114
DTEND;VALUE=DATE:20261115
SUMMARY:Pack the library books
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261114T100000
DTEND;TZID=America/Toronto:20261114T110000
SUMMARY:Call grandma
END:VEVENT
END:VCALENDAR
"""
    assert reminder_text(ics, _at(21, 15)) == (
        "Tomorrow: Pack the library books · Sat 10:00 am: Call grandma"
    )


def test_empty_today_shows_tomorrows_first_timed_event():
    """An empty today lists every tomorrow event, earliest first, not only the first."""
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261114T150000
DTEND;TZID=America/Toronto:20261114T160000
SUMMARY:Later errand
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261114T100000
DTEND;TZID=America/Toronto:20261114T110000
SUMMARY:Call grandma
END:VEVENT
END:VCALENDAR
"""
    assert reminder_text(ics, _at(21, 15)) == (
        "Tomorrow: Sat 10:00 am: Call grandma · Sat 3:00 pm: Later errand"
    )


def test_timed_event_shows_a_readable_clock_on_the_panel():
    from datetime import date

    from weather_kiosk.render import render_page

    text = reminder_text(TIMED, _at(20, 15))
    page = render_page(date(2026, 11, 13), [], [], text)
    assert 'class="reminder"' in page
    assert "Fri 8:00 pm: Call grandma" in page
    assert "20261113T200000" not in page

    utc = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20261114T013000Z
DTEND:20261114T020000Z
SUMMARY:Call grandma
END:VEVENT
END:VCALENDAR
"""
    utc_page = render_page(date(2026, 11, 13), [], [], reminder_text(utc, _at(19, 0)))
    assert "Fri 8:30 pm: Call grandma" in utc_page
    assert "20261114T013000Z" not in utc_page

    allday = render_page(date(2026, 11, 13), [], [], reminder_text(ALL_DAY, _at(12)))
    assert "Pack the library books" in allday
    assert "20261113T" not in allday



def test_description_follows_the_title():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261113T200000
DTEND;TZID=America/Toronto:20261113T210000
SUMMARY:Call grandma
DESCRIPTION:Ask about Sunday lunch
END:VEVENT
END:VCALENDAR
"""
    assert (
        reminder_text(ics, _at(20, 15))
        == "Fri 8:00 pm: Call grandma — Ask about Sunday lunch"
    )
    wrapped = ics.replace(
        "DESCRIPTION:Ask about Sunday lunch",
        "DESCRIPTION:Ask about Sunday\\nBring the list",
    )
    assert (
        reminder_text(wrapped, _at(20, 15))
        == "Fri 8:00 pm: Call grandma — Ask about Sunday Bring the list"
    )
    blank = ics.replace("DESCRIPTION:Ask about Sunday lunch", "DESCRIPTION:")
    assert reminder_text(blank, _at(20, 15)) == "Fri 8:00 pm: Call grandma"


def test_long_description_scrolls_on_the_reminder_line():
    from datetime import date

    from weather_kiosk.render import render_page

    description = "Bring the blue folder and the spare key from the kitchen drawer."
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261113T200000
DTEND;TZID=America/Toronto:20261113T210000
SUMMARY:Call grandma
DESCRIPTION:%s
END:VEVENT
END:VCALENDAR
""" % description
    page = render_page(date(2026, 11, 13), [], [], reminder_text(ics, _at(20, 15)))
    assert description in page
    assert 'class="alert-text alert-marquee"' not in page
    assert "text.scrollWidth > line.clientWidth" in page
    assert "distance / pixelsPerSecond" in page
    assert "pixelsPerSecond = 75" in page
    assert "weather-kiosk-marquee" in page
    assert "queued" in page
    assert "translateX(-50%)" in page
    assert "padding-right: 48px" in page
    assert "padding-left: 100%" not in page


def test_overlapping_all_day_events_share_one_scrolling_line():
    from datetime import date

    from weather_kiosk.render import render_page

    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261113
DTEND;VALUE=DATE:20261114
SUMMARY:Pack the library books
DESCRIPTION:Bring the bag
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261113
DTEND;VALUE=DATE:20261114
SUMMARY:Dentist
DESCRIPTION:Morning only
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261112
DTEND;VALUE=DATE:20261115
SUMMARY:Recycling
END:VEVENT
END:VCALENDAR
"""
    line = reminder_text(ics, _at(12))
    assert line == (
        "Fri Nov 13: Pack the library books — Bring the bag"
        " · Fri Nov 13: Dentist — Morning only"
        " · Thu Nov 12: Recycling"
    )
    page = render_page(date(2026, 11, 13), [], [], line)
    assert 'class="reminder"' in page
    assert line in page
    assert 'class="alert-text alert-marquee"' not in page
    assert "text.scrollWidth > line.clientWidth" in page

    pair = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261113
DTEND;VALUE=DATE:20261114
SUMMARY:Pack the library books
DESCRIPTION:Bring the bag
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261113
DTEND;VALUE=DATE:20261114
SUMMARY:Dentist
DESCRIPTION:Morning only
END:VEVENT
END:VCALENDAR
"""
    assert reminder_text(pair, _at(12)) == (
        "Fri Nov 13: Pack the library books — Bring the bag"
        " · Fri Nov 13: Dentist — Morning only"
    )


def test_today_and_tomorrow_list_every_event():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261113
DTEND;VALUE=DATE:20261114
SUMMARY:Library books
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261113T200000
DTEND;TZID=America/Toronto:20261113T210000
SUMMARY:Call grandma
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261114
DTEND;VALUE=DATE:20261115
SUMMARY:Pack the library books
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261114T100000
DTEND;TZID=America/Toronto:20261114T110000
SUMMARY:Later errand
END:VEVENT
END:VCALENDAR
"""
    assert reminder_text(ics, _at(21, 15)) == (
        "Library books"
        " · Tomorrow: Pack the library books"
        " · Sat 10:00 am: Later errand"
    )


def test_ended_events_drop_off_and_later_ones_stay():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261113T120000
DTEND;TZID=America/Toronto:20261113T130000
SUMMARY:Earlier today
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261113T230000
DTEND;TZID=America/Toronto:20261114T000000
SUMMARY:Later today
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261114T100000
DTEND;TZID=America/Toronto:20261114T110000
SUMMARY:Call grandma
END:VEVENT
END:VCALENDAR
"""
    assert reminder_text(ics, _at(21, 15)) == (
        "Fri 11:00 pm: Later today · Tomorrow: Sat 10:00 am: Call grandma"
    )
