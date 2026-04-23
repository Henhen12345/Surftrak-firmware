#!/usr/bin/env python3
"""uwb.py — UWB ranging via DWM3000 × 2 over SPI. Produces filtered azimuth."""

import math
import threading
import time
from typing import Optional

import shared


class UWBRanger:

    def __init__(self):
        self._lock    = threading.Lock()
        self._latest: Optional[dict] = None
        self._running = False
        self._thread:  Optional[threading.Thread] = None
        self._spi:     Optional[object] = None        # spidev.SpiDev

        # 1D Kalman filter state
        self._kf_x = 0.0
        self._kf_p = 1.0

        # Reset watchdog
        self._fail_count = 0

        # IRQ wake-up event (set by GPIO callbacks, cleared by ranging loop)
        self._irq_event = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Initialize both DWM3000 modules and start 10 Hz ranging loop."""
        import RPi.GPIO as GPIO
        import spidev

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # CS and RESET as outputs (idle high)
        for pin in [shared.GPIO_CS1, shared.GPIO_CS2,
                    shared.GPIO_RESET1, shared.GPIO_RESET2]:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)

        # IRQ as inputs with rising-edge interrupt
        for pin in [shared.GPIO_IRQ1, shared.GPIO_IRQ2]:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(pin, GPIO.RISING, callback=self._on_irq)

        # Single SPI bus; CS managed manually so GPIO25 can serve as CS2
        self._spi = spidev.SpiDev()
        self._spi.open(0, 0)
        self._spi.max_speed_hz = 8_000_000
        self._spi.mode = 0

        self._init_module(shared.GPIO_CS1, "left")
        self._init_module(shared.GPIO_CS2, "right")

        self._running = True
        self._thread  = threading.Thread(target=self._ranging_loop, daemon=True,
                                         name="uwb-ranger")
        self._thread.start()
        print("[UWB] Started — 10 Hz DS-TWR ranging", flush=True)

    def stop(self) -> None:
        """Stop ranging loop cleanly."""
        self._running = False
        self._irq_event.set()          # unblock the waiting thread
        if self._thread:
            self._thread.join(timeout=2.0)
        try:
            if self._spi:
                self._spi.close()
        except Exception:
            pass
        self._spi = None
        print("[UWB] Stopped", flush=True)

    def get_latest(self) -> Optional[dict]:
        """Return latest reading or None. Thread-safe."""
        with self._lock:
            return self._latest.copy() if self._latest else None

    # ── GPIO callback ─────────────────────────────────────────────────────────

    def _on_irq(self, channel: int) -> None:
        self._irq_event.set()

    # ── DWM3000 init ─────────────────────────────────────────────────────────

    def _init_module(self, cs_pin: int, name: str) -> None:
        """Configure DWM3000 for max range: 110 kbps data rate, 1024 preamble symbols."""
        try:
            # Soft-reset via PMSC_CTRL0 (0x36:00), then wait for PLL
            self._reg_write(cs_pin, 0x36, 0x00, [0x01, 0x01])
            time.sleep(0.002)

            # SYS_CFG (0x04:00): enable 1024 preamble and long-range mode
            self._reg_write(cs_pin, 0x04, 0x00, [0x00, 0x00, 0x04, 0x00])

            # CHAN_CTRL (0x1F:00): channel 5, PRF 64 MHz, 110 kbps
            self._reg_write(cs_pin, 0x1F, 0x00, [0x05, 0x05, 0x00, 0x00])

            # TX_FCTRL (0x08:00): 1024 preamble symbols, 110 kbps
            self._reg_write(cs_pin, 0x08, 0x00, [0x00, 0x00, 0x60, 0x09])

            print(f"[UWB] Module {name} (CS GPIO{cs_pin}) initialized", flush=True)
        except Exception as e:
            print(f"[UWB] Failed to init module {name}: {e}", flush=True)

    # ── SPI helpers ───────────────────────────────────────────────────────────

    def _reg_write(self, cs_pin: int, file_id: int,
                   sub_addr: int, data: list) -> None:
        import RPi.GPIO as GPIO
        header = [0x80 | (file_id & 0x3F), sub_addr]
        GPIO.output(cs_pin, GPIO.LOW)
        self._spi.xfer2(header + data)
        GPIO.output(cs_pin, GPIO.HIGH)

    def _reg_read(self, cs_pin: int, file_id: int,
                  sub_addr: int, length: int) -> list:
        import RPi.GPIO as GPIO
        header = [file_id & 0x3F, sub_addr]
        GPIO.output(cs_pin, GPIO.LOW)
        resp = self._spi.xfer2(header + [0x00] * length)
        GPIO.output(cs_pin, GPIO.HIGH)
        return resp[len(header):]

    # ── Ranging loop ──────────────────────────────────────────────────────────

    def _ranging_loop(self) -> None:
        interval = 0.1  # 10 Hz

        while self._running:
            t0 = time.monotonic()

            # Wait for IRQ signal or timeout at the loop rate
            self._irq_event.wait(timeout=interval)
            self._irq_event.clear()

            if not self._running:
                break

            d1 = self._ds_twr(shared.GPIO_CS1, "left")
            d2 = self._ds_twr(shared.GPIO_CS2, "right")

            if d1 is not None and d2 is not None:
                confidence      = "high"
                self._fail_count = 0
            elif d1 is not None or d2 is not None:
                # Only one module responded — hold previous azimuth
                self._fail_count = 0
                elapsed = time.monotonic() - t0
                if elapsed < interval:
                    time.sleep(interval - elapsed)
                continue
            else:
                self._fail_count += 1
                if self._fail_count >= 5:
                    self._reset_modules()
                elapsed = time.monotonic() - t0
                if elapsed < interval:
                    time.sleep(interval - elapsed)
                continue

            # Law-of-cosines azimuth
            baseline = shared.BASELINE_M
            try:
                cos_a = (d1 ** 2 + baseline ** 2 - d2 ** 2) / (2 * d1 * baseline)
                cos_a = max(-1.0, min(1.0, cos_a))
                raw_az = math.degrees(math.acos(cos_a)) - 90.0
            except (ValueError, ZeroDivisionError):
                elapsed = time.monotonic() - t0
                if elapsed < interval:
                    time.sleep(interval - elapsed)
                continue

            # 1D Kalman filter
            self._kf_p += shared.UWB_KALMAN_Q
            K           = self._kf_p / (self._kf_p + shared.UWB_KALMAN_R)
            self._kf_x += K * (raw_az - self._kf_x)
            self._kf_p  = (1 - K) * self._kf_p

            with self._lock:
                self._latest = {
                    "azimuth":    self._kf_x,
                    "d1":         d1,
                    "d2":         d2,
                    "confidence": confidence,
                }

            elapsed = time.monotonic() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)

    # ── DS-TWR ────────────────────────────────────────────────────────────────

    def _ds_twr(self, cs_pin: int, name: str) -> Optional[float]:
        """Double-Sided Two-Way Ranging. Returns distance in metres or None."""
        try:
            if self._spi is None:
                return None

            # Trigger transmission: write TX_CTRL (0x0D) start bit
            self._reg_write(cs_pin, 0x0D, 0x00, [0x02])

            # Allow time for round-trip exchange (~4 ms at 110 kbps, 1024 preamble)
            time.sleep(0.006)

            # Read RX timestamp register (0x15, 5 bytes)
            raw = self._reg_read(cs_pin, 0x15, 0x00, 5)
            if not raw or len(raw) < 4:
                return None

            tof_raw = (raw[0] | (raw[1] << 8) | (raw[2] << 16) | (raw[3] << 24))
            if tof_raw == 0 or tof_raw == 0xFFFFFFFF:
                return None

            # DWM3000 timestamp resolution: 1 / (499.2 MHz × 128) ≈ 15.65 ps
            TICK_S  = 1.0 / (499.2e6 * 128)
            C       = 299_792_458.0  # m/s
            dist_m  = (tof_raw * TICK_S * C) / 2.0

            if not (0.1 <= dist_m <= 100.0):
                return None

            return dist_m
        except Exception as e:
            print(f"[UWB] DS-TWR error ({name}): {e}", flush=True)
            return None

    # ── Module reset ──────────────────────────────────────────────────────────

    def _reset_modules(self) -> None:
        import RPi.GPIO as GPIO
        print("[UWB] Module reset triggered", flush=True)
        for pin in [shared.GPIO_RESET1, shared.GPIO_RESET2]:
            GPIO.output(pin, GPIO.LOW)
        time.sleep(0.010)
        for pin in [shared.GPIO_RESET1, shared.GPIO_RESET2]:
            GPIO.output(pin, GPIO.HIGH)
        time.sleep(0.100)
        self._fail_count = 0
