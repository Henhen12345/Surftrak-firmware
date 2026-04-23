#!/usr/bin/env python3
"""led.py — WS2812B status LED on GPIO21. Thread-safe animated state machine."""

import math
import threading
import time
from typing import Optional

import shared

# Render loop tick — 50 ms gives smooth animations at 20 fps
_TICK = 0.05


class StatusLED:

    def __init__(self):
        self._state   = "off"
        self._lock    = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._strip   = None       # rpi_ws281x.PixelStrip

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Initialize hardware and start render thread."""
        try:
            from rpi_ws281x import PixelStrip
            self._strip = PixelStrip(1, shared.GPIO_LED, 800000, 5, False, 255, 0)
            self._strip.begin()
        except Exception as e:
            print(f"[LED] Hardware error during init: {e}", flush=True)

        self._running = True
        self._thread  = threading.Thread(target=self._render_loop, daemon=True,
                                         name="led-render")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._set_pixel(0, 0, 0)

    def set_state(self, state: str) -> None:
        """Thread-safe state update."""
        with self._lock:
            self._state = state

    # ── Render loop ───────────────────────────────────────────────────────────

    def _render_loop(self) -> None:
        t = 0.0   # animation phase accumulator (seconds)

        while self._running:
            with self._lock:
                state = self._state

            try:
                self._render(state, t)
            except Exception as e:
                print(f"[LED] Hardware error: {e}", flush=True)

            t += _TICK
            time.sleep(_TICK)

        self._set_pixel(0, 0, 0)

    def _render(self, state: str, t: float) -> None:
        if state == "booting":
            # Slow white pulse, 1 s period
            f = 0.5 + 0.5 * math.sin(2 * math.pi * t)
            v = int(200 * f)
            self._set_pixel(v, v, v)

        elif state == "ready":
            # Solid green at 30 % brightness  (#00FF00 × 0.30 → 76)
            self._set_pixel(0, 76, 0)

        elif state == "recording":
            # Solid red at 60 % brightness  (#FF0000 × 0.60 → 153)
            self._set_pixel(153, 0, 0)

        elif state == "beacon_lost":
            # Fast orange blink — 200 ms on / 200 ms off (#FF6600 = 255,102,0)
            on = int(t / 0.2) % 2 == 0
            self._set_pixel(255, 102, 0) if on else self._set_pixel(0, 0, 0)

        elif state == "error":
            # Fast red blink — 100 ms on / 100 ms off
            on = int(t / 0.1) % 2 == 0
            self._set_pixel(255, 0, 0) if on else self._set_pixel(0, 0, 0)

        elif state == "pairing":
            # Slow blue pulse, 1 s period (#0044FF = 0,68,255)
            f = 0.5 + 0.5 * math.sin(2 * math.pi * t)
            self._set_pixel(0, int(68 * f), int(255 * f))

        elif state == "diagnostic":
            # Faster cycling white (2 s period)
            f = 0.5 + 0.5 * math.sin(2 * math.pi * t / 2.0)
            v = int(200 * f)
            self._set_pixel(v, v, v)

        else:  # "off" or unknown
            self._set_pixel(0, 0, 0)

    # ── Hardware helper ───────────────────────────────────────────────────────

    def _set_pixel(self, r: int, g: int, b: int) -> None:
        try:
            if self._strip:
                from rpi_ws281x import Color
                self._strip.setPixelColor(0, Color(r, g, b))
                self._strip.show()
        except Exception as e:
            print(f"[LED] Hardware error: {e}", flush=True)
