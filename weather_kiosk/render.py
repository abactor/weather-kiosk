"""Render selected forecast slots to one offline HTML page.

The page targets an 800x480 Raspberry Pi DSI panel. It uses no external
assets, fonts, or network URLs. The background is dark because a failed
panel comes up solid white.
"""

from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta
from typing import List, Sequence

from weather_kiosk.alerts import alert_line
from weather_kiosk.icons import icon_svg
from weather_kiosk.transit import format_arrival

_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_CSS = """
html, body {
  margin: 0;
  width: 800px;
  height: 480px;
  overflow: hidden;
  background: #0c1218;
  color: #e7eef6;
  user-select: none;
  -webkit-user-select: none;
  touch-action: none;
}
body {
  position: relative;
  width: 800px;
  height: 480px;
  box-sizing: border-box;
  padding: 12px 16px 8px;
  font-family: sans-serif;
  line-height: 1.1;
}
h1 {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
}
.day {
  margin: 2px 0 4px;
  font-size: 20px;
  line-height: 22px;
  color: #b7c6d6;
}
.alert,
.reminder {
  margin: 0 0 6px;
  height: 22px;
  overflow: hidden;
  font-size: 18px;
  font-weight: 700;
  line-height: 22px;
}
.alert {
  color: #ff2a2a;
}
.reminder {
  color: #ffffff;
}
html.power-dim::before,
html.power-off::before {
  content: "";
  position: fixed;
  left: 0;
  top: 0;
  width: 100%;
  height: 100%;
  z-index: 10000;
}
html.power-dim::before {
  background: rgba(0, 0, 0, 0.62);
}
html.power-off::before {
  background: #000000;
}
.alert-text {
  display: inline-block;
  white-space: nowrap;
}
.alert-chunk {
  display: inline-block;
}
.alert-marquee .alert-chunk {
  padding-right: 48px;
}
.alert-marquee {
  animation-name: alert-marquee;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
}
@keyframes alert-marquee {
  from { transform: translateX(0); }
  to { transform: translateX(-50%); }
}
.viewport {
  width: 768px;
  overflow: hidden;
  touch-action: none;
}
.chart {
  width: 768px;
}
.cols {
  display: grid;
  text-align: center;
}
.plot-wrap {
  position: relative;
  width: 100%;
  height: 206px;
}
.plot {
  position: absolute;
  left: 0;
  top: 0;
  width: 100%;
  height: 206px;
  display: block;
  margin: 0;
}
.marker {
  position: absolute;
  z-index: 1;
  text-align: center;
  transform: translate(-50%, calc(-100% - 16px));
}
.icon {
  display: block;
  margin: 0 auto;
}
.temp {
  font-size: 22px;
  font-weight: 700;
  margin-top: 0;
  line-height: 1;
}
.time {
  font-size: 22px;
  font-weight: 700;
  margin-top: 4px;
}
.time-day {
  margin-top: 1px;
  font-size: 15px;
  color: #b7c6d6;
}
.condition {
  margin-top: 6px;
  padding: 0 8px;
  box-sizing: border-box;
  font-size: 16px;
  line-height: 1.2;
  color: #d5dee8;
}
.pop {
  margin-top: 0;
  font-size: 14px;
  line-height: 1;
  color: rgba(176, 188, 201, 0.55);
}
.humidity,
.wind {
  margin-top: 1px;
  font-size: 13px;
  line-height: 1.1;
  color: #c5d4e4;
  overflow: hidden;
  white-space: nowrap;
}
.empty {
  font-size: 24px;
}
.buses {
  position: absolute;
  top: 12px;
  right: 16px;
  z-index: 2;
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 5px;
}
.route-row {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 16px;
  height: 24px;
  white-space: nowrap;
}
.route {
  display: grid;
  grid-template-columns: 40px 90px 72px;
  align-items: center;
  column-gap: 8px;
  height: 24px;
  white-space: nowrap;
}
.badge {
  min-width: 40px;
  padding: 0 4px;
  box-sizing: border-box;
  text-align: center;
  font-size: 15px;
  font-weight: 700;
  line-height: 22px;
  color: #121820;
  background: #ff5a36;
  border-radius: 4px;
}
.next {
  font-size: 20px;
  font-weight: 700;
  line-height: 22px;
  color: #ffffff;
  white-space: nowrap;
}
.later {
  font-size: 14px;
  font-weight: 500;
  line-height: 22px;
  color: #b7c6d6;
  white-space: nowrap;
}
.note {
  grid-column: 2 / span 2;
  font-size: 15px;
  font-weight: 600;
  line-height: 22px;
  color: #c5d0dc;
}
.missing {
  grid-column: 2 / span 2;
  color: #1c1408;
  background: #e0a020;
  border-radius: 3px;
  padding: 1px 6px;
  font-size: 14px;
  font-weight: 700;
  line-height: 20px;
  animation: bus-warn 0.9s ease-out 1;
}
@keyframes bus-warn {
  from { background: #fff4cc; }
  to { background: #e0a020; }
}
@media (prefers-reduced-motion: reduce) {
  .missing { animation: none; }
}
"""


