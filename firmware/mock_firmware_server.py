from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

MOTOR_STEPS_PER_REV = 200
GEAR_RATIO_TT = 11.335
GEAR_RATIO_VT = 6.2 * 7.5
DEFAULT_FW_VERSION = "1.0.1"


@dataclass
class CommandResult:
    reply: Optional[str]
    delay: float = 0.0


class FirmwareState:
    def __init__(
        self,
        *,
        pan_deg: float = 0.0,
        tilt_deg: float = 0.0,
        microstep: int = 1,
        drivers_enabled: bool = True,
        fw_version: str = DEFAULT_FW_VERSION,
        seconds_per_degree: float = 0.0,
        settle_delay: float = 0.25,
    ) -> None:
        self.pan_deg = pan_deg
        self.tilt_deg = tilt_deg
        self.pan_dir = 1
        self.tilt_dir = 1
        self.microstep = microstep
        self.drivers_enabled = drivers_enabled
        self.fw_version = fw_version
        self.seconds_per_degree = max(0.0, seconds_per_degree)
        self.settle_delay = max(0.0, settle_delay)

    def handle_command(self, raw: str) -> CommandResult:
        command = raw.strip()
        if not command:
            return CommandResult(reply=None)
        key = command[0]
        upper = key.upper()
        if upper == "V":
            return CommandResult(reply=f"VERSION {self.fw_version}")
        if upper == "M":
            parts = command[1:].strip().split()
            if len(parts) != 2:
                return CommandResult(reply="ERR Syntax")
            try:
                target_pan = float(parts[0])
                target_tilt = float(parts[1])
            except ValueError:
                return CommandResult(reply="ERR Syntax")
            delta_pan = target_pan - self.pan_deg
            delta_tilt = target_tilt - self.tilt_deg
            if delta_pan == 0.0 and delta_tilt == 0.0:
                return CommandResult(reply="DONE")
            self.pan_deg = target_pan
            self.tilt_deg = target_tilt
            move_delay = self._move_delay(delta_pan, delta_tilt)
            return CommandResult(reply="DONE", delay=move_delay)
        if upper == "S":
            status = 1 if self.drivers_enabled else 0
            return CommandResult(reply=f"STATUS {self.pan_deg:.3f} {self.tilt_deg:.3f} {status}")
        if key in "12486":
            resolution = 16 if key == "6" else int(key)
            self.microstep = resolution
            return CommandResult(reply=f"OK MICROSTEP {resolution}")
        if key in {"n", "N"}:
            self.pan_deg += self.pan_dir * self._pan_step_degrees()
            return CommandResult(reply="OK ROT STEP")
        if key in {"c", "C"}:
            self.pan_deg += self.pan_dir * 360.0
            return CommandResult(reply="OK ROT REV")
        if key in {"r", "R"}:
            self.pan_dir *= -1
            return CommandResult(reply="OK ROT DIR")
        if key == "x":
            return CommandResult(reply="OK ROT STOP")
        if key in {"w", "W"}:
            self.tilt_deg += self.tilt_dir * self._tilt_step_degrees()
            return CommandResult(reply="OK TILT STEP")
        if key in {"p", "P"}:
            self.tilt_deg += self.tilt_dir * 360.0
            return CommandResult(reply="OK TILT REV")
        if key in {"t", "T"}:
            self.tilt_dir *= -1
            return CommandResult(reply="OK TILT DIR")
        if key == "z":
            return CommandResult(reply="OK TILT STOP")
        if key in "+-":
            return CommandResult(reply="OK SPEED")
        if upper == "X":
            return CommandResult(reply="OK STOP")
        if key in {"d", "D"}:
            self.drivers_enabled = False
            self.pan_deg = 0.0
            self.tilt_deg = 0.0
            return CommandResult(reply="OK DRIVERS OFF")
        if key in {"e", "E"}:
            self.drivers_enabled = True
            self.pan_deg = 0.0
            self.tilt_deg = 0.0
            return CommandResult(reply="OK DRIVERS ON")
        return CommandResult(reply="ERR Unknown")

    def _move_delay(self, delta_pan: float, delta_tilt: float) -> float:
        distance = max(abs(delta_pan), abs(delta_tilt))
        if distance == 0.0:
            return 0.0
        return distance * self.seconds_per_degree + self.settle_delay

    def _pan_step_degrees(self) -> float:
        steps_per_rev = MOTOR_STEPS_PER_REV * GEAR_RATIO_TT * self.microstep
        return 360.0 / steps_per_rev

    def _tilt_step_degrees(self) -> float:
        steps_per_rev = MOTOR_STEPS_PER_REV * GEAR_RATIO_VT * self.microstep
        return 360.0 / steps_per_rev


class MockFirmwareServer:
    def __init__(self, host: str, port: int, state: FirmwareState, logger: Optional[logging.Logger] = None) -> None:
        self.host = host
        self.port = port
        self.state = state
        self.logger = logger or logging.getLogger(__name__)
        self._stop_event = threading.Event()
        self._server_socket: Optional[socket.socket] = None

    def serve_forever(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            self._server_socket = server
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(1)
            self.logger.info("Mock firmware listening on %s:%s", self.host, self.port)
            while not self._stop_event.is_set():
                try:
                    client, address = server.accept()
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise
                with client:
                    self.logger.info("Client connected from %s:%s", *address)
                    self._handle_client(client)
                    self.logger.info("Client disconnected from %s:%s", *address)
            self.logger.info("Mock firmware stopped")

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._server_socket is not None:
            try:
                self._server_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._server_socket.close()

    def _handle_client(self, client: socket.socket) -> None:
        client.settimeout(0.5)
        buffer = b""
        while not self._stop_event.is_set():
            try:
                chunk = client.recv(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                command = line.rstrip(b"\r").decode("utf-8", errors="ignore")
                if not command:
                    continue
                result = self.state.handle_command(command)
                if result.reply is None:
                    continue
                if result.delay:
                    time.sleep(result.delay)
                response = f"{result.reply}\n".encode("utf-8")
                try:
                    client.sendall(response)
                except OSError:
                    return


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mock the CameraCommander firmware over TCP")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=9999, help="Bind port")
    parser.add_argument("--initial-pan", type=float, default=0.0, help="Starting pan angle")
    parser.add_argument("--initial-tilt", type=float, default=0.0, help="Starting tilt angle")
    parser.add_argument("--microstep", type=int, choices=[1, 2, 4, 8, 16], default=1, help="Starting microstep resolution")
    parser.add_argument("--drivers-disabled", action="store_true", help="Start with drivers disabled")
    parser.add_argument("--deg-per-second", type=float, default=60.0, help="Simulated movement speed")
    parser.add_argument("--settle-delay", type=float, default=0.25, help="Extra delay after moves")
    parser.add_argument("--fw-version", default=DEFAULT_FW_VERSION, help="Firmware version string")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    logger = logging.getLogger("mock_firmware")
    seconds_per_degree = 0.0 if args.deg_per_second <= 0 else 1.0 / args.deg_per_second
    state = FirmwareState(
        pan_deg=args.initial_pan,
        tilt_deg=args.initial_tilt,
        microstep=args.microstep,
        drivers_enabled=not args.drivers_disabled,
        fw_version=args.fw_version,
        seconds_per_degree=seconds_per_degree,
        settle_delay=max(0.0, args.settle_delay),
    )
    server = MockFirmwareServer(args.host, args.port, state, logger)

    def _handle_signal(signum, _frame):
        logger.info("Signal %s received, shutting down", signum)
        server.shutdown()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt, shutting down")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
