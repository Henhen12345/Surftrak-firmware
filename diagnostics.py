#!/usr/bin/env python3
"""diagnostics.py — boot hardware self-test. Never raises; logs and continues."""

import json
import subprocess
import time
from typing import Dict, Tuple

import shared


def run() -> Dict[str, str]:
    """Run all hardware diagnostics. Returns result dict. Never raises."""
    print("[DIAG] Starting boot diagnostics...", flush=True)
    results: Dict[str, str] = {}

    results["gpio"]      = _test_gpio()
    left, right          = _test_uwb()
    results["uwb_left"]  = left
    results["uwb_right"] = right
    results["tmc2209"]   = _test_tmc2209()
    results["led"]       = _test_led()
    results["camera"]    = _test_camera()

    _save_report(results)
    _show_led_feedback(results)

    passed = sum(1 for v in results.values() if v == "pass")
    total  = len(results)
    print(f"[DIAG] Complete — {passed}/{total} passed: {results}", flush=True)
    return results


# ── Individual tests ──────────────────────────────────────────────────────────

def _test_gpio() -> str:
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        pins = [
            shared.GPIO_STEP, shared.GPIO_DIR, shared.GPIO_ENN,
            shared.GPIO_CS1, shared.GPIO_CS2,
            shared.GPIO_IRQ1, shared.GPIO_IRQ2,
            shared.GPIO_RESET1, shared.GPIO_RESET2,
            shared.GPIO_LED, shared.GPIO_BTN,
        ]
        failed = []
        for pin in pins:
            try:
                GPIO.setup(pin, GPIO.OUT)
            except Exception:
                failed.append(str(pin))
        return "pass" if not failed else f"fail:{','.join(failed)}"
    except Exception as e:
        print(f"[DIAG] GPIO test error: {e}", flush=True)
        return "fail:all"


def _test_uwb() -> Tuple[str, str]:
    try:
        import spidev
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        spi = spidev.SpiDev()
        spi.open(0, 0)
        spi.max_speed_hz = 1_000_000
        spi.mode = 0

        left  = "pass" if _read_dwm3000_id(spi, shared.GPIO_CS1) else "fail"
        right = "pass" if _read_dwm3000_id(spi, shared.GPIO_CS2) else "fail"
        spi.close()
        return left, right
    except Exception as e:
        print(f"[DIAG] UWB SPI test error: {e}", flush=True)
        return "fail", "fail"


def _read_dwm3000_id(spi, cs_pin: int) -> bool:
    """Read DWM3000 device ID register 0x00. Expects 0xDECA prefix."""
    import RPi.GPIO as GPIO
    try:
        GPIO.setup(cs_pin, GPIO.OUT)
        GPIO.output(cs_pin, GPIO.LOW)
        time.sleep(0.001)
        resp = spi.xfer2([0x00, 0x00, 0x00, 0x00, 0x00])
        GPIO.output(cs_pin, GPIO.HIGH)
        dev_id = (resp[4] << 8) | resp[3]
        return dev_id == 0xDECA
    except Exception as e:
        print(f"[DIAG] DWM3000 CS{cs_pin} read error: {e}", flush=True)
        return False


def _test_tmc2209() -> str:
    """Send UART read request for IOIN register (0x04). Check sync byte in response."""
    try:
        import serial
        uart = serial.Serial("/dev/ttyAMA0", 115200, timeout=0.1)
        msg = _tmc_read_request(0x04)
        uart.write(msg)
        time.sleep(0.01)
        response = uart.read(8)
        uart.close()
        if len(response) >= 1 and response[0] == 0x05:
            return "pass"
        return "fail"
    except Exception as e:
        print(f"[DIAG] TMC2209 test error: {e}", flush=True)
        return "fail"


def _test_led() -> str:
    try:
        from rpi_ws281x import PixelStrip, Color
        strip = PixelStrip(1, shared.GPIO_LED, 800000, 5, False, 255, 0)
        strip.begin()
        strip.setPixelColor(0, Color(50, 50, 50))
        strip.show()
        time.sleep(0.5)
        strip.setPixelColor(0, Color(0, 0, 0))
        strip.show()
        return "pass"
    except Exception as e:
        print(f"[DIAG] LED test error: {e}", flush=True)
        return "fail"


def _test_camera() -> str:
    try:
        result = subprocess.run(
            ["rpicam-still", "--immediate", "-o", "/dev/null", "--timeout", "1000"],
            capture_output=True,
            timeout=10,
        )
        return "pass" if result.returncode == 0 else "fail"
    except Exception as e:
        print(f"[DIAG] Camera test error: {e}", flush=True)
        return "fail"


# ── Reporting ─────────────────────────────────────────────────────────────────

def _save_report(results: Dict[str, str]) -> None:
    try:
        shared.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        report_path = shared.CONFIG_DIR / "last_diagnostic.json"
        report_path.write_text(json.dumps(results, indent=2))
    except Exception as e:
        print(f"[DIAG] Failed to save report: {e}", flush=True)


def _show_led_feedback(results: Dict[str, str]) -> None:
    """Flash green 3× if all pass; flash red N× where N = failed component count."""
    try:
        from rpi_ws281x import PixelStrip, Color
        strip = PixelStrip(1, shared.GPIO_LED, 800000, 5, False, 255, 0)
        strip.begin()

        failed_count = sum(1 for v in results.values() if v != "pass")

        if failed_count == 0:
            color = Color(0, 255, 0)
            flashes = 3
        else:
            color = Color(255, 0, 0)
            flashes = failed_count

        for _ in range(flashes):
            strip.setPixelColor(0, color)
            strip.show()
            time.sleep(0.3)
            strip.setPixelColor(0, Color(0, 0, 0))
            strip.show()
            time.sleep(0.3)

    except Exception as e:
        print(f"[DIAG] LED feedback error: {e}", flush=True)


# ── TMC2209 UART helpers ──────────────────────────────────────────────────────

def _tmc_read_request(reg: int) -> bytes:
    data = [0x05, 0x00, reg & 0x7F]
    return bytes(data + [_tmc_crc(data)])


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