def format_day(day: date) -> str:
    """English weekday and date, independent of the process locale."""
    return "%s, %s %d, %d" % (
        _WEEKDAYS[day.weekday()],
        _MONTHS[day.month - 1],
        day.day,
        day.year,
    )


def format_window(day: date, slots: Sequence) -> str:
    """The leftmost column's date. One line, never a range."""
    for slot in slots:
        return format_day(slot.local_time.date())
    return format_day(day)


def format_clock(local_time: datetime) -> str:
    hour = local_time.hour
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12
    if hour12 == 0:
        hour12 = 12
    return "%d:%02d %s" % (hour12, local_time.minute, suffix)


def format_temperature(celsius: float) -> str:
    number = float(celsius)
    if number.is_integer():
        text = str(int(number))
    else:
        text = "%.1f" % number
    return text + "\u00b0C"


def format_pop(percent: int) -> str:
    return "%s%% PoP" % int(percent)


def format_precip_mm(millimetres: float) -> str:
    """Measured precipitation for the current column. Not a probability."""
    number = float(millimetres)
    if number <= 0:
        return "0 mm"
    if abs(number - round(number)) < 0.05:
        return "%d mm" % int(round(number))
    return "%.1f mm" % number


def format_humidity(percent: float) -> str:
    """Station relative humidity for the current column."""
    number = float(percent)
    if number.is_integer():
        text = str(int(number))
    else:
        text = "%.1f" % number
    return text + "% humidity"


def format_wind(kmh: float) -> str:
    """Station wind speed for the current column, in kilometres per hour."""
    number = float(kmh)
    if abs(number - round(number)) < 0.05:
        text = str(int(round(number)))
    else:
        text = "%.1f" % number
    return text + " km/h wind"

def _measure_row(kind, values, columns):
    """One grid row for the current column. Omitted when every cell is empty."""
    if not any(value is not None for value in values):
        return ""
    cells = []
    for value in values:
        shown = "" if value is None else html.escape(value)
        cells.append('<div class="%s">%s</div>' % (kind, shown))
    return (
        '<div class="cols %s-row" style="grid-template-columns: %s">\n%s\n</div>\n'
        % (kind, columns, "\n".join(cells))
    )


def render_page(day: date, slots: Sequence, alerts: Sequence = (), reminder: str = "", boards: Sequence = ()) -> str:
    """Return a complete HTML document for the given daytime slots."""
    if slots:
        block = _chart_html(slots)
    else:
        block = (
            '<p class="empty">No forecast hours are available for this daytime.</p>'
        )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Toronto forecast</title>\n"
        '<meta http-equiv="refresh" content="60">\n'
        "<style>\n"
        + _CSS
        + "</style>\n"
        "</head>\n"
        "<body>\n"
        "<h1>Toronto forecast</h1>\n"
        '<p class="day">'
        + html.escape(format_window(day, slots))
        + "</p>\n"
        + _alert_html(alerts, reminder)
        + _buses_html(boards)
        + _FIT_SCRIPT
        + _POWER_SCRIPT
        + block
        + "\n"
        + _SWIPE_SCRIPT
        + "</body>\n</html>\n"
    )



