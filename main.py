#!/usr/bin/env python3
"""main.py — SurfTrak system orchestrator."""

import asyncio
import json
import logging
import signal
import sys
import threading
import time
from http.server import HTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import shared

# ── Module singletons (populated in main()) ──────────────────────────────────

_led:    Optional[object] = None
_pwr:    Optional[object] = None
_motor:  Optional[object] = None
_uwb:    Optional[object] = None
_beacon: Optional[object] = None


# ── Logging setup ─────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    shared.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = shared.LOGS_DIR / "surftrak.log"

    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    handlers = [
        RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


# ── Shared state helpers ──────────────────────────────────────────────────────

def _read_state() -> dict:
    try:
        return json.loads(shared.STATE_FILE.read_text())
    except Exception:
        return {"recording": False, "current_clip": None}


def _led_set(state: str) -> None:
    if _led:
        try:
            _led.set_state(state)
        except Exception as e:
            logging.error(f"[MAIN] LED set_state error: {e}")


# ── Signal handling ───────────────────────────────────────────────────────────

def _shutdown(signum=None, frame=None) -> None:
    logging.info(f"[MAIN] Shutdown signal received — stopping all modules")
    try:
        import ble_server
        ble_server.stop_recording()
    except Exception:
        pass
    _stop_all()
    sys.exit(0)


def _stop_all() -> None:
    for name, obj, method in [
        ("UWB",         _uwb,    "stop"),
        ("Beacon",      _beacon, "stop"),
        ("Motor",       _motor,  "stop"),
        ("PowerButton", _pwr,    "stop"),
    ]:
        if obj:
            try:
                getattr(obj, method)()
            except Exception as e:
                logging.error(f"[MAIN] Error stopping {name}: {e}")

    _led_set("off")
    if _led:
        try:
            _led.stop()
        except Exception as e:
            logging.error(f"[MAIN] Error stopping LED: {e}")


# ── BLE button callbacks ──────────────────────────────────────────────────────

def _on_short_press() -> None:
    """Notify current recording status over BLE Status characteristic."""
    try:
        import ble_server
        state  = _read_state()
        status = "RECORDING" if state.get("recording") else "IDLE"
        ble_server.notify_status(status)
    except Exception as e:
        logging.error(f"[MAIN] Short press callback error: {e}")


def _on_long_press() -> None:
    """Stop recording, show white pulse, then power off."""
    import subprocess
    try:
        import ble_server
        ble_server.stop_recording()
    except Exception:
        pass
    _led_set("booting")   # slow white pulse while shutting down
    time.sleep(2.0)
    subprocess.run(["sudo", "shutdown", "-h", "now"])


# ── Service runners ───────────────────────────────────────────────────────────

def _run_ble_server() -> None:
    try:
        import ble_server
        ble_server._beacon_manager = _beacon
        asyncio.run(ble_server.run())
    except Exception as e:
        logging.error(f"[MAIN] BLE server error: {e}")


def _run_file_server() -> None:
    try:
        import file_server
        shared.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        server = HTTPServer(("0.0.0.0", 8080), file_server.Handler)
        logging.info("[MAIN] File server listening on :8080")
        server.serve_forever()
    except Exception as e:
        logging.error(f"[MAIN] File server error: {e}")


# ── Main tracking loop ────────────────────────────────────────────────────────

def _tracking_loop() -> None:
    last_reading_time:   Optional[float] = None
    uwb_stall_start:     Optional[float] = None

    while True:
        reading = None
        if _uwb:
            try:
                reading = _uwb.get_latest()
            except Exception as e:
                logging.error(f"[MAIN] UWB get_latest error: {e}")

        now = time.monotonic()

        if reading:
            last_reading_time = now
            uwb_stall_start   = None

            if reading.get("confidence") == "high" and _motor:
                try:
                    _motor.pan_to(reading["azimuth"])
                except Exception as e:
                    logging.error(f"[MAIN] Motor pan_to error: {e}")

        else:
            # Track how long we've had no UWB readings
            if last_reading_time is not None:
                if uwb_stall_start is None:
                    uwb_stall_start = now
                elif (now - uwb_stall_start) > 10.0:
                    logging.warning("[MAIN] UWB watchdog — no readings for 10s")
                    if _uwb:
                        try:
                            _uwb.stop()
                            time.sleep(1.0)
                            _uwb.start()
                        except Exception as e:
                            logging.error(f"[MAIN] UWB restart error: {e}")
                    uwb_stall_start = None

        # LED state follows recording / beacon / ready priority
        try:
            state = _read_state()
            if state.get("recording"):
                _led_set("recording")
            elif _beacon and not _beacon.is_connected():
                _led_set("beacon_lost")
            else:
                _led_set("ready")
        except Exception as e:
            logging.error(f"[MAIN] LED state error: {e}")

        time.sleep(0.1)


# ── Boot sequence ─────────────────────────────────────────────────────────────

def main() -> None:
    global _led, _pwr, _motor, _uwb, _beacon

    # 1. Logging
    _setup_logging()
    logging.info("[MAIN] SurfTrak starting")

    # Register signal handlers
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    # 2. LED — set booting state before thread starts so diagnostics runs clean
    try:
        import led as led_mod
        _led = led_mod.StatusLED()
        _led.set_state("booting")
    except Exception as e:
        logging.error(f"[MAIN] LED init failed: {e}")

    # 3. Diagnostics (uses its own PixelStrip — LED render thread not yet running)
    try:
        import diagnostics as diag_mod
        _led_set("diagnostic")
        diag_mod.run()
    except Exception as e:
        logging.error(f"[MAIN] Diagnostics error: {e}")

    # Start LED render thread after diagnostics so there's no PixelStrip conflict
    _led_set("booting")
    if _led:
        try:
            _led.start()
        except Exception as e:
            logging.error(f"[MAIN] LED start error: {e}")

    # 4. Power button
    try:
        import power_button as pwr_mod
        _pwr = pwr_mod.PowerButton()
        _pwr.start(on_short_press=_on_short_press, on_long_press=_on_long_press)
    except Exception as e:
        logging.error(f"[MAIN] Power button start error: {e}")

    # 5. Motor home
    try:
        import motor as motor_mod
        _motor = motor_mod.PanMotor()
        _motor.start()
        _motor.home()
    except Exception as e:
        logging.error(f"[MAIN] Motor start/home error: {e}")

    # 6. UWB
    try:
        import uwb as uwb_mod
        _uwb = uwb_mod.UWBRanger()
        _uwb.start()
    except Exception as e:
        logging.error(f"[MAIN] UWB start error: {e}")

    # 7. Beacon
    try:
        import beacon as beacon_mod
        _beacon = beacon_mod.BeaconManager()
        _beacon.start()
    except Exception as e:
        logging.error(f"[MAIN] Beacon start error: {e}")

    # 8. BLE server in daemon thread
    ble_thread = threading.Thread(target=_run_ble_server, daemon=True, name="ble-server")
    ble_thread.start()

    # 9. File server in daemon thread
    fs_thread = threading.Thread(target=_run_file_server, daemon=True, name="file-server")
    fs_thread.start()

    # 10. Ready
    _led_set("ready")
    logging.info("[MAIN] All modules started — entering tracking loop")

    # 11. Main tracking loop
    _tracking_loop()


if __name__ == "__main__":
    main()
