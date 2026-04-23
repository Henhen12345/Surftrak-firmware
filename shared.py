#!/usr/bin/env python3
"""shared.py — constants used across all SurfTrak modules."""

from pathlib import Path

# ── BLE UUIDs ─────────────────────────────────────────────────────────────────

SERVICE_UUID = "12345678-1234-1234-1234-123456789012"
RECORD_UUID  = "12345678-1234-1234-1234-123456789013"
STATUS_UUID  = "12345678-1234-1234-1234-123456789014"
CLIPS_UUID   = "12345678-1234-1234-1234-123456789015"

# ── Paths ─────────────────────────────────────────────────────────────────────

RECORDINGS_DIR = Path.home() / "recordings"
LOGS_DIR       = Path.home() / "logs"
CONFIG_DIR     = Path.home() / ".surftrak"
STATE_FILE     = Path("/tmp/surftrak_state.json")

# ── UWB ───────────────────────────────────────────────────────────────────────

BASELINE_M   = 0.61   # 24 inches between anchor modules — update when hardware changes
UWB_KALMAN_Q = 0.1
UWB_KALMAN_R = 2.0

# ── Motor ─────────────────────────────────────────────────────────────────────

STEPS_PER_DEGREE = 10    # tune on hardware
PAN_LIMIT_DEG    = 120   # soft limit each side from home
MOTOR_ALPHA      = 0.15  # exponential smoothing factor

# ── GPIO (BCM numbering — do not change) ──────────────────────────────────────

GPIO_STEP   = 14   # TMC2209 STEP
GPIO_DIR    = 15   # TMC2209 DIR
GPIO_ENN    = 18   # TMC2209 ENN (active low)
GPIO_UART   = 8    # TMC2209 UART TX

GPIO_CS1    = 7    # DWM3000 left anchor CS
GPIO_CS2    = 25   # DWM3000 right anchor CS
GPIO_IRQ1   = 24   # DWM3000 left anchor IRQ
GPIO_IRQ2   = 23   # DWM3000 right anchor IRQ
GPIO_RESET1 = 17   # DWM3000 left anchor RESET
GPIO_RESET2 = 27   # DWM3000 right anchor RESET

GPIO_LED    = 21   # WS2812B data
GPIO_BTN    = 3    # Power button (active low, pull-up)