def _buses_html(boards: Sequence) -> str:
    """Top-right arrivals. Omitted when there is nothing to show."""
    if not boards:
        return ""
    primary = list(boards[:3])
    overflow = list(boards[3:])
    top = [_route_html(board) for board in reversed(overflow)]
    top.append(_route_html(primary[0]))
    rows = ['<li class="route-row">%s</li>' % "".join(top)]
    for board in primary[1:]:
        rows.append('<li class="route-row">%s</li>' % _route_html(board))
    return '<ul class="buses">\n%s\n</ul>\n' % "\n".join(rows)


def _route_html(board) -> str:
    badge = '<span class="badge">%s</span>' % html.escape(str(board.route_id))
    if board.state == "times" and board.arrivals:
        shown = [badge, '<span class="next">%s</span>' % html.escape(format_arrival(board.arrivals[0]))]
        second = ""
        if len(board.arrivals) > 1:
            second = html.escape(format_arrival(board.arrivals[1]))
        shown.append('<span class="later">%s</span>' % second)
    elif board.state == "not_at_stop":
        shown = [badge, '<span class="missing">does not serve that stop</span>']
    else:
        shown = [badge, '<span class="note">no bus</span>']
    return '<span class="route">%s</span>' % "".join(shown)


def _alert_html(alerts: Sequence, reminder: str = "") -> str:
    """One line under the date.

    An alert is red and scrolls when it is long. With no alert, the same
    line stays reserved in white for a daily reminder.
    """
    line = alert_line(alerts)
    if line:
        return _banner("alert", line)
    return _banner("reminder", " ".join(str(reminder).split()))


def _banner(kind: str, line: str) -> str:
    if not line:
        return '<p class="%s"></p>\n' % kind
    return (
        '<p class="%s"><span class="alert-text"><span class="alert-chunk">%s</span></span></p>\n'
        % (kind, html.escape(line))
    )


def _chart_html(slots: Sequence) -> str:
    count = len(slots)
    visible = count if count < _VISIBLE_SLOTS else _VISIBLE_SLOTS
    if count > _VISIBLE_SLOTS:
        chart_width = _PLOT_WIDTH * float(count) / float(_VISIBLE_SLOTS)
    else:
        chart_width = _PLOT_WIDTH
    columns = "repeat(%d, 1fr)" % count
    points = _plot_points(slots, chart_width)
    markers = []
    days = []
    times = []
    conditions = []
    pops = []
    for index, slot in enumerate(slots):
        _x, y = points[index]
        left = (index + 0.5) * 100.0 / float(count)
        markers.append(
            '<div class="marker" style="left: %.3f%%; top: %.1fpx">%s'
            '<div class="temp">%s</div></div>'
            % (
                left,
                y,
                icon_svg(slot.condition),
                html.escape(format_temperature(slot.temperature_c)),
            )
        )
        label = format_clock(slot.local_time)
        day_name = _WEEKDAYS[slot.local_time.weekday()][:3]
        days.append('<div class="time-day">%s</div>' % html.escape(day_name))
        times.append('<div class="time">%s</div>' % html.escape(label))
        conditions.append(
            '<div class="condition">%s</div>' % html.escape(slot.condition)
        )
        if slot.precip_mm is not None:
            pop_text = format_precip_mm(slot.precip_mm)
        elif slot.precip_percent is None:
            pop_text = ""
        else:
            pop_text = format_pop(slot.precip_percent)
        pops.append('<div class="pop">%s</div>' % html.escape(pop_text))
    humidity_row = _measure_row(
        "humidity",
        [
            format_humidity(slot.humidity_percent)
            if slot.humidity_percent is not None
            else None
            for slot in slots
        ],
        columns,
    )
    wind_row = _measure_row(
        "wind",
        [
            format_wind(slot.wind_kmh) if slot.wind_kmh is not None else None
            for slot in slots
        ],
        columns,
    )
    return (
        '<div class="viewport" data-count="%d" data-visible="%d" data-dates="%s">\n'
        '<div class="chart" style="width: %.1fpx">\n'
        '<div class="plot-wrap">\n'
        "%s\n"
        "%s\n"
        "</div>\n"
        '<div class="cols day-row" style="grid-template-columns: %s">\n%s\n</div>\n'
        '<div class="cols" style="grid-template-columns: %s">\n%s\n</div>\n'
        '<div class="cols pop-row" style="grid-template-columns: %s">\n%s\n</div>\n'
        "%s"
        "%s"
        '<div class="cols" style="grid-template-columns: %s">\n%s\n</div>\n'
        "</div>\n"
        "</div>"
    ) % (
        count,
        visible,
        html.escape(json.dumps([format_day(slot.local_time.date()) for slot in slots]), quote=True),
        chart_width,
        _plot_svg(slots, points, chart_width),
        "\n".join(markers),
        columns,
        "\n".join(days),
        columns,
        "\n".join(times),
        columns,
        "\n".join(pops),
        humidity_row,
        wind_row,
        columns,
        "\n".join(conditions),
    )


