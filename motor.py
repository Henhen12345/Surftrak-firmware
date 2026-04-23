#!/usr/bin/env python3
"""motor.py — TMC2209 stepper pan motor control."""

import threading
import time
from typing import Optional

import shared


def _tmc_crc(data: list) -> int:
    crc = 0
    for byte in data:
        for _ in range(8):
            if (crc >> 7) ^ (byte & 0x01):
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
            byte >>= 1
    return crc


def _tmc_read_datagram(reg: int) -> bytes:
    data = [0x05, 0x00, reg & 0x7F]
    return bytes(data + [_tmc_crc(data)])


class PanMotor:

    def __init__(self):
        self._lock = threading.Lock()
        self._running    = False
        self._thread:    Optional[threading.Thread] = None

        self._current_pos = 0.0   # degrees
        self._target_pos  = 0.0   # smoothed target (degrees)
        self._raw_target  = 0.0   # unsmoothed target set by pan_to()

        self._step_rate   = 500   # steps per second in tracking mode

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Set up GPIO, enable the driver, start step thread."""
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        GPIO.setup(shared.GPIO_STEP, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(shared.GPIO_DIR,  GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(shared.GPIO_ENN,  GPIO.OUT, initial=GPIO.HIGH)  # start disabled

        GPIO.output(shared.GPIO_ENN, GPIO.LOW)   # enable motor (active low)

        self._running = True
        self._thread  = threading.Thread(target=self._step_loop, daemon=True,
                                         name="motor-step")
        self._thread.start()
        print("[MOTOR] Started", flush=True)

    def stop(self) -> None:
        """Disable motor driver and stop step thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        try:
            import RPi.GPIO as GPIO
            GPIO.output(shared.GPIO_ENN, GPIO.HIGH)   # disable
        except Exception:
            pass
        print("[MOTOR] Stopped", flush=True)

    def home(self) -> None:
        """Drive to mechanical stop via StallGuard; fall back to fixed-step homing."""
        print("[MOTOR] Homing...", flush=True)
        if not self._try_stallguard_home():
            self._fixed_step_home()
        with self._lock:
            self._current_pos = 0.0
            self._target_pos  = 0.0
            self._raw_target  = 0.0
        print("[MOTOR] Homed — position zeroed", flush=True)

    def pan_to(self, azimuth_deg: float) -> None:
        """Set target pan angle. Clamped to ±PAN_LIMIT_DEG."""
        clamped = max(-shared.PAN_LIMIT_DEG, min(shared.PAN_LIMIT_DEG, azimuth_deg))
        with self._lock:
            self._raw_target = clamped

    def get_position(self) -> float:
        """Return current position in degrees."""
        with self._lock:
            return self._current_pos

    # ── Homing routines ───────────────────────────────────────────────────────

    def _try_stallguard_home(self) -> bool:
        """Use TMC2209 StallGuard (SG_RESULT 0x41) to detect mechanical stop."""
        try:
            import serial
            import RPi.GPIO as GPIO
            uart = serial.Serial("/dev/ttyAMA0", 115200, timeout=0.1)

            GPIO.output(shared.GPIO_DIR, GPIO.LOW)   # toward home

            step_interval = 1.0 / 100   # 100 steps/sec — slow for reliable stall
            max_steps     = shared.PAN_LIMIT_DEG * shared.STEPS_PER_DEGREE * 2

            for _ in range(max_steps):
                if not self._running:
                    uart.close()
                    return False

                sg = self._read_tmc_reg(uart, 0x41)
                if sg is not None and sg < 50:
                    print("[MOTOR] StallGuard stall detected — at home", flush=True)
                    uart.close()
                    return True

                self._pulse_step()
                time.sleep(step_interval)

            uart.close()
            return True
        except Exception as e:
            print(f"[MOTOR] StallGuard unavailable: {e}", flush=True)
            return False

    def _fixed_step_home(self) -> None:
        """Drive a fixed number of steps toward the mechanical stop."""
        import RPi.GPIO as GPIO
        GPIO.output(shared.GPIO_DIR, GPIO.LOW)
        steps         = shared.PAN_LIMIT_DEG * shared.STEPS_PER_DEGREE
        step_interval = 1.0 / 200
        for _ in range(steps):
            if not self._running:
                break
            self._pulse_step()
            time.sleep(step_interval)

    # ── Step thread ───────────────────────────────────────────────────────────

    def _step_loop(self) -> None:
        """Daemon thread: exponentially smooth toward raw target, step toward smoothed."""
        import RPi.GPIO as GPIO

        step_deg = 1.0 / shared.STEPS_PER_DEGREE

        while self._running:
            with self._lock:
                raw      = self._raw_target
                smoothed = self._target_pos
                current  = self._current_pos

                # Exponential smoothing: smoothed tracks raw at rate MOTOR_ALPHA
                new_smoothed = smoothed + shared.MOTOR_ALPHA * (raw - smoothed)
                self._target_pos = new_smoothed

            delta = new_smoothed - current

            if abs(delta) < step_deg * 0.5:
                time.sleep(0.005)
                continue

            if delta > 0:
                GPIO.output(shared.GPIO_DIR, GPIO.HIGH)
            else:
                GPIO.output(shared.GPIO_DIR, GPIO.LOW)

            self._pulse_step()

            with self._lock:
                if delta > 0:
                    self._current_pos += step_deg
                else:
                    self._current_pos -= step_deg

            time.sleep(1.0 / self._step_rate)

    # ── Low-level helpers ─────────────────────────────────────────────────────

    def _pulse_step(self) -> None:
        import RPi.GPIO as GPIO
        GPIO.output(shared.GPIO_STEP, GPIO.HIGH)
        time.sleep(0.000002)   # 2 µs minimum pulse width for TMC2209
        GPIO.output(shared.GPIO_STEP, GPIO.LOW)

    def _read_tmc_reg(self, uart, reg: int) -> Optional[int]:
        """Read a 32-bit TMC2209 register. Returns value or None on error."""
        try:
            uart.write(_tmc_read_datagram(reg))
            time.sleep(0.005)
            resp = uart.read(8)
            if len(resp) >= 7:
                return (resp[3] << 24) | (resp[4] << 16) | (resp[5] << 8) | resp[6]
            return None
        except Exception:
            return None
