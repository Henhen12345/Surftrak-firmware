#!/usr/bin/env python3
"""
ble_server.py — BLE peripheral for SurfTrak.

Advertises as "SurfTrak". The iOS app connects, sends START/STOP on the
Record characteristic, and receives status notifications and clip lists.

BLE Profile:
  Service  12345678-1234-1234-1234-123456789012
  Record   12345678-1234-1234-1234-123456789013  Write-no-response
  Status   12345678-1234-1234-1234-123456789014  Notify
  Clips    12345678-1234-1234-1234-123456789015  Read + Notify
"""

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from bless import (
    BlessServer,
    BlessGATTCharacteristic,
    GATTCharacteristicProperties,
    GATTAttributePermissions,
)

# ── Constants ─────────────────────────────────────────────────────────────────

SERVICE_UUID = "12345678-1234-1234-1234-123456789012"
RECORD_UUID  = "12345678-1234-1234-1234-123456789013"
STATUS_UUID  = "12345678-1234-1234-1234-123456789014"
CLIPS_UUID   = "12345678-1234-1234-1234-123456789015"

RECORDINGS_DIR = Path.home() / "recordings"
STATE_FILE     = Path("/tmp/surftrak_state.json")

# ── Mutable state ─────────────────────────────────────────────────────────────

_proc: Optional[subprocess.Popen] = None
_current_clip: Optional[str] = None
_server: Optional[BlessServer] = None

# ── Helpers ───────────────────────────────────────────────────────────────────

def write_state(recording: bool, clip: Optional[str]) -> None:
    """Write shared state for file_server.py to read."""
    try:
        STATE_FILE.write_text(json.dumps({"recording": recording, "current_clip": clip}))
    except Exception as e:
        print(f"[BLE] Failed to write state file: {e}", flush=True)


def get_clips() -> list:
    """Return list of clip filenames sorted newest first."""
    if not RECORDINGS_DIR.exists():
        return []
    clips = sorted(
        RECORDINGS_DIR.glob("*.h264"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [p.name for p in clips]


def notify_status(status: str) -> None:
    """Push a status string to connected iOS client via notification."""
    if _server is None:
        return
    try:
        char = _server.get_characteristic(STATUS_UUID)
        if char:
            char.value = bytearray(status.encode())
            _server.update_value(SERVICE_UUID, STATUS_UUID)
            print(f"[BLE] Status → {status}", flush=True)
    except Exception as e:
        print(f"[BLE] Failed to notify status: {e}", flush=True)


def notify_clips() -> None:
    """Push updated clips JSON to connected iOS client via notification."""
    if _server is None:
        return
    try:
        char = _server.get_characteristic(CLIPS_UUID)
        if char:
            char.value = bytearray(json.dumps(get_clips()).encode())
            _server.update_value(SERVICE_UUID, CLIPS_UUID)
    except Exception as e:
        print(f"[BLE] Failed to notify clips: {e}", flush=True)

# ── Recording control ─────────────────────────────────────────────────────────

def start_recording() -> None:
    global _proc, _current_clip

    if _proc is not None:
        notify_status("ERROR:already recording")
        return

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"surf_{ts}.h264"
    filepath = RECORDINGS_DIR / filename

    cmd = [
        "rpicam-vid",
        "-t", "0",
        "--width", "1920",
        "--height", "1080",
        "--framerate", "30",
        "--codec", "h264",
        "-o", str(filepath),
    ]

    try:
        _proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _current_clip = filename
        write_state(True, filename)
        notify_status("RECORDING")
        print(f"[BLE] Recording started: {filename}", flush=True)
    except Exception as e:
        _proc = None
        _current_clip = None
        write_state(False, None)
        notify_status(f"ERROR:{e}")
        print(f"[BLE] Failed to start recording: {e}", flush=True)


def stop_recording() -> None:
    global _proc, _current_clip

    if _proc is None:
        notify_status("IDLE")
        return

    clip = _current_clip
    try:
        _proc.terminate()
        try:
            _proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _proc.kill()
            _proc.wait()
    except Exception as e:
        print(f"[BLE] Error stopping process: {e}", flush=True)
    finally:
        _proc = None
        _current_clip = None

    write_state(False, None)
    notify_status("IDLE")
    notify_clips()
    print(f"[BLE] Recording stopped: {clip}", flush=True)

# ── GATT callbacks ────────────────────────────────────────────────────────────

def read_request(characteristic: BlessGATTCharacteristic, **kwargs) -> bytearray:
    """Handle read requests — only Clips char is readable."""
    if str(characteristic.uuid).lower() == CLIPS_UUID.lower():
        return bytearray(json.dumps(get_clips()).encode())
    return characteristic.value or bytearray()


def write_request(characteristic: BlessGATTCharacteristic, value: Any, **kwargs) -> None:
    """Handle write requests — only Record char is writable."""
    if str(characteristic.uuid).lower() != RECORD_UUID.lower():
        return

    try:
        cmd = bytes(value).decode("utf-8", errors="ignore").strip().upper()
    except Exception:
        return

    print(f"[BLE] Received: {cmd}", flush=True)

    if cmd == "START":
        start_recording()
    elif cmd == "STOP":
        stop_recording()
    else:
        notify_status(f"ERROR:unknown command {cmd}")

# ── Main loop ─────────────────────────────────────────────────────────────────

async def run() -> None:
    global _server

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    write_state(False, None)

    loop = asyncio.get_event_loop()
    trigger = asyncio.Event()

    _server = BlessServer(name="SurfTrak", loop=loop)
    _server.read_request_func = read_request
    _server.write_request_func = write_request

    # Build GATT service and characteristics
    await _server.add_new_service(SERVICE_UUID)

    # Record char — write without response only
    await _server.add_new_characteristic(
        SERVICE_UUID,
        RECORD_UUID,
        GATTCharacteristicProperties.write_without_response,
        None,
        GATTAttributePermissions.writeable,
    )

    # Status char — notify (read also set so value can be initialised)
    await _server.add_new_characteristic(
        SERVICE_UUID,
        STATUS_UUID,
        GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
        bytearray(b"IDLE"),
        GATTAttributePermissions.readable,
    )

    # Clips char — read + notify
    await _server.add_new_characteristic(
        SERVICE_UUID,
        CLIPS_UUID,
        GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
        bytearray(b"[]"),
        GATTAttributePermissions.readable,
    )

    await _server.start()
    print("[BLE] Advertising as 'SurfTrak'", flush=True)
    print(f"[BLE] Service UUID : {SERVICE_UUID}", flush=True)
    print(f"[BLE] Recordings   : {RECORDINGS_DIR}", flush=True)

    # Run until cancelled (systemd Restart=always handles crashes)
    await trigger.wait()


if __name__ == "__main__":
    while True:
        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            print("[BLE] Interrupted — stopping.", flush=True)
            stop_recording()
            sys.exit(0)
        except Exception as e:
            print(f"[BLE] Fatal error: {e} — restarting in 3s", flush=True)
            stop_recording()
            import time
            time.sleep(3)
