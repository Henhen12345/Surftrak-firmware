# SurfTrak Firmware

Raspberry Pi Zero 2W firmware that broadcasts a WiFi hotspot and streams live video from an Arducam IMX519 (16MP) camera. Connect any device to the hotspot and open a browser to watch the stream.

---

## Hardware

| Part | Detail |
|---|---|
| Pi | Raspberry Pi Zero 2W |
| OS | Raspberry Pi OS Lite (Bookworm, 64-bit) |
| Camera | Arducam IMX519 16MP (CSI ribbon cable) |

---

## Setup Order

### Step 0 — Flash the SD card

1. Flash **Raspberry Pi OS Lite (64-bit, Bookworm)** with Raspberry Pi Imager.
2. In Imager's advanced settings, enable SSH and set username `pi` / your password.
3. Boot the Pi on a normal WiFi network (or wired) for the initial install.

---

### Step 1 — Install the Arducam IMX519 driver

The IMX519 is not natively supported by Pi OS — you must install the Arducam driver first.

```bash
sudo bash install_arducam.sh
sudo reboot
```

After reboot, verify the camera:

```bash
libcamera-hello --list-cameras
```

You should see `imx519` listed as `cam0`. If not, see [Troubleshooting](#troubleshooting).

---

### Step 2 — Run the master setup script

```bash
sudo bash setup_all.sh
```

This will:
- Install and configure `hostapd` + `dnsmasq` for the WiFi hotspot
- Install `stream.py` and enable it as a systemd service

When it finishes, reboot:

```bash
sudo reboot
```

---

## Connecting your iPhone

### 1. Join the SurfTrak hotspot

1. On iPhone, go to **Settings → Wi-Fi**
2. Select **SurfTrak**
3. Enter password: **surftrak1**
4. Wait for the checkmark — you are now on the Pi's local network

### 2. Watch the live stream in Safari

1. Open **Safari**
2. Type this URL in the address bar:

```
http://192.168.4.1:8080/stream
```

3. The live camera feed will appear immediately. No app needed.

> **Tip:** You can also open `http://192.168.4.1:8080/` for a full-screen HTML page that embeds the stream.

---

## SSH into the Pi from a Mac

Once your Mac is connected to the SurfTrak hotspot:

```bash
ssh pi@192.168.4.1
```

Enter the password you set during Raspberry Pi Imager setup.

---

## Checking service status

SSH into the Pi, then:

```bash
# Is the hotspot running?
sudo systemctl status hostapd
sudo systemctl status dnsmasq

# Is the stream running?
sudo systemctl status stream

# Watch live stream logs
sudo journalctl -u stream -f
```

---

## Troubleshooting

### Camera not detected after reboot

```bash
libcamera-hello --list-cameras
# Should show: Available cameras: imx519 [cam0]
```

**If not detected:**

1. **Check ribbon cable.** The Zero 2W uses a 22-pin to 15-pin CSI adapter. Confirm the cable is fully inserted and the locking tab is closed at both ends.
2. **Check the overlay is in config.txt:**
   ```bash
   grep arducam /boot/firmware/config.txt
   # Should show: dtoverlay=arducam-pivariety,cam0
   ```
3. **Check the kernel module loaded:**
   ```bash
   lsmod | grep arducam
   ```
4. **Check dmesg for errors:**
   ```bash
   dmesg | grep -i 'imx\|arducam\|csi\|unicam'
   ```
5. **Confirm you're running 64-bit OS:**
   ```bash
   uname -m   # should say aarch64
   ```

---

### Hotspot not appearing on iPhone

1. **Check hostapd is running:**
   ```bash
   sudo systemctl status hostapd
   ```
2. **Look at hostapd logs:**
   ```bash
   sudo journalctl -u hostapd -n 50
   ```
3. **Confirm wlan0 has the static IP:**
   ```bash
   ip addr show wlan0
   # Should show: inet 192.168.4.1/24
   ```
4. **rfkill blocking WiFi?**
   ```bash
   rfkill list
   # If wlan is "Soft blocked: yes", run: sudo rfkill unblock wifi
   ```
5. **Restart hostapd manually:**
   ```bash
   sudo systemctl restart hostapd
   ```

---

### Stream not loading in Safari

1. **Confirm iPhone is on SurfTrak WiFi** (not LTE/5G — disable mobile data if needed).
2. **Check stream service is running:**
   ```bash
   sudo systemctl status stream
   ```
3. **Check stream logs:**
   ```bash
   sudo journalctl -u stream -n 50
   ```
4. **Confirm stream is listening on port 8080:**
   ```bash
   ss -tlnp | grep 8080
   ```
5. **Restart the stream manually:**
   ```bash
   sudo systemctl restart stream
   ```
6. **Test with curl from the Pi itself:**
   ```bash
   curl -v http://192.168.4.1:8080/stream --max-time 3
   # Should return: Content-Type: multipart/x-mixed-replace
   ```

---

## File Overview

| File | Purpose |
|---|---|
| `install_arducam.sh` | Installs Arducam IMX519 kernel driver |
| `setup_hotspot.sh` | Configures hostapd + dnsmasq WiFi hotspot |
| `setup_stream.sh` | Installs stream service |
| `setup_all.sh` | Master script — runs hotspot + stream setup |
| `stream.py` | MJPEG HTTP streaming server |
| `stream.service` | systemd unit file for stream.py |

---

## Network Reference

| Item | Value |
|---|---|
| SSID | SurfTrak |
| Password | surftrak1 |
| Pi IP | 192.168.4.1 |
| Stream URL | http://192.168.4.1:8080/stream |
| SSH | ssh pi@192.168.4.1 |
| DHCP range | 192.168.4.10 – 192.168.4.50 |
