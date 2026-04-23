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
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from bless import (
    BlessServer,
    BlessGATTCharacteristic,
    GATTCharacteristicProperties,
    GATTAttributePermissions,
)

from shared import (
    SERVICE_UUID,
    RECORD_UUID,
    STATUS_UUID,
    CLIPS_UUID,
    RECORDINGS_DIR,
    STATE_FILE,
    CONFIG_DIR,
)

# ── Mutable state ─────────────────────────────────────────────────────────────

_proc: Optional[subprocess.Popen] = None
_current_clip: Optional[str] = None
_server: Optional[BlessServer] = None
_beacon_manager = None   # injected by main.py

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


def _send_status_notification(msg: str) -> None:
    """Low-level: push a single status packet to the connected client."""
    if _server is None:
        return
    try:
        char = _server.get_characteristic(STATUS_UUID)
        if char:
            char.value = bytearray(msg.encode())
            _server.update_value(SERVICE_UUID, STATUS_UUID)
            print(f"[BLE] Status → {msg}", flush=True)
    except Exception as e:
        print(f"[BLE] Failed to notify status: {e}", flush=True)


def notify_status(status: str, payload: Optional[str] = None) -> None:
    """Push status to connected iOS client.

    If *payload* is provided and exceeds 512 bytes (BLE MTU limit) it is
    delivered in chunks: DIAG_START … DIAG_<chunk> … DIAG_END.
    """
    _send_status_notification(status)

    if payload is None:
        return

    encoded = payload.encode()
    if len(encoded) <= 512:
        _send_status_notification(f"DIAG_{payload}")
        return

    # Chunked delivery for large payloads (e.g. diagnostic JSON)
    _send_status_notification("DIAG_START")
    chunk_size = 490   # "DIAG_" prefix (5 bytes) + chunk ≤ 495 < 512
    for i in range(0, len(encoded), chunk_size):
        chunk = encoded[i:i + chunk_size].decode(errors="replace")
        _send_status_notification(f"DIAG_{chunk}")
        time.sleep(0.05)   # brief pause to avoid flooding
    _send_status_notification("DIAG_END")


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

    notify_status("IDLE")
    notify_clips()


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
    elif cmd == "PAIR":
        _handle_pair()
    elif cmd == "DIAGNOSTIC":
        _handle_diagnostic()
    else:
        notify_status(f"ERROR:unknown command {cmd}")

# ── PAIR / DIAGNOSTIC handlers ────────────────────────────────────────────────

def _handle_pair() -> None:
    """Initiate BLE beacon pairing in a background thread."""
    notify_status("PAIRING")

    def _pair_worker():
        if _beacon_manager is None:
            notify_status("PAIR_FAILED")
            return
        try:
            address = _beacon_manager.pair(timeout=30)
            if address:
                notify_status(f"PAIRED:{address}")
            else:
                notify_status("PAIR_FAILED")
        except Exception as e:
            print(f"[BLE] Pair error: {e}", flush=True)
            notify_status("PAIR_FAILED")

    threading.Thread(target=_pair_worker, daemon=True).start()


def _handle_diagnostic() -> None:
    """Read last_diagnostic.json and send it as chunked Status notifications."""
    diag_file = CONFIG_DIR / "last_diagnostic.json"
    try:
        if not diag_file.exists():
            notify_status("DIAGNOSTIC", payload='{"error":"no diagnostic run yet"}')
            return
        content = diag_file.read_text()
        notify_status("DIAGNOSTIC", payload=content)
    except Exception as e:
        print(f"[BLE] Diagnostic read error: {e}", flush=True)
        notify_status(f"ERROR:diagnostic read failed: {e}")


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
