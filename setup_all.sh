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

echo "[1/8] Installing dependencies (bless, ffmpeg)..."
pip install bless --break-system-packages
apt-get install -y ffmpeg
echo ""

# ── Step 2: Configure and enable Bluetooth ───────────────────────────────────

echo "[2/8] Configuring Bluetooth..."

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

echo "[3/8] Creating recordings directory..."
mkdir -p "$TARGET_HOME/recordings"
chown "$TARGET_USER:$TARGET_USER" "$TARGET_HOME/recordings"
echo "    Created: $TARGET_HOME/recordings"
echo ""

# ── Step 4: Copy Python scripts ──────────────────────────────────────────────

echo "[4/8] Copying ble_server.py and file_server.py..."
cp "$SCRIPT_DIR/ble_server.py"   "$TARGET_HOME/ble_server.py"
cp "$SCRIPT_DIR/file_server.py"  "$TARGET_HOME/file_server.py"
chown "$TARGET_USER:$TARGET_USER" "$TARGET_HOME/ble_server.py" "$TARGET_HOME/file_server.py"
chmod 755 "$TARGET_HOME/ble_server.py" "$TARGET_HOME/file_server.py"
echo ""

# ── Step 5: Install systemd service files ────────────────────────────────────

echo "[5/8] Installing systemd service files..."

# Patch User= and home directory paths to match the real username
for SVC in ble_server file_server; do
    sed "s|User=pi|User=$TARGET_USER|g; s|/home/pi/|$TARGET_HOME/|g" \
        "$SCRIPT_DIR/${SVC}.service" > "/etc/systemd/system/${SVC}.service"
    chmod 644 "/etc/systemd/system/${SVC}.service"
    echo "    Installed: /etc/systemd/system/${SVC}.service"
done
echo ""

# ── Step 6: Reload systemd ────────────────────────────────────────────────────

echo "[6/8] Reloading systemd daemon..."
systemctl daemon-reload
echo ""

# ── Step 7: Enable services ───────────────────────────────────────────────────

echo "[7/8] Enabling services on boot..."
systemctl enable ble_server.service
systemctl enable file_server.service
echo ""

# ── Step 8: Start services ────────────────────────────────────────────────────

echo "[8/8] Starting services..."
systemctl start file_server.service
systemctl start ble_server.service
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

PI_IP=$(hostname -I | awk '{print $1}')

echo "╔══════════════════════════════════════════════╗"
echo "║          SurfTrak firmware installed!        ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  BLE       : Pi advertising as 'SurfTrak'"
echo "  File server: http://${PI_IP}:8080"
echo "  Recordings : $TARGET_HOME/recordings/"
echo ""
echo "  Useful commands:"
echo "    sudo journalctl -u ble_server -f     # BLE logs"
echo "    sudo journalctl -u file_server -f    # File server logs"
echo "    systemctl status ble_server"
echo "    systemctl status file_server"
echo "    ls -lh $TARGET_HOME/recordings/"
echo "    curl http://localhost:8080/clips"
echo ""

# ── SurfTrak full-firmware extensions ─────────────────────────────────────────

echo "[EXT] Installing new dependencies (hostapd, dnsmasq, ffmpeg)..."
sudo apt-get install -y hostapd dnsmasq ffmpeg
echo ""

echo "[EXT] Installing Python packages..."
pip3 install spidev RPi.GPIO bleak rpi-ws281x bless --break-system-packages
echo ""

echo "[EXT] Enabling SPI interface..."
if ! grep -q "dtparam=spi=on" /boot/firmware/config.txt 2>/dev/null; then
    echo "dtparam=spi=on" | sudo tee -a /boot/firmware/config.txt
    echo "    Added: dtparam=spi=on to /boot/firmware/config.txt"
elif ! grep -q "dtparam=spi=on" /boot/config.txt 2>/dev/null; then
    echo "dtparam=spi=on" | sudo tee -a /boot/config.txt
    echo "    Added: dtparam=spi=on to /boot/config.txt"
else
    echo "    SPI already enabled — skipping."
fi
echo ""

echo "[EXT] Running hotspot setup..."
bash "$SCRIPT_DIR/setup_hotspot.sh"
echo ""

echo "[EXT] Swapping to surftrak.service (replacing ble_server + file_server)..."
sudo systemctl disable ble_server.service file_server.service 2>/dev/null || true
sed "s|User=pi|User=$TARGET_USER|g; s|/home/pi/|$TARGET_HOME/|g" \
    "$SCRIPT_DIR/surftrak.service" | sudo tee /etc/systemd/system/surftrak.service > /dev/null
sudo chmod 644 /etc/systemd/system/surftrak.service
sudo systemctl daemon-reload
sudo systemctl enable surftrak.service
echo "    Installed: /etc/systemd/system/surftrak.service"
echo ""

echo "[EXT] Creating required directories..."
mkdir -p "$TARGET_HOME/logs" "$TARGET_HOME/.surftrak" "$TARGET_HOME/recordings"
chown -R "$TARGET_USER:$TARGET_USER" \
    "$TARGET_HOME/logs" "$TARGET_HOME/.surftrak" "$TARGET_HOME/recordings"
echo ""

echo "[EXT] SurfTrak firmware installed. Reboot to start."
echo "    sudo journalctl -u surftrak -f   # runtime logs"
echo ""
