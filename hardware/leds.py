"""WS2812B frame over SPI (GPIO10 / MOSI, physical pin 19) through the
SN74AHCT125N level shifter (docs/wiring.md). Pi5Neo; rpi_ws281x does NOT work
on Pi 5. The strip's 5V comes ONLY from the ALITOVE supply (invariant 4).

States (hal.LED_STATES):
  off              everything dark (night)
  ambient          steady warm white, low brightness
  warning          AMBER, ramps up over RAMP_S then holds (predicted-low warning)
  full             RED, pulsing at 1 Hz (actual low)
  strobe           red / white alternating at 3 Hz (escalation)
  message_waiting  soft teal, slow breathe (optional; the backend keeps it out
                   of the night and out of every alarm)
  buddy_alert      violet band chasing round the frame (optional; "not your
                   low, your buddy's")
Warning and full differ in colour AND motion; the optional states use colours
and patterns neither alarm tier uses. LedFrame refuses a colour config that
would make any of these four look alike.

Current cap: strip power crosses the breadboard (docs/wiring.md "As built",
~1 A), so every frame is scaled down until its estimated draw fits
max_current_a. Low brightness states are untouched; full-white strobe is the
one that gets trimmed.

render_frame() is pure (state + seconds since the state began -> pixels) and
is what the tests check; LedFrame runs it on a background thread so a
set_state() from the backend's event loop never blocks."""

from __future__ import annotations

import inspect
import logging
import math
import threading
import time

from .hal import Color, check_color, check_led_state, clamp01

log = logging.getLogger("irin.hal.leds")

try:
    from pi5neo import Pi5Neo  # type: ignore
except ImportError:  # laptops
    Pi5Neo = None

LED_COUNT = 29  # the as-built frame (docs/wiring.md)
SPI_DEVICE = "/dev/spidev0.0"
MAX_CURRENT_A = 1.0  # breadboard power path; raise only once strip power moves onto Wagos
MA_PER_CHANNEL = 20.0  # WS2812B: ~20 mA per colour channel at 255
FPS = 30
RAMP_S = 20.0  # warning ramp from 15 % to full over this many seconds

DEFAULT_COLORS: dict[str, Color] = {
    "ambient": (255, 140, 60),  # warm white
    "warning": (255, 100, 0),  # amber
    "full": (255, 0, 0),  # red
    "strobe": (255, 0, 0),  # red, alternating with white
    "message_waiting": (0, 150, 140),  # teal
    "buddy_alert": (150, 0, 255),  # violet
}
DEFAULT_BRIGHTNESS: dict[str, float] = {
    "ambient": 0.08,
    "warning": 0.6,
    "full": 1.0,
    "strobe": 1.0,
    "message_waiting": 0.1,
    "buddy_alert": 0.6,
    "off": 0.0,
}
_MUST_DIFFER = ("warning", "full", "message_waiting", "buddy_alert")

Pixels = list[Color]


def frame_current_a(pixels: Pixels) -> float:
    return sum(r + g + b for r, g, b in pixels) / 255.0 * MA_PER_CHANNEL / 1000.0


def _fill(count: int, color: Color, level: float) -> Pixels:
    px = tuple(int(round(c * level)) for c in color)
    return [px] * count  # type: ignore[list-item]


