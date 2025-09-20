"""tripod_controller.py
High-level controller for dual‑axis tripod stepper system communicating over a serial
link. Requires ``pyserial``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import RLock
from typing import Optional, Tuple

import serial
from serial import SerialException

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class TripodController:
    """Controller for a dual‑axis (pan/tilt) tripod head.

    Parameters
    ----------
    config
        Dictionary with mandatory keys::

            {
                "port": "/dev/ttyUSB0",
                "baudrate": 115200,
                "timeout": 1.0,            # read timeout seconds
                "write_timeout": 1.0,      # write timeout seconds
                "reconnect_interval": 2.0, # seconds between reconnect attempts
                "max_retries": 5           # consecutive failures before giving up
            }

        Any additional parameter accepted by :class:`serial.Serial` can be
        supplied – they will be passed through verbatim.

    Notes
    -----
    The class maintains the current absolute pan and tilt positions in degrees
    by accumulating relative moves in software. Because the device exposes only
    relative motion, this state is purely software‑based. Call
    :py:meth:`reset_position` after homing to keep values in sync.

    All public methods are thread‑safe.
    """

    _ACK_OK = b"OK"
    _ACK_DONE = b"DONE"
    _ACK_ERR = b"ERR"

    # ------------------------------------------------------------------ #
    # Life‑cycle
    # ------------------------------------------------------------------ #
    def __init__(self, config: dict) -> None:
        self._cfg = config.copy()
        self._serial: Optional[serial.Serial] = None
        self._lock = RLock()

        # init state ----------------------------------------------------#
        # absolute position in degrees
        self._pan_deg: float = 0.0
        self._tilt_deg: float = 0.0
        self._drivers_enabled: bool = False
        self._microstep: int = self._cfg.get("microstep", 1)

        # connection ------------------------------------------------------
        self._reconnect_interval = float(config.get("reconnect_interval", 2.0))
        self._max_retries = int(config.get("max_retries", 5))
        self._open_serial()

        # set state base  on config -
        self._cfg["timeout"] = float(self._cfg.get("timeout", 1.0))
        self._cfg["write_timeout"] = float(self._cfg.get("write_timeout", 1.0))
        self.enable_drivers(self._drivers_enabled)
        self.set_microstep(self._microstep)

    # ------------------------------------------------------------------ #
    # Serial layer helpers
    # ------------------------------------------------------------------ #
    def _open_serial(self) -> None:
        """(Re)open the serial port according to the stored configuration."""
        if self._serial and self._serial.is_open:
            return

        failures = 0
        while failures <= self._max_retries:
            try:
                logger.info("Opening serial port %s", self._cfg["serial"]["port"])
                self._serial = serial.Serial(**self._cfg["serial"], exclusive=True)
                logger.info("Serial port opened successfully")
                return
            except SerialException as exc:
                failures += 1
                logger.warning("Serial open failed (%s); retry %s/%s in %.1fs",
                               exc, failures, self._max_retries,
                               self._reconnect_interval)
                time.sleep(self._reconnect_interval)

        raise ConnectionError("Could not open serial port after max retries")

    def close(self) -> None:
        """Close the underlying serial connection."""
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
                logger.info("Serial port closed")

    def _recover_if_needed(self) -> None:
        """Ensure the port is open; attempt reconnection on failure."""
        if self._serial is None or not self._serial.is_open:
            self._open_serial()

    def _send(self, cmd: str, expect_ok: bool = True) -> str:
        """Send *cmd* and return the raw response string (without EOL)."""
        with self._lock:
            self._recover_if_needed()
            assert self._serial is not None  # mypy safety
            try:
                logger.debug("-> %s", cmd)
                self._serial.write(f"{cmd}\n".encode())
                self._serial.flush()
                resp = self._serial.readline().decode().strip()
                logger.debug("<- %s", resp)
            except SerialException as exc:
                logger.error("SerialException during command %s: %s", cmd, exc)
                self.close()
                raise

        if expect_ok and not (resp.startswith(self._ACK_OK.decode()) or resp.startswith(self._ACK_DONE.decode())):
            raise RuntimeError(f"Unexpected response to '{cmd}': {resp}")
        return resp

    # ------------------------------------------------------------------ #
    # Public API – information
    # ------------------------------------------------------------------ #
    def firmware_version(self) -> str:
        """Return the firmware version string."""
        return self._send("V", expect_ok=False)

    def query_busy(self) -> bool:
        """Return *True* while axes are moving."""
        resp = self._send("Q", expect_ok=False)
        if resp == "BUSY":
            return True
        if resp == "DONE":
            return False
        raise RuntimeError(f"Unexpected Q response: {resp}")

    # ------------------------------------------------------------------ #
    # Public API – motion
    # ------------------------------------------------------------------ #
    def move(self, pan_deg: float = 0.0, tilt_deg: float = 0.0) -> None:
        """Move axes by the specified *relative* degrees *non‑blocking*."""
        if pan_deg == 0.0 and tilt_deg == 0.0:
            return
        with self._lock:
            self._send(f"M {pan_deg} {tilt_deg}")
            self._pan_deg += pan_deg
            self._tilt_deg += tilt_deg

    def move_to(self, pan_deg: float | None = None, tilt_deg: float | None = None) -> None:
        """Move axes to the given *absolute* pan/tilt angles.

        Parameters
        ----------
        pan_deg, tilt_deg
            Absolute angles in degrees. ``None`` leaves an axis unchanged.
        """
        with self._lock:
            delta_pan = 0.0 if pan_deg is None else pan_deg - self._pan_deg
            delta_tilt = 0.0 if tilt_deg is None else tilt_deg - self._tilt_deg
            if delta_pan == 0.0 and delta_tilt == 0.0:
                return
            self._send(f"M {delta_pan} {delta_tilt}")
            self._pan_deg += delta_pan
            self._tilt_deg += delta_tilt

    def move_blocking(self, pan_deg: float = 0.0, tilt_deg: float = 0.0,
                      poll_interval: float = 0.05, timeout: float | None = None) -> None:
        """Move axes and *block* until the device reports that movement is complete.

        Parameters
        ----------
        pan_deg, tilt_deg
            Relative angles in degrees (same semantics as :py:meth:`move`).
        poll_interval
            Seconds between BUSY polls (default 50 ms).
        timeout
            Maximum seconds to wait. ``None`` (default) waits indefinitely.

        Raises
        ------
        TimeoutError
            If *timeout* elapses before the controller returns ``DONE``.
        """
        start = time.monotonic()
        self.move(pan_deg, tilt_deg)
        while self.query_busy():
            if timeout is not None and (time.monotonic() - start) > timeout:
                raise TimeoutError("move_blocking timed out")
            time.sleep(poll_interval)

    def move_to_blocking(self, pan_deg: float | None = None, tilt_deg: float | None = None,
                         poll_interval: float = 0.05,
                         timeout: float | None = None) -> None:
        """Absolute :py:meth:`move_to` variant that blocks until movement completes."""
        start = time.monotonic()
        self.move_to(pan_deg, tilt_deg)
        while self.query_busy():
            if timeout is not None and (time.monotonic() - start) > timeout:
                raise TimeoutError("move_to_blocking timed out")
            time.sleep(poll_interval)

    def stop(self) -> None:
        """Emergency stop – halt both axes immediately."""
        self._send("X")
        # state remains consistent – we stopped mid‑move

    # ------------------------------------------------------------------ #
    # Public API – microstep & helpers
    # ------------------------------------------------------------------ #
    def set_microstep(self, microstep: int) -> None:
        """Set driver microstepping (1, 2, 4, 8, 16)."""
        if microstep not in {1, 2, 4, 8, 16}:
            raise ValueError("microstep must be one of 1,2,4,8,16")
        cmd_lookup = {1: "1", 2: "2", 4: "4", 8: "8", 16: "6"}
        self._send(cmd_lookup[microstep])
        self._microstep = microstep

    # --- pan helpers -----------------------------------------------------
    def pan_step(self) -> None:  # one micro‑step
        self._send("n")

    def pan_revolution(self) -> None:
        self._send("c")

    def toggle_pan_dir(self) -> None:
        self._send("r")

    def stop_pan(self) -> None:
        self._send("x")

    # --- tilt helpers ----------------------------------------------------
    def tilt_step(self) -> None:  # one micro‑step
        self._send("w")

    def tilt_revolution(self) -> None:
        self._send("p")

    def toggle_tilt_dir(self) -> None:
        self._send("t")

    def stop_tilt(self) -> None:
        self._send("z")

    # --- speed -----------------------------------------------------------
    def speed_up(self) -> None:
        self._send("+")

    def speed_down(self) -> None:
        self._send("-")

    # --- drivers ---------------------------------------------------------
    def enable_drivers(self, enable: bool = True) -> None:
        self._send("e" if enable else "d")
        self._drivers_enabled = enable

    # ------------------------------------------------------------------ #
    # State & utility
    # ------------------------------------------------------------------ #
    @property
    def position(self) -> Tuple[float, float]:
        """Current absolute (pan, tilt) in degrees."""
        return self._pan_deg, self._tilt_deg

    def reset_position(self) -> None:
        """Reset internal position counters to zero (e.g. after homing)."""
        self._pan_deg = 0.0
        self._tilt_deg = 0.0

    # ------------------------------------------------------------------ #
    # Context‑manager helpers
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "TripodController":
        self._open_serial()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> Optional[bool]:
        self.close()
        # Propagate exception (return False)
        return False


# ---------------------------------------------------------------------- #
# Convenience helpers
# ---------------------------------------------------------------------- #

def configure_logging(level: int = logging.INFO,
                      logfile: Optional[Path | str] = None) -> None:
    """Quick root logger setup (console or file)."""
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level,
                        filename=str(logfile) if logfile else None,
                        format=fmt)
