# Weather kiosk

A Toronto forecast panel for a Raspberry Pi 4. It draws an hourly chart and the current observation, and it shows calendar reminders, TTC arrivals, and city alerts. The display dims, then shuts off. A tap wakes it. A tap in the centre dims it. A swipe does not.

## Config

Copy `calendar.url.template` to `~/.config/weather-kiosk/calendar.url` and `transit.conf.template` to `~/.config/weather-kiosk/transit.conf`, then edit them.

- `calendar.url`, mode 600. One iCal URL, or `WEATHER_KIOSK_CALENDAR_URL`. If both are missing, the reminder line stays blank.
- `transit.conf`. Lines of `stop <code>` and `route <id>`. A route belongs to the stop above it. One line may list several ids, separated by commas. `#` starts a comment. If the file is missing, the panel shows no buses.

Finding stop and route numbers:

- The stop code is the public number printed on the TTC stop sign, for example `1874`. The app maps it to the feed's internal stop id, so use the sign number, not the internal id.
- Route numbers are the standard TTC route numbers, for example `28` or `11`.
- A bare number includes that number and one trailing letter. `route 11` matches `11`, `11A`, `11B`, and `11C`. It does not match `111` or `11AB`. Use a bare number when you want every branch of that route. Lower case works in `transit.conf`: `route 11a` is read as `11A`.
- List the ids when you want only some branches. `route 11A, 11B` matches `11A` and `11B` only, not `11` or `11C`. A separate `route` line for each id is the same. A listed id is exact, so `11A` does not match `11AA`.
- Look them up on the TTC website: https://www.ttc.ca/routes-and-schedules. Search by route, stop, or station. A stop page's address ends in the stop code, for example https://www.ttc.ca/routes-and-schedules/11/0/1874 for stop `1874`.

The app writes the rest of that folder.

- `reminder.json`
- `transit-board.json`
- `surface-gtfs/`, the public TTC schedule. Refreshed after 14 days. Large.

The hourly stash is `<output>.hourly.json`, beside the HTML file.

## Deploy

This repo is the source of truth. `/opt/weather-kiosk` is the running copy. Those are real files, not symlinks.

From the repo:

```sh
rsync -a --delete \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'tests/' \
  --exclude 'pytest.ini' \
  --exclude 'README.md' \
  --exclude '.gitignore' \
  ./ \
  /opt/weather-kiosk/
```

Then restart `python3 -m weather_kiosk --interval 60`. `~/.config/labwc/weather-kiosk.sh` starts that process and Chromium. Config is not deployed.

## Tests

77 tests in six files. All of them run. None are skipped or marked xfail. `tests/README.md` lists each file. `tests/fixtures/toronto_on-143.json` is a saved Toronto city-page forecast, used offline.

```sh
pytest tests
```

## Branches

`master` and `feature/marquee-truncation`.