def render_frame(
    state: str,
    t: float,
    count: int = LED_COUNT,
    color: Color | None = None,
    brightness: float | None = None,
    max_current_a: float = MAX_CURRENT_A,
) -> Pixels:
    """Pixels for `state`, `t` seconds after it began. Pure."""
    check_led_state(state)
    if state == "off":
        return [(0, 0, 0)] * count
    c = color if color is not None else DEFAULT_COLORS[state]
    b = clamp01(brightness if brightness is not None else DEFAULT_BRIGHTNESS[state])
    t = max(0.0, t)

    if state == "ambient":
        px = _fill(count, c, b)
    elif state == "warning":
        px = _fill(count, c, b * min(1.0, 0.15 + 0.85 * t / RAMP_S))
    elif state == "full":
        px = _fill(count, c, b * (0.35 + 0.65 * (0.5 + 0.5 * math.sin(2 * math.pi * t))))
    elif state == "strobe":
        px = _fill(count, c if int(t * 6) % 2 == 0 else (255, 255, 255), b)
    elif state == "message_waiting":
        px = _fill(count, c, b * (0.3 + 0.7 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 10.0))))
    else:  # buddy_alert: a 5-LED band travelling at 12 LEDs/s over a dim frame
        head = int(t * 12) % count
        band = {(head - k) % count for k in range(5)}
        px = [tuple(int(round(ch * (b if i in band else b * 0.1))) for ch in c) for i in range(count)]  # type: ignore[misc]

    amps = frame_current_a(px)
    if amps > max_current_a:
        k = max_current_a / amps
        px = [tuple(int(ch * k) for ch in p) for p in px]  # type: ignore[misc]
    return px


def check_distinct(colors: dict[str, Color]) -> None:
    seen: dict[Color, str] = {}
    for name in _MUST_DIFFER:
        c = tuple(colors[name])
        if c in seen:
            raise ValueError(f"LED colours for {seen[c]!r} and {name!r} are identical; they must stay visually distinct")
        seen[c] = name


class LedFrame:
    """The real frame. `strip` is injectable (tests pass a fake); `start=False`
    skips the render thread so tests drive tick() by hand."""

    def __init__(
        self,
        count: int = LED_COUNT,
        strip=None,
        colors: dict[str, Color] | None = None,
        max_current_a: float = MAX_CURRENT_A,
        start: bool = True,
    ) -> None:
        self.colors = {**DEFAULT_COLORS, **(colors or {})}
        for c in self.colors.values():
            check_color(c)
        check_distinct(self.colors)
        if strip is None:
            if Pi5Neo is None:
                raise RuntimeError("pi5neo not installed: real LEDs are Pi-only (IRIN_HW=real)")
            strip = Pi5Neo(SPI_DEVICE, count, 800)
        self.strip = strip
        self.count = count
        self.max_current_a = max_current_a
        self._lock = threading.Lock()
        self._state = "off"
        self._color: Color | None = None
        self._brightness: float | None = None
        self._since = time.monotonic()
        self._last: Pixels | None = None
        self._last_write = 0.0
        self._stop = threading.Event()
        params = inspect.signature(strip.update_strip).parameters
        self._update_kwargs = {"sleep_duration": 0.001} if "sleep_duration" in params else {}
        self._thread: threading.Thread | None = None
        if start:
            self._thread = threading.Thread(target=self._run, name="irin-leds", daemon=True)
            self._thread.start()

    def set_state(self, state: str, color: Color | None = None, brightness: float | None = None) -> None:
        check_led_state(state)
        check_color(color)
        if color is not None and state in _MUST_DIFFER:
            check_distinct({**self.colors, state: tuple(color)})
        with self._lock:
            self._state, self._since = state, time.monotonic()
            self._color = tuple(color) if color is not None else self.colors.get(state)
            self._brightness = brightness
        log.info("LEDs -> %s", state)

    def tick(self, now: float | None = None) -> Pixels:
        """Render and (if changed, or once a second) write one frame."""
        now = time.monotonic() if now is None else now
        with self._lock:
            state, color, brightness, since = self._state, self._color, self._brightness, self._since
        px = render_frame(state, now - since, self.count, color, brightness, self.max_current_a)
        if px != self._last or now - self._last_write >= 1.0:
            for i, (r, g, b) in enumerate(px):
                self.strip.set_led_color(i, r, g, b)
            self.strip.update_strip(**self._update_kwargs)
            self._last, self._last_write = px, now
        return px

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # never let a glitch kill the frame thread
                log.exception("LED frame write failed")
            self._stop.wait(1.0 / FPS)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        with self._lock:
            self._state = "off"
        self.tick()
