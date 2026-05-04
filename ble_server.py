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
import threading
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

RECORDINGS_DIR   = Path.home() / "recordings"
STATE_FILE       = Path("/tmp/surftrak_state.json")
HOTSPOT_SCRIPT   = Path.home() / "hotspot.sh"
TRANSFER_TIMEOUT = 180  # seconds before hotspot auto-tears-down after STOP

# ── Mutable state ─────────────────────────────────────────────────────────────

_proc: Optional[subprocess.Popen] = None
_current_clip: Optional[str] = None
_server: Optional[BlessServer] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_hotspot_active: bool = False
_transfer_timer: Optional[threading.Timer] = None

# ── Helpers ───────────────────────────────────────────────────────────────────

def write_state(recording: bool, clip: Optional[str]) -> None:
    """Write shared state for file_server.py to read."""
    try:
        STATE_FILE.write_text(json.dumps({"recording": recording, "current_clip": clip}))
    except Exception as e:
        print(f"[BLE] Failed to write state file: {e}", flush=True)


def get_clips() -> list:
    """Return list of clip filenames sorted newest first. MP4 preferred, h264 fallback."""
    if not RECORDINGS_DIR.exists():
        return []
    clips = sorted(
        list(RECORDINGS_DIR.glob("*.mp4")) + list(RECORDINGS_DIR.glob("*.h264")),
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

# ── Hotspot management ────────────────────────────────────────────────────────

def get_wifi_mac_suffix() -> str:
    """Return the last 4 hex characters of the wlan0 MAC address."""
    try:
        mac = Path("/sys/class/net/wlan0/address").read_text().strip()
        return mac.replace(":", "")[-4:].upper()
    except Exception:
        return "0000"


def enable_hotspot() -> bool:
    """Switch wlan0 to AP mode via hotspot.sh. Returns True on success. Blocking."""
    global _hotspot_active
    ssid = f"SurfTrak-{get_wifi_mac_suffix()}"
    print(f"[BLE] Bringing up hotspot: {ssid}", flush=True)
    try:
        result = subprocess.run(
            ["sudo", str(HOTSPOT_SCRIPT), "enable", ssid],
            capture_output=True,
            timeout=20,
        )
        if result.returncode == 0:
            _hotspot_active = True
            print(f"[BLE] Hotspot active: {ssid} @ 192.168.50.1:8080", flush=True)
            return True
        err = result.stderr.decode(errors="ignore").strip()
        print(f"[BLE] Hotspot enable failed (rc={result.returncode}): {err}", flush=True)
        return False
    except subprocess.TimeoutExpired:
        print("[BLE] Hotspot enable timed out", flush=True)
        return False
    except Exception as e:
        print(f"[BLE] Hotspot enable error: {e}", flush=True)
        return False


def disable_hotspot() -> None:
    """Tear down the AP and return to WiFi client mode via hotspot.sh. Blocking."""
    global _hotspot_active
    _hotspot_active = False  # mark inactive immediately to prevent double-teardown
    print("[BLE] Tearing down hotspot", flush=True)
    try:
        result = subprocess.run(
            ["sudo", str(HOTSPOT_SCRIPT), "disable"],
            capture_output=True,
            timeout=20,
        )
        if result.returncode == 0:
            print("[BLE] WiFi client mode restored", flush=True)
        else:
            err = result.stderr.decode(errors="ignore").strip()
            print(f"[BLE] Hotspot disable failed (rc={result.returncode}): {err}", flush=True)
    except subprocess.TimeoutExpired:
        print("[BLE] Hotspot disable timed out", flush=True)
    except Exception as e:
        print(f"[BLE] Hotspot disable error: {e}", flush=True)

# ── Transfer timer ────────────────────────────────────────────────────────────

def _start_transfer_timer() -> None:
    global _transfer_timer
    _transfer_timer = threading.Timer(TRANSFER_TIMEOUT, _on_transfer_timeout)
    _transfer_timer.daemon = True
    _transfer_timer.start()
    print(f"[BLE] Transfer timeout armed: {TRANSFER_TIMEOUT}s", flush=True)


def _cancel_transfer_timer() -> None:
    global _transfer_timer
    if _transfer_timer is not None:
        _transfer_timer.cancel()
        _transfer_timer = None


def _on_transfer_timeout() -> None:
    """Called by threading.Timer after TRANSFER_TIMEOUT seconds. Runs in timer thread."""
    if not _hotspot_active:
        return
    print("[BLE] Transfer timeout — tearing down hotspot", flush=True)
    if _loop and _loop.is_running():
        # Schedule teardown back on the event loop thread so notify_status is safe
        asyncio.run_coroutine_threadsafe(_tear_down_hotspot(), _loop)

# ── Async hotspot task helpers ────────────────────────────────────────────────

async def _bring_up_hotspot() -> None:
    """Enable hotspot in a thread pool so the BLE event loop stays responsive."""
    loop = asyncio.get_running_loop()
    success = await loop.run_in_executor(None, enable_hotspot)
    if success:
        notify_status("HOTSPOT_READY")
        _start_transfer_timer()
    else:
        notify_status("IDLE")


async def _tear_down_hotspot() -> None:
    """Disable hotspot in a thread pool, then send IDLE. Idempotent."""
    if not _hotspot_active:
        return
    _cancel_transfer_timer()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, disable_hotspot)
    notify_status("IDLE")

