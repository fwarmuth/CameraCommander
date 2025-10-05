"""Serial tripod controller adapter for the Gradio application."""

from __future__ import annotations

import logging
import math
import time
from threading import RLock
from typing import Dict, Optional, Tuple

import serial
from serial import SerialException

__all__ = ["TripodAdapter", "TripodAdapterError"]

logger = logging.getLogger(__name__)


class TripodAdapterError(RuntimeError):
    """Structured exception surfaced to the Gradio layer."""

    def __init__(self, message: str, *, code: str = "tripod_error", details: Optional[Dict[str, object]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class TripodAdapter:
    """High level controller for the dual-axis tripod firmware."""

    _ACK_OK = b"OK"
    _ACK_DONE = b"DONE"
    _ACK_ERR = b"ERR"

    def __init__(self, config: Dict[str, object]) -> None:
        self._cfg = config.copy()
        serial_cfg = self._cfg.get("serial")
        if not isinstance(serial_cfg, dict):
            raise TripodAdapterError(
                "Tripod configuration must define a 'serial' mapping.",
                code="tripod_config_invalid",
            )
        self._serial_cfg = serial_cfg.copy()
        self._serial_cfg["timeout"] = float(self._serial_cfg.get("timeout", 1.0))
        self._serial_cfg["write_timeout"] = float(self._serial_cfg.get("write_timeout", 1.0))
        self._serial: Optional[serial.Serial] = None
        self._lock = RLock()

        self._pan_deg = float(self._cfg.get("pan_degrees", 0.0))
        self._tilt_deg = float(self._cfg.get("tilt_degrees", 0.0))
        self._drivers_enabled = bool(self._cfg.get("drivers_enabled", True))
        self._microstep = int(self._cfg.get("microstep", 1))

        self._reconnect_interval = float(self._cfg.get("reconnect_interval", 2.0))
        self._max_retries = int(self._cfg.get("max_retries", 5))

        self._open_serial()
        self.enable_drivers(self._drivers_enabled)
        self.set_microstep(self._microstep)

    # ------------------------------------------------------------------
    # Serial helpers
    # ------------------------------------------------------------------
    def _open_serial(self) -> None:
        if self._serial and self._serial.is_open:
            return
        failures = 0
        while failures <= self._max_retries:
            try:
                logger.info("Opening serial port %s", self._serial_cfg.get("port"))
                self._serial = serial.Serial(**self._serial_cfg, exclusive=True)
                logger.info("Serial port opened successfully")
                return
            except SerialException as exc:
                failures += 1
                logger.warning(
                    "Serial open failed (%s); retry %s/%s in %.1fs",
                    exc,
                    failures,
                    self._max_retries,
                    self._reconnect_interval,
                )
                time.sleep(self._reconnect_interval)
        raise TripodAdapterError("Could not open serial port after max retries", code="tripod_connection_failed")

    def close(self) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
                logger.info("Serial port closed")

    def _recover_if_needed(self) -> None:
        if self._serial is None or not self._serial.is_open:
            self._open_serial()

    def _send(self, cmd: str, expect_ok: bool = True) -> str:
        with self._lock:
            self._recover_if_needed()
            assert self._serial is not None
            try:
                logger.debug("-> %s", cmd)
                self._serial.write(f"{cmd}\n".encode())
                self._serial.flush()
                resp = self._serial.readline().decode().strip()
                logger.debug("<- %s", resp)
            except SerialException as exc:
                self.close()
                raise TripodAdapterError(
                    f"SerialException during command {cmd}: {exc}",
                    code="tripod_serial_error",
                ) from exc
        if expect_ok:
            normalized = cmd.strip()
            prefix = normalized[:1].upper() if normalized else ""
            ack_ok = resp.startswith(self._ACK_OK.decode())
            if not ack_ok and prefix == "M":
                ack_ok = resp.startswith(self._ACK_DONE.decode())
            if not ack_ok:
                raise TripodAdapterError(
                    f"Unexpected response to '{cmd}': {resp}",
                    code="tripod_protocol_error",
                )
        return resp

    # ------------------------------------------------------------------
    # Command helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_angle(angle: float) -> str:
        return f"{angle:.6f}"

    def _send_absolute_move(self, pan_deg: float, tilt_deg: float) -> None:
        if not self._drivers_enabled:
            raise TripodAdapterError("Cannot move: drivers are disabled", code="tripod_drivers_disabled")
        self._send(f"M {self._format_angle(pan_deg)} {self._format_angle(tilt_deg)}")

    # ------------------------------------------------------------------
    # Public API – information
    # ------------------------------------------------------------------
    def status(self) -> Tuple[float, float, bool]:
        with self._lock:
            resp = self._send("S", expect_ok=False)
            if not resp.startswith("STATUS"):
                raise TripodAdapterError(
                    f"Unexpected status response: {resp}",
                    code="tripod_protocol_error",
                )
            parts = resp.split()
            if len(parts) < 4:
                raise TripodAdapterError(
                    f"Incomplete status response: {resp}",
                    code="tripod_protocol_error",
                )
            pan = float(parts[1])
            tilt = float(parts[2])
            drivers = parts[3] not in {"0", "OFF", "DISABLED"}
            self._pan_deg = pan
            self._tilt_deg = tilt
            self._drivers_enabled = drivers
            return pan, tilt, drivers

    # ------------------------------------------------------------------
    # Public API – motion
    # ------------------------------------------------------------------
    def move(self, pan_deg: float = 0.0, tilt_deg: float = 0.0) -> None:
        if pan_deg == 0.0 and tilt_deg == 0.0:
            return
        with self._lock:
            target_pan = self._pan_deg + pan_deg
            target_tilt = self._tilt_deg + tilt_deg
            self._send_absolute_move(target_pan, target_tilt)
            self._pan_deg = target_pan
            self._tilt_deg = target_tilt

    def move_to(self, pan_deg: Optional[float] = None, tilt_deg: Optional[float] = None) -> None:
        with self._lock:
            target_pan = self._pan_deg if pan_deg is None else pan_deg
            target_tilt = self._tilt_deg if tilt_deg is None else tilt_deg
            if math.isclose(target_pan, self._pan_deg, abs_tol=1e-7) and math.isclose(
                target_tilt, self._tilt_deg, abs_tol=1e-7
            ):
                return
            self._send_absolute_move(target_pan, target_tilt)
            self._pan_deg = target_pan
            self._tilt_deg = target_tilt

    def move_blocking(
        self,
        pan_deg: float = 0.0,
        tilt_deg: float = 0.0,
        *,
        timeout: Optional[float] = None,
    ) -> None:
        start = time.monotonic()
        self.move(pan_deg, tilt_deg)
        if timeout is not None and (time.monotonic() - start) > timeout:
            raise TripodAdapterError("move_blocking timed out", code="tripod_timeout")

    def move_to_blocking(
        self,
        pan_deg: Optional[float] = None,
        tilt_deg: Optional[float] = None,
        *,
        timeout: Optional[float] = None,
    ) -> None:
        start = time.monotonic()
        self.move_to(pan_deg, tilt_deg)
        if timeout is not None and (time.monotonic() - start) > timeout:
            raise TripodAdapterError("move_to_blocking timed out", code="tripod_timeout")

    def stop(self) -> None:
        self._send("X")

    # ------------------------------------------------------------------
    # Microstep and driver helpers
    # ------------------------------------------------------------------
    def set_microstep(self, microstep: int) -> None:
        if microstep not in {1, 2, 4, 8, 16}:
            raise TripodAdapterError("microstep must be one of 1,2,4,8,16", code="invalid_arguments")
        cmd_lookup = {1: "1", 2: "2", 4: "4", 8: "8", 16: "6"}
        self._send(cmd_lookup[microstep])
        self._microstep = microstep

    def enable_drivers(self, enable: bool = True) -> None:
        self._send("e" if enable else "d")
        self._drivers_enabled = enable
        self._pan_deg = 0.0
        self._tilt_deg = 0.0

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    @property
    def position(self) -> Tuple[float, float]:
        return self._pan_deg, self._tilt_deg

    def reset_position(self) -> None:
        self._pan_deg = 0.0
        self._tilt_deg = 0.0

    # ------------------------------------------------------------------
    # Context manager helpers
    # ------------------------------------------------------------------
    def __enter__(self) -> "TripodAdapter":
        self._open_serial()
        return self

    def __exit__(self, exc_type, exc, tb) -> Optional[bool]:
        self.close()
        return False
