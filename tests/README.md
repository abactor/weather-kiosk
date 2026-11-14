# Tests

76 tests. Pytest collects all of them. None are skipped or marked xfail.

| File | Covers |
| --- | --- |
| `test_kiosk.py` | Which hours are shown, parsing, icons, the chart, the current observation, and the hourly stash. |
| `test_calendar.py` | Which events become the reminder line, including tomorrow, clocks, descriptions, and a failed fetch. |
| `test_transit.py` | Stop and route config, the next two arrivals, a missing route, the five-minute cache, and the panel. |
| `test_alerts.py` | City alerts, the red line, and a reminder staying white. |
| `test_fetch.py` | A local stand-in for the weather fetch, CLI output, and the interval loop. |
| `test_display_power.py` | Idle dim, shutoff, wake, a centre tap, and a swipe that does not dim. |

`fixtures/toronto_on-143.json` is a saved Toronto city-page forecast, GeoMet id `on-143`. The tests read it offline. They do not call the weather service.

From the repo root: `pytest tests`
