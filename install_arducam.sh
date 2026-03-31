#!/bin/bash
# install_arducam.sh
# Installs the Arducam IMX519 kernel driver on Raspberry Pi OS Bookworm (64-bit).
# Must be run as root (sudo). Reboot required after this script completes.

set -e

echo "=== Arducam IMX519 Driver Installer ==="
echo "Running as: $(whoami)"
echo ""

# Step 1: Download and run the Arducam package installer bootstrap
echo "[1/3] Downloading Arducam install script..."
curl -L https://github.com/ArduCAM/arducam_pivariety_kernel_module/releases/download/install_script/install_pivariety_pkgs.sh | bash

# Step 2: Install the IMX519 standalone driver package
# This installs the DKMS kernel module and the libcamera tuning file.
echo "[2/3] Installing IMX519 standalone driver..."
install_pivariety_pkgs.sh -p imx519_standalone_driver

# Step 3: Add the device tree overlay to config.txt
# Bookworm uses /boot/firmware/config.txt (NOT /boot/config.txt).
CONFIG=/boot/firmware/config.txt

OVERLAY_LINE="dtoverlay=arducam-pivariety,cam0"

if grep -qF "$OVERLAY_LINE" "$CONFIG"; then
    echo "[3/3] Overlay already present in $CONFIG — skipping."
else
    echo "[3/3] Adding dtoverlay to $CONFIG..."
    # Append under [all] section if present, otherwise just append at end
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
