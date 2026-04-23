#!/usr/bin/env python3
"""power_button.py — GPIO3 power button with short/long press detection."""

import subprocess
import threading
import time
from typing import Callable, Optional

import shared

_DEBOUNCE_MS    = 50
_SHORT_MAX_S    = 1.5
_LONG_THRESH_S  = 3.0


class PowerButton:

    def __init__(self):
        self._press_start:    Optional[float]    = None
        self._on_short_press: Optional[Callable] = None
        self._on_long_press:  Optional[Callable] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self,
              on_short_press: Optional[Callable] = None,
              on_long_press:  Optional[Callable] = None) -> None:
        """Arm the button interrupt. Callbacks run in daemon threads."""
        import RPi.GPIO as GPIO

        self._on_short_press = on_short_press or self._default_short
        self._on_long_press  = on_long_press  or self._default_long

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(shared.GPIO_BTN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(
            shared.GPIO_BTN, GPIO.BOTH,
            callback=self._on_edge,
            bouncetime=_DEBOUNCE_MS,
        )
        print("[PWR] Power button armed on GPIO3", flush=True)

    def stop(self) -> None:
        try:
            import RPi.GPIO as GPIO
            GPIO.remove_event_detect(shared.GPIO_BTN)
        except Exception:
            pass

    # ── GPIO edge callback ────────────────────────────────────────────────────

    def _on_edge(self, channel: int) -> None:
        import RPi.GPIO as GPIO
        level = GPIO.input(shared.GPIO_BTN)

        if level == GPIO.LOW:
            # Button pressed (active low)
            self._press_start = time.monotonic()
        elif level == GPIO.HIGH and self._press_start is not None:
            # Button released — classify press duration
            duration          = time.monotonic() - self._press_start
            self._press_start = None

            if duration < _DEBOUNCE_MS / 1000.0:
                return   # spurious glitch

            print(f"[PWR] Button released after {duration:.2f}s", flush=True)

            if duration >= _LONG_THRESH_S:
                print("[PWR] Long press detected", flush=True)
                threading.Thread(target=self._on_long_press, daemon=True).start()
            else:
                print("[PWR] Short press detected", flush=True)
                threading.Thread(target=self._on_short_press, daemon=True).start()

    # ── Default callbacks ─────────────────────────────────────────────────────

    def _default_short(self) -> None:
        print("[PWR] Short press — no callback configured", flush=True)

    def _default_long(self) -> None:
        print("[PWR] Long press — initiating shutdown", flush=True)
        subprocess.run(["sudo", "shutdown", "-h", "now"])
