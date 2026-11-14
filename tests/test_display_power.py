"""Idle dim, shutoff, tap to wake, a centre tap that dims, and a swipe that does not."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

from weather_kiosk.render import render_page

_PROBE = """<script>
(function () {
  var display = window.kioskDisplay;
  var start = display.idleAt();
  var width = 800;
  var centre = 400;
  function doubleTap(t, x) {
    if (display.level() !== "on") {
      display.noteTap(t - 20, x, width);
      display.consume();
    }
    var first = display.noteTap(t, x, width);
    var second = display.noteTap(t + display.doubleMs, x, width);
    return {
      first: first,
      second: second,
      level: display.level(),
      consumed: display.consume()
    };
  }
  var base = start + display.offMs;
  var report = {
    before: display.level(),
    almost: display.tick(start + display.dimMs - 1),
    dim: display.tick(start + display.dimMs),
    off: display.tick(start + display.offMs),
    wake: display.noteTap(base + 50),
    afterWake: display.level(),
    single: display.noteTap(base + 1000, centre, width),
    afterSingle: display.level(),
    singleDimClass: document.documentElement.classList.contains("power-dim"),
    singleOffClass: document.documentElement.classList.contains("power-off"),
    held: display.tick(base + 1000 + 5000),
    sideFromDim: display.noteTap(base + 8000, 100, width),
    afterSideFromDim: display.level(),
    first: display.noteTap(base + 9000, centre, width),
    second: display.noteTap(base + 9000 + display.doubleMs, centre, width),
    afterDouble: display.level(),
    centreConsumed: display.consume(),
    sideWake: display.noteTap(base + 2000, 100, width),
    afterSideWake: display.level(),
    sideWakeConsumed: display.consume(),
    leftQuarter: doubleTap(base + 4000, 100),
    rightQuarter: doubleTap(base + 6000, 700),
    midHalf: doubleTap(base + 8000, 240),
    edgeLow: doubleTap(base + 10000, 300),
    edgeHigh: doubleTap(base + 12000, 500),
    sideThenCentre: (function () {
      var t = base + 14000;
      if (display.level() !== "on") {
        display.noteTap(t - 20, 100, width);
        display.consume();
      }
      var first = display.noteTap(t, 100, width);
      var second = display.noteTap(t + display.doubleMs, centre, width);
      return {first: first, second: second, level: display.level(), consumed: display.consume()};
    })(),
    centreThenSide: (function () {
      var t = base + 16000;
      if (display.level() !== "on") {
        display.noteTap(t - 20, 100, width);
        display.consume();
      }
      var first = display.noteTap(t, centre, width);
      var second = display.noteTap(t + display.doubleMs, 700, width);
      return {first: first, second: second, level: display.level(), consumed: display.consume()};
    })(),
    centreSwipe: (function () {
      if (display.level() !== "on") {
        display.noteTap(base + 18000, 100, width);
        display.consume();
      }
      var moved = display.noteTap(base + 18100, centre, width, display.tapPx);
      return {result: moved, level: display.level(), consumed: display.consume()};
    })(),
    centreNudge: (function () {
      if (display.level() !== "on") {
        display.noteTap(base + 19000, 100, width);
        display.consume();
      }
      var moved = display.noteTap(base + 19100, centre, width, display.tapPx - 1);
      return {result: moved, level: display.level()};
    })(),
    gesture: (function () {
      if (display.level() !== "on") {
        display.noteTap(base + 20000, 100, width);
        display.consume();
      }
      var span = window.innerWidth || width;
      var mid = span * 0.5;
      function fire(type, x, y, id) {
        document.body.dispatchEvent(new PointerEvent(type, {
          bubbles: true,
          cancelable: true,
          clientX: x,
          clientY: y,
          pointerId: id
        }));
      }
      fire("pointerdown", mid, 200, 7);
      fire("pointerup", mid + display.tapPx, 200, 7);
      var afterSwipe = display.level();
      if (display.level() !== "on") {
        display.noteTap(base + 20100, 100, width);
        display.consume();
      }
      fire("pointerdown", mid, 200, 8);
      fire("pointerup", mid + 1, 200, 8);
      return {afterSwipe: afterSwipe, afterTap: display.level()};
    })()
  };
  document.documentElement.setAttribute("data-power-result", JSON.stringify(report));
})();
</script>
"""

_STAYS_ON = {"first": "activity", "second": "activity", "level": "on", "consumed": False}
_TURNS_OFF = {"first": "dim", "second": "off", "level": "off", "consumed": True}


def test_page_dims_after_five_minutes_then_turns_off():
    """Idle dim and shutoff, plus a centre tap. A move past the threshold is a swipe and does not dim."""
    page = render_page(date(2026, 11, 14), [])
    assert "html.power-dim::before" in page
    assert "html.power-off::before" in page
    assert "background: #000000" in page
    assert "var DIM_MS = 5 * 60 * 1000;" in page
    assert "var OFF_MS = 6 * 60 * 1000;" in page
    assert "var DOUBLE_MS = 400;" in page
    assert "var TAP_PX = 48;" in page
    root = Path("/tmp/kiosk-power")
    root.mkdir(exist_ok=True)
    target = root / "power.html"
    target.write_text(page.replace("</body>", _PROBE + "</body>"), encoding="utf-8")
    subprocess.check_call(
        [
            "google-chrome",
            "--headless",
            "--disable-gpu",
            "--user-data-dir=/tmp/kiosk-power/chrome",
            "--window-size=800,480",
            "--virtual-time-budget=2000",
            "--dump-dom",
            target.as_uri(),
        ],
        stdout=open(root / "dom.html", "w", encoding="utf-8"),
        stderr=subprocess.DEVNULL,
    )
    dom = (root / "dom.html").read_text(encoding="utf-8")
    marker = 'data-power-result="'
    assert marker in dom
    raw = dom.split(marker, 1)[1].split('"', 1)[0]
    raw = raw.replace("&quot;", '"')
    report = json.loads(raw)
    assert report == {
        "before": "on",
        "almost": "on",
        "dim": "dim",
        "off": "off",
        "wake": "wake",
        "afterWake": "on",
        "single": "dim",
        "afterSingle": "dim",
        "singleDimClass": True,
        "singleOffClass": False,
        "held": "dim",
        "sideFromDim": "wake",
        "afterSideFromDim": "on",
        "first": "dim",
        "second": "off",
        "afterDouble": "off",
        "centreConsumed": True,
        "sideWake": "wake",
        "afterSideWake": "on",
        "sideWakeConsumed": True,
        "leftQuarter": _STAYS_ON,
        "rightQuarter": _STAYS_ON,
        "midHalf": _STAYS_ON,
        "edgeLow": _TURNS_OFF,
        "edgeHigh": _TURNS_OFF,
        "sideThenCentre": {"first": "activity", "second": "dim", "level": "dim", "consumed": True},
        "centreThenSide": {"first": "dim", "second": "wake", "level": "on", "consumed": True},
        "centreSwipe": {"result": "swipe", "level": "on", "consumed": False},
        "centreNudge": {"result": "dim", "level": "dim"},
        "gesture": {"afterSwipe": "on", "afterTap": "dim"},
    }
