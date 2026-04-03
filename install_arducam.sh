#!/bin/bash
# install_arducam.sh
# Installs the Arducam IMX519 kernel driver on Raspberry Pi OS (64-bit).
# Supports both Bookworm and Trixie. Must be run as root (sudo).
# Reboot required after this script completes.

set -e

echo "=== Arducam IMX519 Driver Installer ==="
echo "Running as: $(whoami)"
echo ""

# Detect OS codename (bookworm, trixie, etc.) to pick the right libcamera package
OS_CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "Detected OS: $OS_CODENAME"
echo ""

# Download install_pivariety_pkgs.sh to a temp file and verify it before running
INSTALLER=$(mktemp /tmp/install_pivariety_pkgs.XXXXXX.sh)

echo "[1/4] Downloading Arducam install script..."
HTTP_STATUS=$(curl -L \
    --output "$INSTALLER" \
    --write-out "%{http_code}" \
    --silent \
    "https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases/download/install_script/install_pivariety_pkgs.sh")

if [ "$HTTP_STATUS" != "200" ]; then
    echo "ERROR: Download failed (HTTP $HTTP_STATUS)."
    echo "Check your internet connection, or download manually from:"
    echo "  https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases"
    rm -f "$INSTALLER"
    exit 1
fi

if ! head -1 "$INSTALLER" | grep -q '^#!'; then
    echo "ERROR: Downloaded file does not look like a shell script."
    echo "First line: $(head -1 "$INSTALLER")"
    rm -f "$INSTALLER"
    exit 1
fi

chmod +x "$INSTALLER"
echo "    Downloaded OK."
echo ""

# Step 2: Install the IMX519 kernel driver
echo "[2/4] Installing IMX519 kernel driver..."
bash "$INSTALLER" -p imx519_kernel_driver

# Step 3: Install the matching libcamera + libcamera-apps for this OS
# Trixie and Bookworm have separate package builds in Arducam's repo.
echo ""
echo "[3/4] Installing libcamera for $OS_CODENAME..."
case "$OS_CODENAME" in
    trixie)
        bash "$INSTALLER" -p libcamera_trixie
        bash "$INSTALLER" -p libcamera_apps_trixie
        ;;
    bookworm)
        bash "$INSTALLER" -p libcamera_bookworm
        bash "$INSTALLER" -p libcamera_apps_bookworm
        ;;
    *)
        # Fall back to the generic (Bullseye-era) packages
        echo "    Unknown OS '$OS_CODENAME' — trying generic libcamera packages..."
        bash "$INSTALLER" -p libcamera || true
        bash "$INSTALLER" -p libcamera_apps || true
        ;;
esac

rm -f "$INSTALLER"

# Step 4: Add the device tree overlay to config.txt
# Bookworm and Trixie both use /boot/firmware/config.txt
CONFIG=/boot/firmware/config.txt

# The imx519_kernel_driver uses the 'imx519' overlay (not arducam-pivariety)
OVERLAY_LINE="dtoverlay=imx519"

# Disable auto-detect so it doesn't conflict with the manual overlay
AUTODETECT_LINE="camera_auto_detect=0"

echo ""
echo "[4/4] Updating $CONFIG..."

# Disable camera_auto_detect — handles all cases: =1, commented out, or absent
if grep -q "^camera_auto_detect=1" "$CONFIG"; then
    sed -i "s/^camera_auto_detect=1/camera_auto_detect=0/" "$CONFIG"
    echo "    Set camera_auto_detect=0"
elif grep -q "^#.*camera_auto_detect" "$CONFIG"; then
    # Commented out — append an explicit =0 line after it
    sed -i "s/^#.*camera_auto_detect.*/&\ncamera_auto_detect=0/" "$CONFIG"
    echo "    Added: camera_auto_detect=0"
elif ! grep -q "^camera_auto_detect" "$CONFIG"; then
    echo "" >> "$CONFIG"
    echo "$AUTODETECT_LINE" >> "$CONFIG"
    echo "    Added: $AUTODETECT_LINE"
fi

if grep -qF "$OVERLAY_LINE" "$CONFIG"; then
    echo "    Overlay already present — skipping."
else
    echo "" >> "$CONFIG"
    echo "# Arducam IMX519 via CSI" >> "$CONFIG"
    echo "$OVERLAY_LINE" >> "$CONFIG"
    echo "    Added: $OVERLAY_LINE"
fi

echo ""
echo "=== Driver installation complete ==="
echo ""
echo "NEXT STEP: Reboot the Pi now:"
echo "    sudo reboot"
echo ""
echo "After reboot, verify the camera is detected:"
echo "    rpicam-hello --list-cameras"
echo ""
echo "You should see 'imx519' listed as cam0."
echo ""
echo "--- Troubleshooting ---"
echo "If the camera is NOT detected after reboot:"
echo "  1. Check the ribbon cable is fully seated at both ends."
echo "     The Zero 2W uses a 22-pin to 15-pin CSI adapter — confirm orientation."
echo "  2. Confirm the overlay in $CONFIG:"
echo "     grep -E 'imx519|camera_auto' $CONFIG"
echo "  3. Check kernel module loaded:"
echo "     lsmod | grep imx519"
echo "  4. Check dmesg for CSI/camera errors:"
echo "     dmesg | grep -i 'imx\|arducam\|csi\|unicam'"
echo "  5. Confirm 64-bit OS:"
echo "     uname -m   (should show aarch64)"
