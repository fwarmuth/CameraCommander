# tripod.py  (rewired to use test.py-style robust serial transactions)

import time
import re
from typing import Iterable, Optional

import serial
from serial.tools import list_ports
from . import logger

# ---------- Protocol validator (copied/adapted from test.py) ----------
LINE_OK = re.compile(
    rb'^(VERSION\b.*|OK\b.*|DONE\b.*|BUSY\b.*|ERR\b.*)$',
    re.IGNORECASE,
)

NEWLINE = b"\n"


class Tripod:
    """
    Interface to the motorized tripod for controlling pan/tilt motions during timelapse.
    Handles serial communication with the ESP8266 and movement calculations.
    """

    def __init__(self, pan_start, pan_end, tilt_start, tilt_end, frames,
                 port=None, movement_mode="incremental", settle_time=1.0,
                 microstep_resolution='1/16', command_timeout=10.0):
        """
        :param pan_start: Start angle for pan axis (degrees).
        :param pan_end: End angle for pan axis (degrees).
        :param tilt_start: Start angle for tilt axis (degrees).
        :param tilt_end: End angle for tilt axis (degrees).
        :param frames: Total number of frames in the timelapse.
        :param port: Serial device (e.g. '/dev/ttyUSB0', 'COM3'). Use None or "auto" to auto-detect.
        :param movement_mode: 'incremental' or 'continuous'.
        :param settle_time: Extra wait time (seconds) after each move to let vibrations settle.
        :param microstep_resolution: ['full','1/2','1/4','1/8','1/16'].
        :param command_timeout: Seconds to wait for an ACK/DONE (also used as overall poll timeout).
        """
        self.pan_start = pan_start
        self.pan_end = pan_end
        self.tilt_start = tilt_start
        self.tilt_end = tilt_end
        self.frames = frames
        self.movement_mode = movement_mode.lower()
        self.settle_time = settle_time
        self.microstep_resolution = microstep_resolution
        self.command_timeout = command_timeout

        # Per-frame step increments for incremental mode
        if frames > 1:
            self.pan_step = (pan_end - pan_start) / float(frames - 1)
            self.tilt_step = (tilt_end - tilt_start) / float(frames - 1)
        else:
            self.pan_step = 0.0
            self.tilt_step = 0.0

        # Track current assumed position (deg)
        self.current_pan = pan_start
        self.current_tilt = tilt_start

        self.port_name = port or "auto"
        self.serial: Optional[serial.Serial] = None

    # ───────────────────────────────────────────────
    # Public API
    # ───────────────────────────────────────────────
    def connect(self):
        """Open the serial connection to the tripod (auto-detect port if needed)."""
        port = self.port_name
        if port == "auto" or port is None:
            port = self._auto_detect_port()
            if not port:
                raise RuntimeError("Unable to auto-detect the tripod serial port.")

        # Use short per-read timeout; our own logic handles overall timeouts
        self.serial = serial.Serial(
            port=port,
            baudrate=9600,
            timeout=0.5,
            write_timeout=self.command_timeout,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            exclusive=True,
        )

        # Mitigate auto-reset on some ESPs
        try:
            self.serial.dtr = False
            self.serial.rts = False
        except Exception:
            pass

        # Give the MCU a moment and clear any banner/noise
        time.sleep(0.1)
        self._drain_input()

        logger.info(f"Connected to serial port: {port}")

        # Ensure we are at the configured start position
        self._move_to_start_position()

    def start_continuous_move(self):
        """For 'continuous' mode. Currently a stub – extend if you actually stream movement."""
        if self.movement_mode != "continuous":
            raise RuntimeError("Continuous mode requested but Tripod not configured for it.")
        logger.warning("Continuous mode not implemented. Implement streaming/polling here.")

    def move_incremental_step(self, frame_idx: int):
        """
        Move to the position of frame_idx (0-based) in incremental mode.
        """
        if self.movement_mode != "incremental":
            raise RuntimeError("move_incremental_step called but movement_mode is not 'incremental'.")

        target_pan = self.pan_start + frame_idx * self.pan_step
        target_tilt = self.tilt_start + frame_idx * self.tilt_step

        delta_pan = target_pan - self.current_pan
        delta_tilt = target_tilt - self.current_tilt

        if abs(delta_pan) < 1e-6 and abs(delta_tilt) < 1e-6:
            logger.debug(f"Frame {frame_idx}: no movement needed.")
            return

        # Adjust microstepping if needed
        self._set_microstep_resolution()

        cmd = f"M {delta_pan:.3f} {delta_tilt:.3f}"
        logger.info(f"Frame {frame_idx}: move Δpan={delta_pan:.3f}°, Δtilt={delta_tilt:.3f}°")
        self._send_command(cmd, expect=["OK", "ERR"])

        self._poll_done()  # wait for DONE
        self.current_pan = target_pan
        self.current_tilt = target_tilt

        if self.settle_time > 0:
            time.sleep(self.settle_time)

    def disconnect(self):
        """Close the serial connection cleanly."""
        if self.serial and self.serial.is_open:
            try:
                self.serial.close()
            except Exception:
                pass
        self.serial = None
        logger.info("Serial connection closed.")

    # Context manager helpers
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False  # propagate exceptions

    # ───────────────────────────────────────────────
    # Internal helpers
    # ───────────────────────────────────────────────
    def _auto_detect_port(self):
        """Return the first serial port that looks like an ESP/Arduino, else None."""
        candidates = []
        for p in list_ports.comports():
            desc = (p.description or "").lower()
            if "cp210" in desc or "ch340" in desc or "arduino" in desc or "usb serial" in desc:
                candidates.append(p.device)
        if not candidates:
            # fallback to first available
            ports = [p.device for p in list_ports.comports()]
            return ports[0] if ports else None
        return candidates[0]

    def _set_microstep_resolution(self):
        """Send microstep resolution command if needed. ('1','2','4','8','6' for 16)"""
        res_map = {
            'full': '1',
            '1/2':  '2',
            '1/4':  '4',
            '1/8':  '8',
            '1/16': '6'
        }
        code = res_map.get(self.microstep_resolution.lower())
        if not code:
            logger.warning(f"Unknown microstep_resolution '{self.microstep_resolution}', skipping.")
            return
        logger.debug(f"Setting microstep resolution: {self.microstep_resolution}")
        self._send_command(code, expect=["OK", "ERR"])

    # ---------- Robust I/O helpers (ported) ----------
    def _drain_input(self, window: float = 0.05) -> None:
        """Flush whatever is sitting in RX to start clean."""
        if not self.serial:
            return
        self.serial.reset_input_buffer()
        end = time.monotonic() + window
        while time.monotonic() < end:
            if self.serial.in_waiting:
                self.serial.read(self.serial.in_waiting)
            else:
                time.sleep(0.005)

    def _transact(
        self,
        cmd: str | bytes,
        expect: Optional[Iterable[str]] = None,
        attempts: int = 3,
        overall_timeout: float | None = None,
    ) -> str:
        """
        Send one command, return one validated reply line (stripped).
        Mirrors test.py/transact().
        """
        if not self.serial or not self.serial.is_open:
            raise RuntimeError("Serial port not open. Call connect() first.")

        if isinstance(cmd, str):
            payload = cmd.encode()
        else:
            payload = cmd
        if not payload.endswith(NEWLINE):
            payload += NEWLINE

        overall_timeout = overall_timeout or self.command_timeout

        for _ in range(attempts):
            self._drain_input()
            self.serial.write(payload)
            self.serial.flush()

            deadline = time.monotonic() + overall_timeout
            while time.monotonic() < deadline:
                raw = self.serial.readline()  # uses per-read timeout
                if not raw:
                    continue
                line = raw.rstrip(b"\r\n")
                if not line:
                    continue
                if LINE_OK.match(line):
                    reply = line.decode(errors="replace")
                    if expect and not any(reply.upper().startswith(p.upper()) for p in expect):
                        raise RuntimeError(f"Unexpected reply: {reply}")
                    return reply
            # retry loop
        raise TimeoutError(f"No valid reply after {attempts} attempts for {cmd!r}")

    def _poll_done(self, interval: float = 0.1, timeout: Optional[float] = None) -> None:
        """Poll 'Q' until DONE or timeout (like test.py/poll_done)."""
        timeout = timeout or self.command_timeout
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            rep = self._transact("Q", expect=["BUSY", "DONE"], attempts=1, overall_timeout=timeout)
            if rep.upper().startswith("DONE"):
                return
            time.sleep(interval)
        raise TimeoutError("Motion did not finish in time.")

    # ---------- Command wrappers ----------
    def _send_command(self, command: str, expect: Optional[Iterable[str]] = None, wait_for_ack: bool = True):
        """
        Send a single-line command and optionally wait for an ACK ("OK...", "DONE", "BUSY", "ERR...", etc.)
        """
        if not wait_for_ack:
            # Fire-and-forget (rare for this firmware, but supported)
            if not self.serial or not self.serial.is_open:
                raise RuntimeError("Serial port not open. Call connect() first.")
            payload = (command.strip() + "\n").encode("utf-8")
            self._drain_input()
            self.serial.write(payload)
            self.serial.flush()
            return True

        # Auto-guess expect prefixes if not provided
        if expect is None:
            c0 = command[:1].upper()
            if c0 == "V":
                expect = ["VERSION"]
            elif c0 == "M":
                expect = ["OK", "ERR"]
            elif c0 == "Q":
                expect = ["BUSY", "DONE"]
            else:
                expect = ["OK", "ERR", "DONE", "BUSY", "VERSION"]

        reply = self._transact(
            command,
            expect=expect,
            attempts=3,
            overall_timeout=self.command_timeout
        )
        logger.debug(f"Reply: {reply}")
        if reply.upper().startswith("ERR"):
            raise RuntimeError(f"Firmware reported error for '{command}': {reply}")
        return True

    def _wait_for_movement_to_finish(self):
        """Legacy wrapper kept for compatibility with existing code paths."""
        logger.debug("Waiting for movement to complete...")
        self._poll_done()
        logger.debug("Movement complete.")

    def _move_to_start_position(self):
        """Ensure we are at start position before shooting."""
        delta_pan = self.pan_start - self.current_pan
        delta_tilt = self.tilt_start - self.current_tilt
        if abs(delta_pan) < 1e-6 and abs(delta_tilt) < 1e-6:
            return

        self._set_microstep_resolution()

        cmd = f"M {delta_pan:.3f} {delta_tilt:.3f}"
        logger.info(f"Moving to start position: pan={self.pan_start}°, tilt={self.tilt_start}°")
        self._send_command(cmd, expect=["OK", "ERR"])
        self._poll_done()

        self.current_pan = self.pan_start
        self.current_tilt = self.tilt_start

        if self.settle_time > 0:
            time.sleep(self.settle_time)
        logger.info("Reached start position.")
