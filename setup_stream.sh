#!/bin/bash
# setup_stream.sh
# Installs stream.py and its systemd service unit so the MJPEG stream
# starts automatically on boot.
# Must be run as root (sudo) from the directory containing stream.py.

set -e

echo "=== SurfTrak Stream Service Setup ==="
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STREAM_PY="$SCRIPT_DIR/stream.py"
SERVICE_FILE="$SCRIPT_DIR/stream.service"

# Verify the source files exist before proceeding
if [ ! -f "$STREAM_PY" ]; then
    echo "ERROR: stream.py not found at $STREAM_PY"
    exit 1
fi
if [ ! -f "$SERVICE_FILE" ]; then
    echo "ERROR: stream.service not found at $SERVICE_FILE"
    exit 1
fi

# Step 1: Copy stream.py to the pi home directory
echo "[1/4] Copying stream.py to /home/pi/stream.py..."
# Detect the non-root user's home directory
TARGET_USER=${SUDO_USER:-pi}
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)

cp "$STREAM_PY" "$TARGET_HOME/stream.py"
chown "$TARGET_USER:$TARGET_USER" "$TARGET_HOME/stream.py"
chmod 755 "$TARGET_HOME/stream.py"

# Step 2: Copy the systemd unit file into place
# Patch the service file with the actual username and home directory before installing
echo "[2/4] Installing stream.service to /etc/systemd/system/..."
sed "s|User=pi|User=$TARGET_USER|g; s|/home/pi/|$TARGET_HOME/|g" \
    "$SERVICE_FILE" > /etc/systemd/system/stream.service
chmod 644 /etc/systemd/system/stream.service

# Step 3: Reload systemd so it picks up the new unit
echo "[3/4] Reloading systemd daemon..."
systemctl daemon-reload

# Step 4: Enable the service to start on every boot
echo "[4/4] Enabling stream.service on boot..."
systemctl enable stream.service

echo ""
echo "=== Stream service configured ==="
echo ""
echo "The stream will start automatically after the next reboot."
echo "To start it right now (without rebooting):"
echo "    sudo systemctl start stream.service"
echo ""
echo "To check status:"
echo "    systemctl status stream.service"
echo "    journalctl -u stream -f"
echo ""
echo "Stream URL (once hotspot is up):"
echo "    http://192.168.4.1:8080/stream"
