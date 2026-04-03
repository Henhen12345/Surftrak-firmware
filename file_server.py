#!/usr/bin/env python3
"""
file_server.py — HTTP file server for SurfTrak clip downloads.

The iOS app hits this server over WiFi to list and download recordings.
Uses only Python stdlib — no Flask or FastAPI.

Endpoints:
  GET    /clips              JSON list of clips (name, size, created)
  GET    /clips/<filename>   Stream the H.264 file (64KB chunks)
  DELETE /clips/<filename>   Delete a clip
  GET    /status             Recording state + disk info
"""

import json
import os
import shutil
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote

# ── Config ────────────────────────────────────────────────────────────────────

HOST           = "0.0.0.0"
PORT           = 8080
RECORDINGS_DIR = Path.home() / "recordings"
STATE_FILE     = Path("/tmp/surftrak_state.json")
CHUNK_SIZE     = 65536   # 64 KB chunks for large file streaming

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_clips() -> list:
    """Return clip metadata sorted newest first. MP4 preferred, h264 fallback."""
    if not RECORDINGS_DIR.exists():
        return []
    all_clips = list(RECORDINGS_DIR.glob("*.mp4")) + list(RECORDINGS_DIR.glob("*.h264"))
    clips = []
    for p in sorted(all_clips, key=lambda f: f.stat().st_mtime, reverse=True):
        stat = p.stat()
        clips.append({
            "name": p.name,
            "size": stat.st_size,
            "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return clips


def read_state() -> dict:
    """Read shared state written by ble_server.py."""
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"recording": False, "current_clip": None}


def safe_filename(raw: str) -> str:
    """Strip any path components so filenames can't escape RECORDINGS_DIR."""
    return Path(unquote(raw)).name

# ── Request handler ───────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Print concise access log to stdout for journalctl
        print(f"[HTTP] {self.command} {self.path} → {args[1]}", flush=True)

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_GET(self):
        if self.path == "/clips":
            self._get_clips()
        elif self.path.startswith("/clips/"):
            self._download_clip(safe_filename(self.path[len("/clips/"):]))
        elif self.path == "/status":
            self._get_status()
        else:
            self._json(404, {"error": "not found"})

    def do_DELETE(self):
        if self.path.startswith("/clips/"):
            self._delete_clip(safe_filename(self.path[len("/clips/"):]))
        else:
            self._json(404, {"error": "not found"})

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _get_clips(self):
        self._json(200, {"clips": get_clips()})

    def _download_clip(self, filename: str):
        if not filename or "/" in filename:
            self._json(400, {"error": "invalid filename"})
            return

        filepath = RECORDINGS_DIR / filename
        if not filepath.exists():
            self._json(404, {"error": "not found"})
            return

        size = filepath.stat().st_size
        self.send_response(200)
        content_type = "video/mp4" if filename.endswith(".mp4") else "application/octet-stream"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        # Disable caching — clips may be re-recorded with the same name
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        # Stream in 64 KB chunks to handle large files without loading into RAM
        try:
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected mid-download — not an error
            pass
        except Exception as e:
            print(f"[HTTP] Error streaming {filename}: {e}", flush=True)

    def _delete_clip(self, filename: str):
        if not filename or "/" in filename:
            self._json(400, {"error": "invalid filename"})
            return

        filepath = RECORDINGS_DIR / filename
        if not filepath.exists():
            self._json(404, {"error": "not found"})
            return

        try:
            filepath.unlink()
            print(f"[HTTP] Deleted: {filename}", flush=True)
            self._json(200, {"deleted": True})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _get_status(self):
        state = read_state()
        try:
            usage = shutil.disk_usage(RECORDINGS_DIR if RECORDINGS_DIR.exists() else Path.home())
            disk_free_gb = round(usage.free / 1_073_741_824, 1)
        except Exception:
            disk_free_gb = -1

        self._json(200, {
            "recording":  state.get("recording", False),
            "current_clip": state.get("current_clip"),
            "clip_count": len(get_clips()),
            "disk_free_gb": disk_free_gb,
        })

    # ── Utility ───────────────────────────────────────────────────────────────

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    server = HTTPServer((HOST, PORT), Handler)
    pi_ip = os.popen("hostname -I | awk '{print $1}'").read().strip()

    print(f"[HTTP] SurfTrak file server running", flush=True)
    print(f"[HTTP] Clips list : http://{pi_ip}:{PORT}/clips", flush=True)
    print(f"[HTTP] Status     : http://{pi_ip}:{PORT}/status", flush=True)
    print(f"[HTTP] Recordings : {RECORDINGS_DIR}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[HTTP] Shutting down.", flush=True)
        server.server_close()
