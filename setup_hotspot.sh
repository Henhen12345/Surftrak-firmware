#!/bin/bash
# setup_hotspot.sh
# Configures the Pi Zero 2W as a WiFi access point using hostapd + dnsmasq.
# Do NOT use NetworkManager — this uses raw hostapd/dnsmasq for reliability.
# Must be run as root (sudo). Reboot required after this script completes.

set -e

echo "=== SurfTrak WiFi Hotspot Setup ==="
echo ""

# Step 1: Install hostapd and dnsmasq
echo "[1/6] Installing hostapd and dnsmasq..."
apt-get update -qq
apt-get install -y hostapd dnsmasq

# Unmask hostapd AFTER install — apt masks it by default on Debian/Trixie,
# so unmasking before install gets overwritten by the postinst script.
systemctl unmask hostapd

# Step 2: Write hostapd configuration
# Sets up WPA2, SSID=SurfTrak, channel 6 on wlan0
echo "[2/6] Writing /etc/hostapd/hostapd.conf..."
cat > /etc/hostapd/hostapd.conf << 'EOF'
# hostapd.conf — WiFi access point configuration for SurfTrak

interface=wlan0
driver=nl80211

# WiFi network identity
ssid=SurfTrak
wpa_passphrase=surftrak1

# 2.4 GHz, channel 6 (reliable for Pi Zero 2W; avoid DFS channels)
hw_mode=g
channel=6
ieee80211n=1
wmm_enabled=0

# WPA2 only
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP

# Broadcast the SSID
ignore_broadcast_ssid=0
EOF

# Point hostapd at the config file
echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' > /etc/default/hostapd

# Step 3: Write dnsmasq configuration
# Backs up the original dnsmasq.conf and replaces it.
echo "[3/6] Writing /etc/dnsmasq.conf..."
if [ -f /etc/dnsmasq.conf ] && [ ! -f /etc/dnsmasq.conf.orig ]; then
    cp /etc/dnsmasq.conf /etc/dnsmasq.conf.orig
fi

cat > /etc/dnsmasq.conf << 'EOF'
# dnsmasq.conf — DHCP server for SurfTrak hotspot

# Only listen on the wlan0 interface (never touch eth0 or lo for DHCP)
interface=wlan0
bind-interfaces

# DHCP pool: hand out addresses .10–.50, 24-hour lease
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,24h

# Assign a friendly hostname for the Pi itself
dhcp-host=pi,192.168.4.1
EOF

# Step 4: Set a static IP on wlan0
# /etc/network/interfaces.d/ may not exist on Trixie — create it if needed.
echo "[4/6] Writing /etc/network/interfaces.d/wlan0..."
mkdir -p /etc/network/interfaces.d
cat > /etc/network/interfaces.d/wlan0 << 'EOF'
# Static IP for wlan0 — required for the hotspot to have a fixed address

auto wlan0
iface wlan0 inet static
    address 192.168.4.1
    netmask 255.255.255.0
EOF

# Step 5: Disable wpa_supplicant on wlan0 so it doesn't fight with hostapd
# On Bookworm, rfkill may block wlan0 — ensure it's unblocked at boot
echo "[5/6] Disabling wpa_supplicant / rfkill conflicts..."

# Prevent wpa_supplicant from managing wlan0
if [ -f /etc/wpa_supplicant/wpa_supplicant.conf ]; then
    # Leave the file but wpa_supplicant won't be needed; mask it safely
    systemctl disable wpa_supplicant 2>/dev/null || true
fi

# Ensure rfkill doesn't block WiFi at boot
# Add a small rc.local-style unblock just in case
if ! grep -q "rfkill unblock wifi" /etc/rc.local 2>/dev/null; then
    # Insert before 'exit 0' if rc.local exists, else create it
    if [ -f /etc/rc.local ]; then
        sed -i '/^exit 0/i rfkill unblock wifi' /etc/rc.local
    else
        printf '#!/bin/sh\nrfkill unblock wifi\nexit 0\n' > /etc/rc.local
        chmod +x /etc/rc.local
    fi
fi

# Step 6: Enable services at boot
echo "[6/6] Enabling hostapd and dnsmasq on boot..."
systemctl enable hostapd
systemctl enable dnsmasq

echo ""
echo "=== Hotspot configured — reboot to activate ==="
echo ""
echo "After reboot:"
echo "  SSID     : SurfTrak"
echo "  Password : surftrak1"
echo "  Pi IP    : 192.168.4.1"
echo "  DHCP     : 192.168.4.10 – 192.168.4.50"
