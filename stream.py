#!/usr/bin/env python3
"""
stream.py — MJPEG streaming server for Arducam IMX519 on Raspberry Pi.

Uses libcamera-vid to capture H.264-free MJPEG frames and serves them over
HTTP so any browser (including iPhone Safari) can display a live video feed.

Stream URL: http://192.168.4.1:8080/stream
"""

import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# ── Configuration ────────────────────────────────────────────────────────────

HOST = "0.0.0.0"
PORT = 8080
STREAM_PATH = "/stream"

# libcamera-vid command:
#   -t 0           run indefinitely
#   --width/height resolution
#   --framerate    target fps
#   --codec mjpeg  output raw MJPEG frames (not H.264) so we can boundary-split them
#   -o -           write to stdout
LIBCAMERA_CMD = [
    "libcamera-vid",
    "-t", "0",
    "--width", "1920",
    "--height", "1080",
    "--framerate", "30",
    "--codec", "mjpeg",
    "-o", "-",
]

BOUNDARY = b"frame"
CRLF = b"\r\n"

# ── Frame broadcaster ─────────────────────────────────────────────────────────

class FrameBroadcaster:
    """
    Runs libcamera-vid in a background thread, parses the raw MJPEG byte stream
    into individual JPEG frames, and distributes them to all connected clients.
    """

    def __init__(self):
        self._clients: list[threading.Event] = []
        self._frame: bytes = b""
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)

    def start(self):
        self._thread.start()

    def _capture_loop(self):
        """Read from libcamera-vid stdout, split on JPEG SOI/EOI markers."""
        while True:
            try:
                proc = subprocess.Popen(
                    LIBCAMERA_CMD,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,  # suppress camera debug output
                )
                buf = b""
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        # libcamera-vid exited — restart after a short delay
                        break
                    buf += chunk

                    # JPEG frames start with FF D8 and end with FF D9
                    while True:
                        start = buf.find(b"\xff\xd8")
                        if start == -1:
                            buf = b""
                            break
                        end = buf.find(b"\xff\xd9", start + 2)
                        if end == -1:
                            # Incomplete frame — keep buffering
                            buf = buf[start:]
                            break
                        frame = buf[start : end + 2]
                        buf = buf[end + 2 :]
                        self._publish(frame)

                proc.wait()
            except Exception as exc:
                print(f"[stream] Capture error: {exc}", flush=True)
            time.sleep(2)  # brief pause before restarting the camera process

    def _publish(self, frame: bytes):
        """Store the latest frame and wake all waiting client threads."""
        with self._lock:
            self._frame = frame
            for event in self._clients:
                event.set()

    def subscribe(self) -> threading.Event:
        """Register a client; returns an Event that fires on each new frame."""
        event = threading.Event()
        with self._lock:
            self._clients.append(event)
        return event

    def unsubscribe(self, event: threading.Event):
        with self._lock:
            self._clients.remove(event)

    def get_frame(self) -> bytes:
        with self._lock:
            return self._frame


broadcaster = FrameBroadcaster()


# ── HTTP request handler ──────────────────────────────────────────────────────

class StreamHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Suppress per-request access logs (they flood the journal)
        pass

    def do_GET(self):
        if self.path == STREAM_PATH:
            self._serve_stream()
        elif self.path in ("/", "/index.html"):
            self._serve_index()
        else:
            self.send_error(404)

    def _serve_index(self):
        """Simple HTML page that embeds the MJPEG stream."""
        html = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SurfTrak Live</title>
  <style>
    body {{ margin: 0; background: #000; display: flex;
            justify-content: center; align-items: center; height: 100vh; }}
    img  {{ max-width: 100%; max-height: 100vh; }}
  </style>
</head>
<body>
  <img src="{STREAM_PATH}" alt="Live stream">
</body>
</html>
""".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _serve_stream(self):
        """Stream MJPEG frames using multipart/x-mixed-replace."""
        self.send_response(200)
        self.send_header(
            "Content-Type",
            f"multipart/x-mixed-replace; boundary={BOUNDARY.decode()}",
        )
        # Disable buffering on the client side
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()

        event = broadcaster.subscribe()
        try:
            while True:
                # Wait for the next frame (timeout lets us check if client left)
                event.wait(timeout=5.0)
                event.clear()

                frame = broadcaster.get_frame()
                if not frame:
                    continue

                # Write one multipart chunk: boundary + headers + JPEG data
                try:
                    self.wfile.write(
                        b"--" + BOUNDARY + CRLF
                        + b"Content-Type: image/jpeg" + CRLF
                        + b"Content-Length: " + str(len(frame)).encode() + CRLF
                        + CRLF
                        + frame
                        + CRLF
                    )
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # Client disconnected — exit cleanly without crashing
                    break
        finally:
            broadcaster.unsubscribe(event)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== SurfTrak Video Stream ===", flush=True)
    print(f"Starting libcamera-vid capture at 1920x1080 @ 30fps...", flush=True)
    broadcaster.start()

    server = HTTPServer((HOST, PORT), StreamHandler)
    print(f"Stream live at: http://192.168.4.1:{PORT}{STREAM_PATH}", flush=True)
    print(f"Or open:        http://192.168.4.1:{PORT}/  (full HTML page)", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[stream] Shutting down.", flush=True)
        server.server_close()