_VISIBLE_SLOTS = 7
_FIT_SCRIPT = """<script>
(function () {
  var KEY = "weather-kiosk-marquee";
  var pixelsPerSecond = 75;
  var line = document.querySelector(".alert, .reminder");
  if (!line) return;
  var text = line.querySelector(".alert-text");
  if (!text) return;
  var chunk = text.querySelector(".alert-chunk");
  if (!chunk) return;
  var incoming = chunk.textContent;
  var applying = false;

  function readState() {
    try {
      var raw = sessionStorage.getItem(KEY);
      if (!raw) return null;
      var state = JSON.parse(raw);
      if (!state || typeof state.text !== "string") return null;
      return state;
    } catch (err) {
      return null;
    }
  }
  function writeState(state) {
    try {
      if (!state) sessionStorage.removeItem(KEY);
      else sessionStorage.setItem(KEY, JSON.stringify(state));
    } catch (err) {}
  }
  function chunks() {
    return text.querySelectorAll(".alert-chunk");
  }
  function setMessage(message) {
    var nodes = chunks();
    var index;
    for (index = 0; index < nodes.length; index += 1) {
      nodes[index].textContent = message;
    }
  }
  function oneCopy() {
    var nodes = chunks();
    var index;
    for (index = nodes.length - 1; index >= 1; index -= 1) {
      nodes[index].parentNode.removeChild(nodes[index]);
    }
  }
  function play(started, duration) {
    var elapsed = Date.now() - started;
    if (elapsed < 0) elapsed = 0;
    if (chunks().length < 2) text.appendChild(chunks()[0].cloneNode(true));
    text.style.animationDuration = (duration / 1000) + "s";
    text.style.animationDelay = (-elapsed / 1000) + "s";
    text.style.animationIterationCount = "infinite";
    if (text.className.indexOf("alert-marquee") === -1) {
      text.className += " alert-marquee";
    }
  }
  function begin(message) {
    applying = true;
    setMessage(message);
    text.style.animationName = "none";
    text.className = text.className.replace(" alert-marquee", "").replace("alert-marquee", "");
    oneCopy();
    if (!(text.scrollWidth > line.clientWidth)) {
      text.style.animationName = "";
      writeState(null);
      applying = false;
      return;
    }
    text.className += " alert-marquee";
    text.appendChild(chunks()[0].cloneNode(true));
    var distance = chunks()[0].offsetWidth;
    var duration = (distance / pixelsPerSecond) * 1000;
    var started = Date.now();
    text.style.animationDuration = (duration / 1000) + "s";
    text.style.animationDelay = "0s";
    text.style.animationIterationCount = "infinite";
    text.style.animationName = "";
    writeState({text: message, started: started, duration: duration, queued: null});
    applying = false;
  }

  var state = readState();
  var now = Date.now();
  var active = !!(state && state.started && state.duration && now < state.started + state.duration);
  if (active && state.text !== incoming) {
    state.queued = incoming;
    setMessage(state.text);
    writeState(state);
    play(state.started, state.duration);
  } else if (active) {
    setMessage(state.text);
    play(state.started, state.duration);
  } else {
    begin(incoming);
  }

  text.addEventListener("animationiteration", function () {
    if (applying) return;
    var current = readState();
    if (!current) return;
    if (current.queued && current.queued !== current.text) {
      begin(current.queued);
      return;
    }
    current.started = Date.now();
    current.queued = null;
    writeState(current);
  });
})();
</script>
"""
_POWER_SCRIPT = """<script>
(function () {
  var DIM_MS = 5 * 60 * 1000;
  var OFF_MS = 6 * 60 * 1000;
  var DOUBLE_MS = 400;
  var TAP_PX = 48;
  var level = "on";
  var idleAt = Date.now();
  var lastTap = 0;
  var consumed = false;
  var held = false;

  function apply() {
    document.documentElement.classList.remove("power-dim", "power-off");
    if (level === "dim") document.documentElement.classList.add("power-dim");
    if (level === "off") document.documentElement.classList.add("power-off");
  }
  function tick(now) {
    if (level === "off") return level;
    var idle = now - idleAt;
    if (idle >= OFF_MS) {
      level = "off";
      held = false;
    } else if (held || idle >= DIM_MS) level = "dim";
    else level = "on";
    apply();
    return level;
  }
  function centreQuarter(clientX, width) {
    var span = width || window.innerWidth || document.documentElement.clientWidth || 800;
    if (!span || typeof clientX !== "number" || !isFinite(clientX)) return false;
    var across = clientX / span;
    return across >= 0.375 && across <= 0.625;
  }
  function noteTap(now, clientX, width, moved) {
    var travel = (typeof moved === "number" && isFinite(moved)) ? moved : 0;
    if (travel >= TAP_PX) {
      if (level === "on") {
        lastTap = 0;
        idleAt = now;
      }
      return "swipe";
    }
    var centre = centreQuarter(clientX, width);
    if (level === "dim" && centre && lastTap && now - lastTap <= DOUBLE_MS) {
      level = "off";
      held = false;
      idleAt = now;
      lastTap = 0;
      consumed = true;
      apply();
      return "off";
    }
    if (level !== "on") {
      level = "on";
      held = false;
      idleAt = now;
      lastTap = 0;
      consumed = true;
      apply();
      return "wake";
    }
    if (centre) {
      level = "dim";
      held = true;
      idleAt = now;
      lastTap = now;
      consumed = true;
      apply();
      return "dim";
    }
    lastTap = 0;
    idleAt = now;
    apply();
    return "activity";
  }
  var arm = null;
  document.addEventListener("pointerdown", function (event) {
    arm = {id: event.pointerId, x: event.clientX, y: event.clientY};
  }, true);
  document.addEventListener("pointerup", function (event) {
    if (!arm || arm.id !== event.pointerId) return;
    var dx = event.clientX - arm.x;
    var dy = event.clientY - arm.y;
    var moved = Math.sqrt(dx * dx + dy * dy);
    var x = arm.x;
    arm = null;
    noteTap(Date.now(), x, window.innerWidth || document.documentElement.clientWidth || 800, moved);
  }, true);
  document.addEventListener("pointercancel", function (event) {
    if (arm && arm.id === event.pointerId) arm = null;
  }, true);
  setInterval(function () { tick(Date.now()); }, 1000);
  window.kioskDisplay = {
    dimMs: DIM_MS,
    offMs: OFF_MS,
    doubleMs: DOUBLE_MS,
    tapPx: TAP_PX,
    level: function () { return level; },
    idleAt: function () { return idleAt; },
    tick: tick,
    noteTap: noteTap,
    consume: function () {
      var flag = consumed;
      consumed = false;
      return flag;
    }
  };
  apply();
})();
</script>
"""