# ── Recording control ─────────────────────────────────────────────────────────

def start_recording() -> None:
    global _proc, _current_clip

    if _hotspot_active:
        notify_status("ERROR:cannot record during transfer")
        return

    if _proc is not None:
        notify_status("ERROR:already recording")
        return

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    # Record to .h264 first; ffmpeg wraps it to .mp4 after STOP
    h264_filename = f"surf_{ts}.h264"
    mp4_filename  = f"surf_{ts}.mp4"
    filepath = RECORDINGS_DIR / h264_filename

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
        # Track both names — h264 is the live file, mp4 is the final deliverable
        _current_clip = (h264_filename, mp4_filename)
        write_state(True, mp4_filename)
        notify_status("RECORDING")
        print(f"[BLE] Recording started: {h264_filename}", flush=True)
    except Exception as e:
        _proc = None
        _current_clip = None
        write_state(False, None)
        notify_status(f"ERROR:{e}")
        print(f"[BLE] Failed to start recording: {e}", flush=True)


def stop_recording() -> None:
    global _proc, _current_clip

    if _proc is None:
        # Not recording — only send IDLE if we're not already in a transfer
        if not _hotspot_active:
            notify_status("IDLE")
        return

    clip_names = _current_clip  # (h264_filename, mp4_filename)
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

    # Wrap the raw H.264 in an MP4 container so iOS AVFoundation can read it
    if clip_names:
        h264_name, mp4_name = clip_names
        h264_path = RECORDINGS_DIR / h264_name
        mp4_path  = RECORDINGS_DIR / mp4_name
        _wrap_to_mp4(h264_path, mp4_path)
        print(f"[BLE] Recording stopped: {mp4_name}", flush=True)
    else:
        print("[BLE] Recording stopped.", flush=True)

    notify_clips()

    # Kick off hotspot bring-up asynchronously so we don't block the BLE event loop
    if _loop and _loop.is_running():
        _loop.create_task(_bring_up_hotspot())
    else:
        notify_status("IDLE")


def _wrap_to_mp4(h264_path: Path, mp4_path: Path) -> None:
    """Convert raw H.264 elementary stream to MP4 container via ffmpeg."""
    # Check ffmpeg is available before attempting conversion
    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
        print("[BLE] WARNING: ffmpeg not found — keeping .h264 file as-is.", flush=True)
        print("[BLE]   Install with: sudo apt-get install -y ffmpeg", flush=True)
        return

    if not h264_path.exists():
        print(f"[BLE] ERROR: source file missing: {h264_path}", flush=True)
        return

    print(f"[BLE] Wrapping {h264_path.name} → {mp4_path.name} ...", flush=True)
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-framerate", "30",
                "-i", str(h264_path),
                "-c:v", "copy",
                "-movflags", "+faststart",
                str(mp4_path),
            ],
            capture_output=True,
            timeout=300,  # 5-minute safety cap for very long recordings
        )
        if result.returncode == 0:
            h264_path.unlink()  # Delete the raw .h264 now that .mp4 exists
            print(f"[BLE] Conversion complete: {mp4_path.name}", flush=True)
        else:
            print(f"[BLE] ffmpeg failed (rc={result.returncode}) — keeping .h264", flush=True)
            print(result.stderr.decode(errors="ignore")[-500:], flush=True)
    except subprocess.TimeoutExpired:
        print("[BLE] ffmpeg timed out — keeping .h264 file.", flush=True)
    except Exception as e:
        print(f"[BLE] ffmpeg error: {e} — keeping .h264 file.", flush=True)

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
    elif cmd == "TRANSFER_COMPLETE":
        if _hotspot_active:
            if _loop and _loop.is_running():
                _loop.create_task(_tear_down_hotspot())
            else:
                _cancel_transfer_timer()
                disable_hotspot()
                notify_status("IDLE")
        else:
            notify_status("IDLE")
    else:
        notify_status(f"ERROR:unknown command {cmd}")

# ── Main loop ─────────────────────────────────────────────────────────────────

async def run() -> None:
    global _server, _loop

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    write_state(False, None)

    _loop = asyncio.get_running_loop()
    trigger = asyncio.Event()

    _server = BlessServer(name="SurfTrak", loop=_loop)
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


def _emergency_cleanup() -> None:
    """Best-effort cleanup on crash or interrupt."""
    _cancel_transfer_timer()
    if _hotspot_active:
        disable_hotspot()
    if _proc is not None:
        stop_recording()


if __name__ == "__main__":
    while True:
        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            print("[BLE] Interrupted — stopping.", flush=True)
            _emergency_cleanup()
            sys.exit(0)
        except Exception as e:
            print(f"[BLE] Fatal error: {e} — restarting in 3s", flush=True)
            _emergency_cleanup()
            import time
            time.sleep(3)
