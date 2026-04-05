#!/bin/bash
# install_arducam.sh
# Installs the Arducam IMX519 kernel driver on Raspberry Pi OS (64-bit).
# Supports Bookworm, Trixie, and kernel 6.x via DKMS source build.
# Must be run as root (sudo).
# Reboot required after this script completes.

set -e

echo "=== Arducam IMX519 Driver Installer ==="
echo "Running as: $(whoami)"
echo ""

KERNEL_VER=$(uname -r)
OS_CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "Kernel  : $KERNEL_VER"
echo "OS      : $OS_CODENAME"
echo ""

# ── Step 1: Build dependencies ────────────────────────────────────────────────

echo "[1/4] Installing build dependencies..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    dkms \
    git \
    "linux-headers-$(uname -r)" \
    build-essential
echo "    Done."
echo ""

# ── Step 2: DKMS source build ─────────────────────────────────────────────────
# Arducam's prebuilt package list only covers kernels up to 5.x.
# Building from source via DKMS works on any kernel version, including 6.12.

DRIVER_NAME="arducam-pivariety"
DRIVER_VER="1.0"
DRIVER_SRC="/usr/src/${DRIVER_NAME}-${DRIVER_VER}"

echo "[2/4] Building IMX519 driver via DKMS (kernel $KERNEL_VER)..."

# Remove any stale DKMS registration before rebuilding
if dkms status "$DRIVER_NAME/$DRIVER_VER" 2>/dev/null | grep -q .; then
    echo "    Removing existing DKMS registration..."
    dkms remove "$DRIVER_NAME/$DRIVER_VER" --all || true
fi

# Clone or refresh the driver source
if [ -d "$DRIVER_SRC/.git" ]; then
    echo "    Refreshing existing source..."
    git -C "$DRIVER_SRC" pull --ff-only
else
    rm -rf "$DRIVER_SRC"
    echo "    Cloning Arducam Pivariety driver source..."
    git clone --depth 1 \
        https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver.git \
        "$DRIVER_SRC"
fi

# Always write dkms.conf — the repo's source is in src/, so we must
# point MAKE and BUILT_MODULE_LOCATION there explicitly.
echo "    Writing dkms.conf..."
cat > "$DRIVER_SRC/dkms.conf" <<DKMS_CONF
PACKAGE_NAME="${DRIVER_NAME}"
PACKAGE_VERSION="${DRIVER_VER}"
MAKE[0]="make -C /lib/modules/\${kernelver}/build M=\${dkms_tree}/\${PACKAGE_NAME}/\${PACKAGE_VERSION}/build/src"
CLEAN="make -C /lib/modules/\${kernelver}/build M=\${dkms_tree}/\${PACKAGE_NAME}/\${PACKAGE_VERSION}/build/src clean"
BUILT_MODULE_NAME[0]="arducam"
BUILT_MODULE_LOCATION[0]="src/"
DEST_MODULE_LOCATION[0]="/kernel/drivers/media/i2c/"
AUTOINSTALL="yes"
DKMS_CONF

dkms add    "$DRIVER_NAME/$DRIVER_VER"
dkms build  "$DRIVER_NAME/$DRIVER_VER"
dkms install "$DRIVER_NAME/$DRIVER_VER"

echo "    Driver built and installed via DKMS."
echo ""

# ── Step 3: Arducam libcamera (userspace — prebuilt debs, kernel-independent) ─

INSTALLER=$(mktemp /tmp/install_pivariety_pkgs.XXXXXX.sh)

echo "[3/4] Installing Arducam libcamera for $OS_CODENAME..."
HTTP_STATUS=$(curl -L \
    --output "$INSTALLER" \
    --write-out "%{http_code}" \
    --silent \
    "https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases/download/install_script/install_pivariety_pkgs.sh")

if [ "$HTTP_STATUS" != "200" ]; then
    echo "WARNING: Could not download Arducam libcamera installer (HTTP $HTTP_STATUS)."
    echo "         Kernel driver is installed. Install libcamera manually if needed."
    rm -f "$INSTALLER"
else
    if ! head -1 "$INSTALLER" | grep -q '^#!'; then
        echo "WARNING: Downloaded file is not a shell script — skipping libcamera install."
        rm -f "$INSTALLER"
    else
        chmod +x "$INSTALLER"
        case "$OS_CODENAME" in
            trixie)
                bash "$INSTALLER" -p libcamera_trixie      || true
                bash "$INSTALLER" -p libcamera_apps_trixie || true
                ;;
            bookworm)
                bash "$INSTALLER" -p libcamera_bookworm      || true
                bash "$INSTALLER" -p libcamera_apps_bookworm || true
                ;;
            *)
                echo "    Unknown OS '$OS_CODENAME' — trying generic libcamera packages..."
                bash "$INSTALLER" -p libcamera      || true
                bash "$INSTALLER" -p libcamera_apps || true
                ;;
        esac
        rm -f "$INSTALLER"
    fi
fi
echo ""

# ── Step 4: config.txt ────────────────────────────────────────────────────────

CONFIG=/boot/firmware/config.txt

# The Arducam IMX519 (16MP autofocus) uses the arducam-pivariety overlay,
# NOT the generic imx519 overlay (which targets a different sensor).
OVERLAY_LINE="dtoverlay=arducam-pivariety"
AUTODETECT_LINE="camera_auto_detect=0"

echo "[4/4] Updating $CONFIG..."

# Remove stale imx519 overlay if present from a previous install attempt
if grep -qF "dtoverlay=imx519" "$CONFIG"; then
    sed -i '/dtoverlay=imx519/d' "$CONFIG"
    echo "    Removed stale: dtoverlay=imx519"
fi

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
    echo "# Arducam IMX519 (16MP autofocus) via CSI" >> "$CONFIG"
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
echo "You should see 'arducam-pivariety' listed as cam0."
echo ""
echo "--- Troubleshooting ---"
echo "If the camera is NOT detected after reboot:"
echo "  1. Check the ribbon cable is fully seated at both ends."
echo "     The Zero 2W uses a 22-pin to 15-pin CSI adapter — confirm orientation."
echo "  2. Confirm the overlay in $CONFIG:"
echo "     grep -E 'arducam|camera_auto' $CONFIG"
echo "  3. Check kernel module loaded:"
echo "     lsmod | grep arducam"
echo "  4. Check dmesg for CSI/camera errors:"
echo "     dmesg | grep -i 'imx\|arducam\|csi\|unicam'"
echo "  5. Check DKMS build status:"
echo "     dkms status arducam-pivariety"
echo "  6. Confirm 64-bit OS:"
echo "     uname -m   (should show aarch64)"
