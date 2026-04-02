#!/bin/bash
# install_arducam.sh
# Installs the Arducam IMX519 kernel driver on Raspberry Pi OS Bookworm (64-bit).
# Must be run as root (sudo). Reboot required after this script completes.

set -e

echo "=== Arducam IMX519 Driver Installer ==="
echo "Running as: $(whoami)"
echo ""

# Step 1: Download the Arducam package installer to a temp file.
# NOTE: The repo was renamed from arducam_pivariety_kernel_module to
#       Arducam-Pivariety-V4L2-Driver — the old URL returns a 404.
# We download to a file first (rather than piping to bash) so we can verify
# the download succeeded before executing anything.
INSTALL_SCRIPT=$(mktemp /tmp/install_pivariety_pkgs.XXXXXX.sh)

echo "[1/3] Downloading Arducam install script..."
HTTP_STATUS=$(curl -L \
    --output "$INSTALL_SCRIPT" \
    --write-out "%{http_code}" \
    --silent \
    "https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases/download/install_script/install_pivariety_pkgs.sh")

if [ "$HTTP_STATUS" != "200" ]; then
    echo "ERROR: Download failed (HTTP $HTTP_STATUS)."
    echo "Check your internet connection, or download manually from:"
    echo "  https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases"
    rm -f "$INSTALL_SCRIPT"
    exit 1
fi

# Sanity-check: the file should start with a shebang, not an HTML/error page
if ! head -1 "$INSTALL_SCRIPT" | grep -q '^#!'; then
    echo "ERROR: Downloaded file does not look like a shell script."
    echo "First line: $(head -1 "$INSTALL_SCRIPT")"
    echo "The release URL may have changed. Check:"
    echo "  https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases"
    rm -f "$INSTALL_SCRIPT"
    exit 1
fi

chmod +x "$INSTALL_SCRIPT"

# Run the bootstrap — this adds the Arducam apt repo and installs the helper
bash "$INSTALL_SCRIPT"

# The bootstrap installs install_pivariety_pkgs.sh to /usr/local/bin
# (or the current directory). Locate it.
if command -v install_pivariety_pkgs.sh &>/dev/null; then
    PKG_INSTALLER="install_pivariety_pkgs.sh"
elif [ -f "$INSTALL_SCRIPT" ]; then
    PKG_INSTALLER="$INSTALL_SCRIPT"
else
    echo "ERROR: install_pivariety_pkgs.sh not found after bootstrap."
    exit 1
fi

# Step 2: Install the IMX519 standalone driver + libcamera tuning file
echo "[2/3] Installing IMX519 standalone driver..."
"$PKG_INSTALLER" -p imx519_standalone_driver

rm -f "$INSTALL_SCRIPT"

# Step 3: Add the device tree overlay to config.txt
# Bookworm uses /boot/firmware/config.txt (NOT /boot/config.txt).
CONFIG=/boot/firmware/config.txt

OVERLAY_LINE="dtoverlay=arducam-pivariety,cam0"

if grep -qF "$OVERLAY_LINE" "$CONFIG"; then
    echo "[3/3] Overlay already present in $CONFIG — skipping."
else
    echo "[3/3] Adding dtoverlay to $CONFIG..."
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
echo "    libcamera-hello --list-cameras"
echo ""
echo "You should see 'imx519' listed as cam0."
echo ""
echo "--- Troubleshooting ---"
echo "If the camera is NOT detected after reboot:"
echo "  1. Check the ribbon cable is fully seated at both ends (Pi and camera)."
echo "     The Zero 2W uses a 22-pin to 15-pin adapter cable — confirm orientation."
echo "  2. Confirm the overlay line in $CONFIG:"
echo "     grep arducam $CONFIG"
echo "  3. Check kernel module loaded:"
echo "     lsmod | grep arducam"
echo "  4. Check dmesg for CSI/camera errors:"
echo "     dmesg | grep -i 'imx\|arducam\|csi\|unicam'"
echo "  5. Ensure you installed the 64-bit driver on a 64-bit OS:"
echo "     uname -m   (should show aarch64)"
