# SurfTrak Firmware

BLE-controlled surf session recorder for Raspberry Pi Zero 2W.
Pairs with the SurfTrak iOS app over Bluetooth to start/stop recording,
then creates a local WiFi hotspot for high-speed clip transfer.

---

## Hardware

| Part | Detail |
|---|---|
| Pi | Raspberry Pi Zero 2W |
| OS | Raspberry Pi OS Lite (Bookworm or Trixie, 64-bit) |
| Camera | Arducam IMX519 16MP (CSI ribbon cable) |
| Network | WiFi client mode for normal use; hotspot mode for post-session transfer |

---

## Setup

### 1. Flash the SD card

Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager.
In advanced settings: enable SSH, set username + password.

### 2. Install the Arducam driver

```bash
sudo bash install_arducam.sh
sudo reboot
```

After reboot, verify:

```bash
rpicam-hello --list-cameras
# Should list: imx519
```

### 3. Connect the Pi to your WiFi network

The Pi needs WiFi for initial SSH access. After sessions, it switches to hotspot
mode for transfer — no shared network is needed at that point.

### 4. Run the firmware installer

```bash
cd ~/Surftrak-firmware
sudo bash setup_all.sh
```

This installs and starts both services, configures the hotspot helper, and
disables the system-level hostapd/dnsmasq services (which SurfTrak manages
directly). No reboot needed.

---

## How it works

### Recording

```
iOS app  ──BLE──►  Pi: "START"  →  rpicam-vid begins recording H.264
iOS app  ──BLE──►  Pi: "STOP"   →  recording stops, H.264 wrapped to MP4
```

### Post-session transfer

```
Pi: "STOP" received
  → Pi creates SurfTrak-XXXX hotspot (hostapd + dnsmasq)
  → Pi sends BLE status: "HOTSPOT_READY"
iOS app connects to SurfTrak-XXXX  (password: surftrak2024)
  → App downloads clip: GET http://192.168.50.1:8080/clips/<filename>
iOS app sends BLE: "TRANSFER_COMPLETE"
  → Pi tears down hotspot, returns to WiFi client mode
  → Pi sends BLE status: "IDLE"
```

If the app disconnects or transfer takes longer than 3 minutes the hotspot
tears down automatically and the Pi returns to WiFi client mode.

---

## BLE Profile

| Item | UUID |
|---|---|
| Service | `12345678-1234-1234-1234-123456789012` |
| Record char | `12345678-1234-1234-1234-123456789013` |
| Status char | `12345678-1234-1234-1234-123456789014` |
| Clips char | `12345678-1234-1234-1234-123456789015` |

**Record** (write without response): send commands to the Pi

| Command | Description |
|---|---|
| `START` | Begin recording |
| `STOP` | Stop recording and start hotspot |
| `TRANSFER_COMPLETE` | Signal transfer done; Pi tears down hotspot |

**Status** (notify): Pi pushes state updates to the app

| Value | Meaning |
|---|---|
| `IDLE` | Waiting; ready to record |
| `RECORDING` | Recording in progress |
| `HOTSPOT_READY` | Hotspot is up; app can connect and download |
| `ERROR:<message>` | Something went wrong |

**Clips** (read + notify): JSON array of filenames, newest first
```json
["surf_2026-04-02_14-30-00.mp4", "surf_2026-04-02_13-00-00.mp4"]
```

---

## HTTP File Server API

Base URL (WiFi client mode): `http://<Pi-IP>:8080`
Base URL (hotspot mode): `http://192.168.50.1:8080`

| Method | Path | Description |
|---|---|---|
| GET | `/clips` | JSON list of all clips (name, size, created) |
| GET | `/clips/<filename>` | Download MP4 file |
| DELETE | `/clips/<filename>` | Delete a clip |
| GET | `/status` | Recording state + disk space |

### Example responses

`GET /clips`
```json
{
  "clips": [
    {"name": "surf_2026-04-02_14-30-00.mp4", "size": 104857600, "created": "2026-04-02T14:30:00"}
  ]
}
```