_SWIPE_SCRIPT = """<script>
(function () {
  var view = document.querySelector(".viewport");
  if (!view) return;
  var chart = view.querySelector(".chart");
  var count = Number(view.getAttribute("data-count"));
  var visible = Number(view.getAttribute("data-visible"));
  var index = 0;
  var origin = null;
  var dates = [];
  try {
    dates = JSON.parse(view.getAttribute("data-dates") || "[]");
  } catch (err) {}
  var dayLine = document.querySelector(".day");
  try {
    var saved = Number(sessionStorage.getItem("weather-kiosk-index"));
    if (!isNaN(saved)) index = saved;
  } catch (err) {}
  function place() {
    var width = view.clientWidth || 768;
    var column = width / visible;
    var maxIndex = Math.max(0, count - visible);
    if (index > maxIndex) index = maxIndex;
    if (index < 0) index = 0;
    chart.style.transform = "translateX(" + (-index * column) + "px)";
    if (dayLine && dates.length) {
      var shown = dates[index] || dates[0];
      if (shown) dayLine.textContent = shown;
    }
    try {
      sessionStorage.setItem("weather-kiosk-index", String(index));
    } catch (err) {}
  }
  view.addEventListener("selectstart", function (event) {
    event.preventDefault();
  });
  view.addEventListener("dragstart", function (event) {
    event.preventDefault();
  });
  view.addEventListener("pointerdown", function (event) {
    if (window.kioskDisplay && window.kioskDisplay.consume()) {
      origin = null;
      return;
    }
    if (event.cancelable) event.preventDefault();
    origin = event.clientX;
    if (view.setPointerCapture) {
      try { view.setPointerCapture(event.pointerId); } catch (err) {}
    }
  });
  view.addEventListener("pointerup", function (event) {
    if (window.kioskDisplay && window.kioskDisplay.consume()) {
      origin = null;
      return;
    }
    if (origin === null) return;
    var delta = event.clientX - origin;
    origin = null;
    var bounds = view.getBoundingClientRect();
    var across = (event.clientX - bounds.left) / (bounds.width || 768);
    var maxIndex = Math.max(0, count - visible);
    if (delta <= -48) {
      index = maxIndex;
    } else if (delta >= 48) {
      index = 0;
    } else if (across >= 0.75) {
      index = index + 2;
    } else if (across <= 0.25) {
      index = index - 2;
    }
    place();
  });
  place();
})();
</script>
"""

