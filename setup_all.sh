#!/bin/bash
# setup_all.sh — SurfTrak firmware installer.
# Installs BLE server + HTTP file server as systemd services.
# Must be run as root: sudo bash setup_all.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Detect the real (non-root) user and their home directory
TARGET_USER="${SUDO_USER:-pi}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║         SurfTrak Firmware Installer          ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "Installing for user : $TARGET_USER"
echo "Home directory      : $TARGET_HOME"
echo ""

# ── Prerequisite check ────────────────────────────────────────────────────────

echo "IMPORTANT: Before continuing, confirm:"
echo "  [1] You ran: sudo bash install_arducam.sh"
echo "  [2] You rebooted after the driver install"
echo "  [3] Camera is visible: rpicam-hello --list-cameras"
echo ""
read -r -p "Confirmed? (yes/no): " CONFIRMED
if [ "$CONFIRMED" != "yes" ]; then
    echo "Run install_arducam.sh first, then reboot, then re-run this script."
    exit 1
fi
echo ""

# ── Step 1: Install dependencies ─────────────────────────────────────────────

echo "[1/9] Installing dependencies (bless, ffmpeg, hostapd, dnsmasq)..."
pip install bless --break-system-packages
apt-get install -y ffmpeg hostapd dnsmasq
echo ""

# ── Step 2: Configure and enable Bluetooth ───────────────────────────────────

echo "[2/9] Configuring Bluetooth..."

# Enable experimental mode in BlueZ — required for GATT peripheral advertising
BTCONF=/etc/bluetooth/main.conf
if ! grep -q "^Experimental = true" "$BTCONF"; then
    sed -i '/\[General\]/a Experimental = true' "$BTCONF"
    echo "    Added Experimental = true to $BTCONF"
fi

# Add --experimental flag to bluetoothd if not already present
BTSVC=/lib/systemd/system/bluetooth.service
if ! grep -q "\-\-experimental" "$BTSVC"; then
    sed -i 's|ExecStart=/usr/libexec/bluetooth/bluetoothd|ExecStart=/usr/libexec/bluetooth/bluetoothd --experimental|' "$BTSVC"
    echo "    Added --experimental to bluetoothd"
fi

# Unblock Bluetooth in case rfkill is blocking it
rfkill unblock bluetooth 2>/dev/null || true

systemctl daemon-reload
systemctl enable bluetooth
systemctl restart bluetooth
sleep 2

# Power on the Bluetooth adapter
bluetoothctl power on || true
echo ""

# ── Step 3: Create recordings directory ──────────────────────────────────────

echo "[3/9] Creating recordings directory..."
mkdir -p "$TARGET_HOME/recordings"
chown "$TARGET_USER:$TARGET_USER" "$TARGET_HOME/recordings"
echo "    Created: $TARGET_HOME/recordings"
echo ""

# ── Step 4: Copy Python scripts ──────────────────────────────────────────────

echo "[4/9] Copying Python scripts and hotspot helper..."
cp "$SCRIPT_DIR/ble_server.py"   "$TARGET_HOME/ble_server.py"
cp "$SCRIPT_DIR/file_server.py"  "$TARGET_HOME/file_server.py"
cp "$SCRIPT_DIR/hotspot.sh"      "$TARGET_HOME/hotspot.sh"
chown "$TARGET_USER:$TARGET_USER" \
    "$TARGET_HOME/ble_server.py" \
    "$TARGET_HOME/file_server.py" \
    "$TARGET_HOME/hotspot.sh"
chmod 755 \
    "$TARGET_HOME/ble_server.py" \
    "$TARGET_HOME/file_server.py" \
    "$TARGET_HOME/hotspot.sh"
echo ""

# ── Step 5: Configure hotspot helper ─────────────────────────────────────────

echo "[5/9] Configuring hotspot helper (sudoers + disable system services)..."

# Grant passwordless sudo for the hotspot script only
SUDOERS_FILE="/etc/sudoers.d/surftrak-hotspot"
echo "$TARGET_USER ALL=(ALL) NOPASSWD: $TARGET_HOME/hotspot.sh" > "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"
echo "    Sudoers rule: $SUDOERS_FILE"

# Disable the system-level hostapd and dnsmasq services — SurfTrak manages
# these processes directly via hotspot.sh, not via systemd units.
systemctl disable hostapd.service 2>/dev/null || true
systemctl stop    hostapd.service 2>/dev/null || true
systemctl disable dnsmasq.service 2>/dev/null || true
systemctl stop    dnsmasq.service 2>/dev/null || true
echo "    hostapd and dnsmasq system services disabled (managed by hotspot.sh)"
echo ""

# ── Step 6: Install systemd service files ────────────────────────────────────

echo "[6/9] Installing systemd service files..."

# Patch User= and home directory paths to match the real username
for SVC in ble_server file_server; do
    sed "s|User=pi|User=$TARGET_USER|g; s|/home/pi/|$TARGET_HOME/|g" \
        "$SCRIPT_DIR/${SVC}.service" > "/etc/systemd/system/${SVC}.service"
    chmod 644 "/etc/systemd/system/${SVC}.service"
    echo "    Installed: /etc/systemd/system/${SVC}.service"
done
echo ""

# ── Step 7: Reload systemd ────────────────────────────────────────────────────

echo "[7/9] Reloading systemd daemon..."
systemctl daemon-reload
echo ""

# ── Step 8: Enable services ───────────────────────────────────────────────────

echo "[8/9] Enabling services on boot..."
systemctl enable ble_server.service
systemctl enable file_server.service
echo ""

# ── Step 9: Start services ────────────────────────────────────────────────────

echo "[9/9] Starting services..."
systemctl start file_server.service
systemctl start ble_server.service
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

PI_IP=$(hostname -I | awk '{print $1}')

echo "╔══════════════════════════════════════════════╗"
echo "║          SurfTrak firmware installed!        ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  BLE        : Pi advertising as 'SurfTrak'"
echo "  File server: http://${PI_IP}:8080  (WiFi client mode)"
echo "               http://192.168.50.1:8080  (hotspot mode)"
echo "  Recordings : $TARGET_HOME/recordings/"
echo ""
echo "  Useful commands:"
echo "    sudo journalctl -u ble_server -f     # BLE logs"
echo "    sudo journalctl -u file_server -f    # File server logs"
echo "    systemctl status ble_server"
echo "    systemctl status file_server"
echo "    ls -lh $TARGET_HOME/recordings/"
echo "    curl http://localhost:8080/clips"
echo "    curl http://localhost:8080/status"
echo ""
