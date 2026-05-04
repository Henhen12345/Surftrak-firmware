#!/bin/bash
# hotspot.sh — Privileged hotspot control for SurfTrak.
# Called via sudo by ble_server.py. Do not invoke directly.
#
# Usage:
#   sudo /home/<user>/hotspot.sh enable <ssid>
#   sudo /home/<user>/hotspot.sh disable

set -euo pipefail

HOTSPOT_IP="192.168.50.1"
HOTSPOT_CHANNEL="6"
HOTSPOT_PASSWD="surftrak2024"

HOSTAPD_PID="/tmp/surftrak_hostapd.pid"
DNSMASQ_PID="/tmp/surftrak_dnsmasq.pid"
HOSTAPD_CONF="/tmp/surftrak_hostapd.conf"
DNSMASQ_CONF="/tmp/surftrak_dnsmasq.conf"
SAVED_CONN="/tmp/surftrak_saved_conn"

log() { echo "[hotspot.sh] $*"; }

enable_hotspot() {
    local ssid="$1"
    log "Enabling AP: ${ssid}"

    # Save the name of the active WiFi connection so we can restore it later
    local conn=""
    conn=$(nmcli -t -f NAME,DEVICE con show --active 2>/dev/null \
           | grep ":wlan0$" | cut -d: -f1 | head -1 || true)
    printf '%s' "$conn" > "$SAVED_CONN"
    log "Saved connection: '${conn:-<none>}'"

    # Hand wlan0 out of NetworkManager's control
    nmcli device set wlan0 managed no

    # Configure a static IP on the interface
    ip link set wlan0 down
    ip addr flush dev wlan0
    ip addr add "${HOTSPOT_IP}/24" dev wlan0
    ip link set wlan0 up

    # Write hostapd configuration
    cat > "$HOSTAPD_CONF" <<EOF
interface=wlan0
driver=nl80211
ssid=${ssid}
hw_mode=g
channel=${HOTSPOT_CHANNEL}
wpa=2
wpa_passphrase=${HOTSPOT_PASSWD}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF

    # Write dnsmasq configuration (DHCP only, no DNS forwarding needed)
    cat > "$DNSMASQ_CONF" <<EOF
interface=wlan0
bind-interfaces
dhcp-range=192.168.50.10,192.168.50.50,255.255.255.0,1h
dhcp-option=3,${HOTSPOT_IP}
EOF

    # Start hostapd in background, writing its PID for cleanup
    hostapd -B -P "$HOSTAPD_PID" "$HOSTAPD_CONF"
    sleep 1   # let the radio associate before DHCP clients connect

    # Start dnsmasq (daemonises itself)
    dnsmasq -C "$DNSMASQ_CONF" --pid-file="$DNSMASQ_PID"

    log "Hotspot active — ${ssid} @ ${HOTSPOT_IP}:8080"
}

disable_hotspot() {
    log "Disabling hotspot"

    # Stop dnsmasq
    if [ -f "$DNSMASQ_PID" ]; then
        kill "$(cat "$DNSMASQ_PID")" 2>/dev/null || true
        rm -f "$DNSMASQ_PID"
    fi

    # Stop hostapd
    if [ -f "$HOSTAPD_PID" ]; then
        kill "$(cat "$HOSTAPD_PID")" 2>/dev/null || true
        rm -f "$HOSTAPD_PID"
    fi
    pkill -x hostapd 2>/dev/null || true   # belt-and-suspenders

    # Bring wlan0 down cleanly
    ip link set wlan0 down
    ip addr flush dev wlan0

    # Give wlan0 back to NetworkManager
    nmcli device set wlan0 managed yes

    # Reconnect to whichever network we were on before
    local conn=""
    if [ -f "$SAVED_CONN" ]; then
        conn=$(cat "$SAVED_CONN")
        rm -f "$SAVED_CONN"
    fi

    sleep 1   # let NM re-detect the device before connecting
    if [ -n "$conn" ]; then
        log "Reconnecting to: ${conn}"
        nmcli con up "$conn" 2>/dev/null \
            || nmcli device connect wlan0 2>/dev/null \
            || true
    else
        nmcli device connect wlan0 2>/dev/null || true
    fi

    log "WiFi client restored"
}

case "${1:-}" in
    enable)
        [ -n "${2:-}" ] || { log "ERROR: ssid required"; exit 1; }
        enable_hotspot "$2"
        ;;
    disable)
        disable_hotspot
        ;;
    *)
        log "Usage: $0 {enable <ssid>|disable}"
        exit 1
        ;;
esac
