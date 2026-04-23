#!/bin/bash
# setup_hotspot.sh — Idempotent WiFi access point setup for SurfTrak.
# Safe to run multiple times. Must be run as root (sudo).

set -e

# ── Derive unique SSID suffix from Pi serial number ───────────────────────────

SERIAL=$(grep -oP '(?<=Serial\s\s: )[0-9a-f]+' /proc/cpuinfo | tail -c 5 | head -c 4)
if [ -z "$SERIAL" ]; then
    SERIAL="0000"
fi
SSID="SurfTrak-${SERIAL}"

# ── Install dependencies ──────────────────────────────────────────────────────

echo "[HOTSPOT] Installing hostapd and dnsmasq..."
sudo apt-get install -y hostapd dnsmasq

# ── hostapd configuration ─────────────────────────────────────────────────────

echo "[HOTSPOT] Writing /etc/hostapd/hostapd.conf..."
sudo tee /etc/hostapd/hostapd.conf > /dev/null <<EOF
interface=wlan0
driver=nl80211
ssid=${SSID}
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=surftrak2024
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
EOF

# Point hostapd daemon at the config file
HOSTAPD_DEFAULT=/etc/default/hostapd
if grep -q '^#DAEMON_CONF' "$HOSTAPD_DEFAULT" 2>/dev/null; then
    sudo sed -i 's|^#DAEMON_CONF.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' "$HOSTAPD_DEFAULT"
elif ! grep -q 'DAEMON_CONF' "$HOSTAPD_DEFAULT" 2>/dev/null; then
    echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' | sudo tee -a "$HOSTAPD_DEFAULT" > /dev/null
else
    sudo sed -i 's|^DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' "$HOSTAPD_DEFAULT"
fi

# ── dnsmasq configuration ─────────────────────────────────────────────────────

echo "[HOTSPOT] Writing /etc/dnsmasq.conf..."
sudo tee /etc/dnsmasq.conf > /dev/null <<EOF
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
EOF

# ── Static IP for wlan0 via dhcpcd ────────────────────────────────────────────

DHCPCD_CONF=/etc/dhcpcd.conf
MARKER="# SurfTrak hotspot static IP"

if ! grep -qF "$MARKER" "$DHCPCD_CONF" 2>/dev/null; then
    echo "[HOTSPOT] Adding static IP for wlan0 to $DHCPCD_CONF..."
    sudo tee -a "$DHCPCD_CONF" > /dev/null <<EOF

${MARKER}
interface wlan0
static ip_address=192.168.4.1/24
nohook wpa_supplicant
EOF
else
    echo "[HOTSPOT] Static IP already configured in $DHCPCD_CONF — skipping."
fi

# ── Enable services ───────────────────────────────────────────────────────────

echo "[HOTSPOT] Enabling hostapd and dnsmasq..."
sudo systemctl unmask hostapd
sudo systemctl enable hostapd
sudo systemctl enable dnsmasq

echo "[HOTSPOT] ${SSID} configured on 192.168.4.1"
echo "[HOTSPOT] Reboot to activate the access point."
