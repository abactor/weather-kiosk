"""Small inline weather icons. No external image files or URLs."""

from __future__ import annotations

import html


def icon_kind(condition: str) -> str:
    """Map an Environment Canada condition phrase to an icon name."""
    text = condition.lower()
    if "thunder" in text:
        return "storm"
    if "freezing" in text:
        return "freezing"
    has_rain = any(word in text for word in ("rain", "shower", "drizzle"))
    has_snow = any(word in text for word in ("snow", "flurr", "blizzard", "ice pellet"))
    if has_rain and has_snow:
        return "mix"
    if "flurr" in text:
        return "flurries"
    if has_snow:
        return "snow"
    if has_rain:
        return "rain"
    if "fog" in text or "mist" in text or "haze" in text:
        return "fog"
    if "mainly sunny" in text or "mostly sunny" in text or "a few clouds" in text:
        return "mainly_sunny"
    if "partly" in text or "partial" in text or "mix of sun" in text:
        return "partly_cloudy"
    if "cloudy" in text or "overcast" in text:
        return "cloudy"
    if "clear" in text or "sunny" in text:
        return "clear"
    return "cloudy"


def icon_svg(condition: str) -> str:
    """Return a 32x32 inline SVG for this condition."""
    if not condition or not str(condition).strip():
        return ""
    kind = icon_kind(condition)
    return (
        '<svg class="icon icon-%s" viewBox="0 0 32 32" width="40" height="40" '
        'role="img" aria-label="%s">%s</svg>'
        % (kind, html.escape(condition, quote=True), _BODIES[kind])
    )


_SUN = (
    '<circle cx="16" cy="16" r="6" fill="#f5c542"/>'
    '<g stroke="#f5c542" stroke-width="2" stroke-linecap="round">'
    '<path d="M16 3v4M16 25v4M3 16h4M25 16h4M6.8 6.8l2.8 2.8M22.4 22.4l2.8 2.8M25.2 6.8l-2.8 2.8M9.6 22.4l-2.8 2.8"/>'
    "</g>"
)
_CLOUD = '<path fill="#d5dee8" d="M10 22h12.2a5 5 0 0 0 .4-10 6.2 6.2 0 0 0-11.8-1.2A4.4 4.4 0 0 0 10 22z"/>'
_SUN_SMALL = (
    '<circle cx="11" cy="11" r="4" fill="#f5c542"/>'
    '<g stroke="#f5c542" stroke-width="1.6" stroke-linecap="round">'
    '<path d="M11 3.5v2.2M11 16.2v2.2M4.2 11h2.2M15.6 11h2.2M6.2 6.2l1.6 1.6M14.2 14.2l1.6 1.6M15.8 6.2l-1.6 1.6M7.8 14.2l-1.6 1.6"/>'
    "</g>"
)

_BODIES = {
    "clear": _SUN,
    "mainly_sunny": _SUN_SMALL + '<path fill="#d5dee8" d="M12 24h11.2a4.2 4.2 0 0 0 .3-8.4 5.2 5.2 0 0 0-10-1A3.8 3.8 0 0 0 12 24z"/>',
    "partly_cloudy": (
        '<circle cx="20" cy="10" r="4.2" fill="#f5c542"/>'
        '<g stroke="#f5c542" stroke-width="1.6" stroke-linecap="round">'
        '<path d="M20 3v2M26.2 6.2l-1.4 1.4M27.5 12h-2"/>'
        "</g>"
        + '<path fill="#d5dee8" d="M8 23h13a5 5 0 0 0 .3-10 6 6 0 0 0-11.5-1A4.3 4.3 0 0 0 8 23z"/>'
    ),
    "cloudy": _CLOUD,
    "fog": (
        '<g stroke="#d5dee8" stroke-width="2.4" stroke-linecap="round">'
        '<path d="M6 12h20M8 17h16M6 22h20"/>'
        "</g>"
    ),
    "rain": _CLOUD + '<g stroke="#7eb6ff" stroke-width="2" stroke-linecap="round"><path d="M12 24l-1 4M17 24l-1 4M22 24l-1 4"/></g>',
    "snow": _CLOUD + '<g fill="#f4f8fc"><circle cx="12" cy="26" r="1.3"/><circle cx="17" cy="28" r="1.3"/><circle cx="22" cy="26" r="1.3"/></g>',
    "flurries": _CLOUD + '<g fill="#f4f8fc"><circle cx="13" cy="26" r="1.2"/><circle cx="19" cy="28" r="1.2"/></g>',
    "mix": _CLOUD + '<g stroke="#7eb6ff" stroke-width="2" stroke-linecap="round"><path d="M12 24l-1 4"/></g><g fill="#f4f8fc"><circle cx="20" cy="27" r="1.3"/></g>',
    "storm": _CLOUD + '<path fill="#f0d060" d="M18 16l-4 7h3l-1 5 5-8h-3l0-4z"/>',
    "freezing": _CLOUD + '<g stroke="#b9dcff" stroke-width="2" stroke-linecap="round"><path d="M12 24l2 4M17 24l2 4M22 24l2 4"/></g>',
}