`GET /status`
```json
{"recording": false, "current_clip": null, "clip_count": 3, "disk_free_gb": 12.4}
```

---

## Hotspot details

| Property | Value |
|---|---|
| SSID | `SurfTrak-XXXX` where XXXX = last 4 hex chars of wlan0 MAC |
| Password | `surftrak2024` |
| Pi IP on hotspot | `192.168.50.1` |
| File server URL | `http://192.168.50.1:8080` |
| Transfer timeout | 3 minutes (auto-teardown if TRANSFER_COMPLETE not received) |

The hotspot uses channel 6, WPA2-PSK. DHCP assigns `192.168.50.10–50` to clients.

---

## Useful commands

```bash
# Service status
systemctl status ble_server
systemctl status file_server

# Live logs
sudo journalctl -u ble_server -f
sudo journalctl -u file_server -f

# List recordings
ls -lh ~/recordings/

# Test file server from the Pi itself
curl http://localhost:8080/clips
curl http://localhost:8080/status

# Find Pi IP (for use in iOS app before hotspot mode)
hostname -I | awk '{print $1}'
```

---

## Troubleshooting

### BLE not advertising

```bash
sudo systemctl status bluetooth
sudo systemctl restart bluetooth
sudo journalctl -u ble_server -n 50
```

Ensure Bluetooth is not blocked:
```bash
rfkill list
sudo rfkill unblock bluetooth
```

### Camera not detected

```bash
rpicam-hello --list-cameras
dmesg | grep -i imx519
```

Check ribbon cable orientation and seating (see `install_arducam.sh` comments).

### File server not reachable from iPhone

In WiFi client mode, confirm both iPhone and Pi are on the same network:
```bash
hostname -I | awk '{print $1}'   # Pi's IP
curl http://<Pi-IP>:8080/status
systemctl status file_server
```

In hotspot mode, the URL is always `http://192.168.50.1:8080`:
```bash
curl http://192.168.50.1:8080/status
```

### Hotspot not appearing after STOP

Check BLE logs for hotspot errors:
```bash
sudo journalctl -u ble_server -n 80
```

Test the hotspot script manually (run on the Pi over SSH while in client mode):
```bash
# Read the SSID the Pi will use
cat /sys/class/net/wlan0/address   # last 4 chars → XXXX

# Manually enable hotspot
sudo ~/hotspot.sh enable SurfTrak-XXXX

# Verify AP and DHCP processes started
pgrep -a hostapd
pgrep -a dnsmasq

# From another device, connect to SurfTrak-XXXX, then:
curl http://192.168.50.1:8080/status

# Manually disable and restore client mode
sudo ~/hotspot.sh disable

# Confirm wlan0 back under NetworkManager
nmcli device status
```

### Hotspot tears down before transfer completes

The auto-teardown timeout is 3 minutes. For large clips, ensure the iOS app
sends `TRANSFER_COMPLETE` promptly after the download finishes.

Check that `nmcli` can find the original connection to restore:
```bash
nmcli -t -f NAME,DEVICE con show --active | grep wlan0
```

### Recording fails to start

```bash
sudo journalctl -u ble_server -n 50
# Look for rpicam-vid errors
rpicam-vid -t 3000 --width 1920 --height 1080 --framerate 30 --codec h264 -o /tmp/test.h264
```

### Verify sudoers rule for hotspot

```bash
sudo cat /etc/sudoers.d/surftrak-hotspot
# Should show: <user> ALL=(ALL) NOPASSWD: /home/<user>/hotspot.sh
```

---

## File overview

| File | Purpose |
|---|---|
| `install_arducam.sh` | Installs Arducam IMX519 kernel driver |
| `ble_server.py` | BLE peripheral — handles START/STOP/TRANSFER_COMPLETE, manages hotspot |
| `file_server.py` | HTTP server — clip listing and download (runs throughout) |
| `hotspot.sh` | Privileged script that switches wlan0 between client and AP mode |
| `ble_server.service` | systemd unit for ble_server.py |
| `file_server.service` | systemd unit for file_server.py |
| `setup_all.sh` | Installs everything and starts both services |