_PLOT_WIDTH = 768.0
_PLOT_HEIGHT = 206.0
_LINE_TOP = 68.0
_LINE_BOTTOM = 194.0


def _plot_points(slots: Sequence, width: float) -> List[tuple]:
    """Return (x, y) in plot pixels for each slot. y leaves room for the icon."""
    count = len(slots)
    temps = [float(slot.temperature_c) for slot in slots]
    low = min(temps)
    high = max(temps)
    span = high - low
    if span < 1.0:
        low -= 0.5
        high += 0.5
        span = high - low
    else:
        pad = span * 0.18
        low -= pad
        high += pad
        span = high - low
    points = []
    step = width / float(count)
    for index, temp in enumerate(temps):
        x = (index + 0.5) * step
        y = _LINE_BOTTOM - ((temp - low) / span) * (_LINE_BOTTOM - _LINE_TOP)
        points.append((x, y))
    return points


def _plot_svg(slots: Sequence, points: Sequence, width: float) -> str:
    dots = []
    for x, y in points:
        dots.append('<circle cx="%.1f" cy="%.1f" r="5" fill="#e7eef6"/>' % (x, y))
    lines = []
    for run in _line_runs(slots, points):
        if len(run) < 2:
            continue
        lines.append(
            '<polyline fill="none" stroke="#7eb6ff" stroke-width="3" '
            'stroke-linejoin="round" stroke-linecap="round" points="%s"/>'
            % " ".join("%.1f,%.1f" % (x, y) for x, y in run)
        )
    return (
        '<svg class="plot" viewBox="0 0 %.1f %.0f" aria-hidden="true">'
        "%s%s</svg>"
    ) % (width, _PLOT_HEIGHT, "".join(lines), "".join(dots))


def _line_runs(slots: Sequence, points: Sequence) -> List[list]:
    """Split the temperature line where the clock jumps across the night."""
    if not points:
        return []
    runs = [[points[0]]]
    for index in range(1, len(points)):
        if _crosses_overnight(slots[index - 1].local_time, slots[index].local_time):
            runs.append([points[index]])
        else:
            runs[-1].append(points[index])
    return runs


def _crosses_overnight(start: datetime, end: datetime) -> bool:
    """True when the open interval from start to end includes 23:00-05:00."""
    cursor = start + timedelta(hours=1)
    while cursor < end:
        if cursor.hour < 6 or cursor.hour > 22:
            return True
        cursor += timedelta(hours=1)
        if cursor - start > timedelta(days=2):
            return True
    return False
