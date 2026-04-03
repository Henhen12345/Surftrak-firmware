# SurfTrak Firmware

BLE-controlled surf session recorder for Raspberry Pi Zero 2W.
Pairs with the SurfTrak iOS app over Bluetooth to start/stop recording.
Downloads H.264 clips to the app over WiFi for AI processing.

---

## Hardware

| Part | Detail |
|---|---|
| Pi | Raspberry Pi Zero 2W |
| OS | Raspberry Pi OS Lite (Bookworm or Trixie, 64-bit) |
| Camera | Arducam IMX519 16MP (CSI ribbon cable) |
| Network | Pi and iPhone on the same WiFi network |

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

Ensure the Pi and your iPhone are on the **same WiFi network**.

### 4. Run the firmware installer

```bash
cd ~/Surftrak-firmware
sudo bash setup_all.sh
```

This installs and starts both services. No reboot needed.

---

## How it works

```
iOS app  ──BLE──►  Pi: "START"  →  rpicam-vid begins recording H.264
iOS app  ──BLE──►  Pi: "STOP"   →  recording stops, clip list notified
iOS app  ──WiFi─►  GET /clips   →  JSON list of recordings
iOS app  ──WiFi─►  GET /clips/<filename>  →  download H.264 file
```

1. iOS app scans BLE → finds **SurfTrak** → connects
2. App sends `START` → Pi records to `/home/<user>/recordings/surf_YYYY-MM-DD_HH-MM-SS.h264`
3. App sends `STOP` → Pi stops recording, notifies app of updated clip list
4. App fetches `http://<Pi-IP>:8080/clips` to see available recordings
5. App downloads clip via `http://<Pi-IP>:8080/clips/<filename>`

---

## BLE Profile

| Item | UUID |
|---|---|
| Service | `12345678-1234-1234-1234-123456789012` |
| Record char | `12345678-1234-1234-1234-123456789013` |
| Status char | `12345678-1234-1234-1234-123456789014` |
| Clips char | `12345678-1234-1234-1234-123456789015` |

**Record** (write without response): send `START` or `STOP`

**Status** (notify): receives `RECORDING`, `IDLE`, or `ERROR:<message>`

**Clips** (read + notify): JSON array of filenames, newest first
```json
["surf_2026-04-02_14-30-00.h264", "surf_2026-04-02_13-00-00.h264"]
```

---

## HTTP File Server API

Base URL: `http://<Pi-IP>:8080`

| Method | Path | Description |
|---|---|---|
| GET | `/clips` | JSON list of all clips (name, size, created) |
| GET | `/clips/<filename>` | Download H.264 file |
| DELETE | `/clips/<filename>` | Delete a clip |
| GET | `/status` | Recording state + disk space |

### Example responses

`GET /clips`
```json
{
  "clips": [
    {"name": "surf_2026-04-02_14-30-00.h264", "size": 104857600, "created": "2026-04-02T14:30:00"}
  ]
}
```

`GET /status`
```json
{"recording": false, "current_clip": null, "clip_count": 3, "disk_free_gb": 12.4}
```

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

# Find Pi IP (for use in iOS app)
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

- Confirm iPhone and Pi are on the same WiFi network
- Get Pi IP: `hostname -I | awk '{print $1}'`
- Test: `curl http://<Pi-IP>:8080/status`
- Check service: `systemctl status file_server`

### Recording fails to start

```bash
sudo journalctl -u ble_server -n 50
# Look for rpicam-vid errors
rpicam-vid -t 3000 --width 1920 --height 1080 --framerate 30 --codec h264 -o /tmp/test.h264
```

---

## File overview

| File | Purpose |
|---|---|
| `install_arducam.sh` | Installs Arducam IMX519 kernel driver |
| `ble_server.py` | BLE peripheral — handles START/STOP, controls rpicam-vid |
| `file_server.py` | HTTP server — clip listing and download over WiFi |
| `ble_server.service` | systemd unit for ble_server.py |
| `file_server.service` | systemd unit for file_server.py |
| `setup_all.sh` | Installs everything and starts both services |
