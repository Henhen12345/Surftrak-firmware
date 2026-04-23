#!/usr/bin/env python3
"""beacon.py — BLE beacon scanner for SurfTrak board pairing and presence detection."""

import asyncio
import threading
import time
from typing import Optional

import shared

_BEACON_FILE = shared.CONFIG_DIR / "beacon_id"


class BeaconManager:

    def __init__(self):
        self._lock          = threading.Lock()
        self._beacon_id:    Optional[str] = None
        self._last_seen:    float         = 0.0
        self._running       = False
        self._thread:       Optional[threading.Thread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Load saved beacon address and start continuous scan loop."""
        self._load_beacon_id()
        self._running = True
        self._thread  = threading.Thread(target=self._run_loop, daemon=True,
                                         name="beacon-scan")
        self._thread.start()
        print("[BEACON] Started", flush=True)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        print("[BEACON] Stopped", flush=True)

    def is_connected(self) -> bool:
        """True if beacon advertisement seen within the last 3 seconds."""
        with self._lock:
            return (time.monotonic() - self._last_seen) < 3.0

    def get_beacon_id(self) -> Optional[str]:
        with self._lock:
            return self._beacon_id

    def pair(self, timeout: int = 30) -> Optional[str]:
        """Scan for a device named '*SurfTrak*'. Save and return address, or None."""
        print(f"[BEACON] Pairing scan for {timeout}s...", flush=True)
        try:
            address = asyncio.run(self._pair_async(timeout))
        except Exception as e:
            print(f"[BEACON] Pair async error: {e}", flush=True)
            address = None

        if address:
            try:
                shared.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                _BEACON_FILE.write_text(address)
            except Exception as e:
                print(f"[BEACON] Failed to save beacon ID: {e}", flush=True)
            with self._lock:
                self._beacon_id = address
            print(f"[BEACON] Paired with {address}", flush=True)
        else:
            print("[BEACON] Pairing failed — no SurfTrak beacon found", flush=True)
        return address

    # ── Async helpers ─────────────────────────────────────────────────────────

    async def _pair_async(self, timeout: int) -> Optional[str]:
        from bleak import BleakScanner
        found: list = []

        def _callback(device, _adv):
            if device.name and "SurfTrak" in device.name and not found:
                found.append(device.address)

        async with BleakScanner(detection_callback=_callback):
            deadline = asyncio.get_event_loop().time() + timeout
            while not found and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.5)

        return found[0] if found else None

    async def _scan_loop_async(self) -> None:
        from bleak import BleakScanner

        prev_connected = False

        while self._running:
            try:
                beacon_id = self.get_beacon_id()
                if beacon_id is None:
                    await asyncio.sleep(1.0)
                    continue

                devices = await BleakScanner.discover(timeout=2.0)
                for device in devices:
                    if device.address == beacon_id:
                        with self._lock:
                            self._last_seen = time.monotonic()
                        break

                now_connected = self.is_connected()
                if prev_connected and not now_connected:
                    print("[BEACON] Beacon lost", flush=True)
                prev_connected = now_connected

            except Exception as e:
                print(f"[BEACON] Scan error: {e}", flush=True)
                await asyncio.sleep(1.0)

    # ── Thread entry ──────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._scan_loop_async())
        except Exception as e:
            print(f"[BEACON] Loop error: {e}", flush=True)
        finally:
            loop.close()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_beacon_id(self) -> None:
        try:
            if _BEACON_FILE.exists():
                bid = _BEACON_FILE.read_text().strip()
                if bid:
                    with self._lock:
                        self._beacon_id = bid
                    print(f"[BEACON] Loaded saved beacon: {bid}", flush=True)
        except Exception as e:
            print(f"[BEACON] Failed to load beacon ID: {e}", flush=True)
