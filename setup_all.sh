#!/bin/bash
# setup_all.sh
# Master setup script for SurfTrak firmware.
# Orchestrates hotspot + stream service installation.
#
# PREREQUISITES (must be done manually before running this script):
#   1. Run install_arducam.sh as root
#   2. Reboot the Pi
#   3. Confirm camera is detected: libcamera-hello --list-cameras
#
# Then run this script as root: sudo bash setup_all.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Banner ────────────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║          SurfTrak Firmware Setup             ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Prerequisite check ────────────────────────────────────────────────────────

echo "IMPORTANT: This script assumes you have already:"
echo "  [1] Run install_arducam.sh"
echo "  [2] Rebooted the Pi"
echo "  [3] Verified the camera: libcamera-hello --list-cameras"
echo ""
read -r -p "Have you completed those steps? (yes/no): " CONFIRMED

if [ "$CONFIRMED" != "yes" ]; then
    echo ""
    echo "Please complete the Arducam driver install first:"
    echo "    sudo bash $SCRIPT_DIR/install_arducam.sh"
    echo "    sudo reboot"
    echo ""
    echo "Then re-run this script after reboot."
    exit 1
fi

echo ""

# ── Step 1: Hotspot ───────────────────────────────────────────────────────────

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 1/2: Configuring WiFi hotspot..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bash "$SCRIPT_DIR/setup_hotspot.sh"
echo ""

# ── Step 2: Stream service ────────────────────────────────────────────────────

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 2/2: Installing video stream service..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bash "$SCRIPT_DIR/setup_stream.sh"
echo ""

# ── Final summary ─────────────────────────────────────────────────────────────

echo "╔══════════════════════════════════════════════╗"
echo "║              Setup Complete!                 ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "Reboot the Pi to activate everything:"
echo "    sudo reboot"
echo ""
echo "After reboot:"
echo "  ┌─ iPhone / Mac ──────────────────────────────────────────┐"
echo "  │  Connect to WiFi : SurfTrak                             │"
echo "  │  Password        : surftrak1                            │"
echo "  └─────────────────────────────────────────────────────────┘"
echo ""
echo "  ┌─ Watch live video ──────────────────────────────────────┐"
echo "  │  Open Safari and go to:                                 │"
echo "  │      http://192.168.4.1:8080/stream                     │"
echo "  └─────────────────────────────────────────────────────────┘"
echo ""
echo "  ┌─ SSH access ────────────────────────────────────────────┐"
echo "  │      ssh pi@192.168.4.1                                 │"
echo "  └─────────────────────────────────────────────────────────┘"
echo ""
