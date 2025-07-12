import time
import serial
from serial.tools import list_ports

class Tripod:
    """
    Interface to the motorized tripod for controlling pan/tilt motions during timelapse.
    Handles serial communication with the ESP8266 and movement calculations.
    """
    def __init__(self, pan_start, pan_end, tilt_start, tilt_end, frames,
                 port=None, movement_mode="incremental", settle_time=1.0):
        """
        Initialize the Tripod controller.
        :param pan_start: Start angle for pan axis (degrees).
        :param pan_end: End angle for pan axis (degrees).
        :param tilt_start: Start angle for tilt axis (degrees).
        :param tilt_end: End angle for tilt axis (degrees).
        :param frames: Total number of frames in the timelapse.
        :param port: Serial port device (e.g. '/dev/ttyUSB0' or 'COM3'). If None or 'auto', auto-detect will be used.
        :param movement_mode: 'incremental' or 'continuous'.
        :param settle_time: Extra wait time (seconds) after each move to let vibrations settle.
        """
        self.pan_start = pan_start
        self.pan_end = pan_end
        self.tilt_start = tilt_start
        self.tilt_end = tilt_end
        self.frames = frames
        self.movement_mode = movement_mode.lower()
        self.settle_time = settle_time
        # Calculate per-frame step increments for incremental mode
        if frames > 1:
            self.pan_step = (pan_end - pan_start) / float(frames - 1)
            self.tilt_step = (tilt_end - tilt_start) / float(frames - 1)
        else:
            # Only one frame, no movement needed
            self.pan_step = 0.0
            self.tilt_step = 0.0
        # Current assumed position (in degrees) relative to start
        # We assume the tripod starts at pan_start/tilt_start after initialization.
        self.current_pan = pan_start
        self.current_tilt = tilt_start
        # Determine the serial port to use
        self.port_name = port or "auto"
        self.serial = None

    def connect(self):
        """Open the serial connection to the tripod (auto-detect port if needed)."""
        port = self.port_name
        if port == "auto" or port is None:
            port = self._auto_detect_port()
            if not port:
                raise RuntimeError("Unable to auto-detect the tripod serial port.")
        # Open serial port at 9600 baud
        self.serial = serial.Serial(port, 9600, timeout=2.0)
        # Flush any initial data (e.g., startup messages) from buffer
        time.sleep(2)  # give some time for serial to initialize
        self.serial.reset_input_buffer()
        print(f"[Tripod] Connected to serial port: {port}")

        # Optionally, ensure drivers are enabled (if firmware requires). For example:
        # self.serial.write(b"e\n")  # 'e' command from firmware to enable motors.
        # We can skip this if motors are enabled by default on startup.

        # If using incremental mode, we will move to the start position now (if not already at start).
        # In continuous mode, we also start at the given start angles (initial position),
        # then later command the full move.
        self._move_to_start_position()

    def _auto_detect_port(self):
        """Attempt to find the tripod's serial port by scanning available ports."""
        ports = list_ports.comports()
        for p in ports:
            # Heuristic: look for typical USB-UART identifiers
            desc = p.description.lower()
            if "cp210" in desc or "ch340" in desc or "usb" in desc:
                # Likely the ESP8266 is on this port
                return p.device
        # If not found via description, but there's exactly one port, use it
        if len(ports) == 1:
            return ports[0].device
        return None

    def _send_command(self, command, wait_for_ack=True):
        """
        Send a command string to the tripod and optionally wait for the "DONE" acknowledgment.
        Adds newline termination automatically. Retries on timeout.
        """
        if not self.serial or not self.serial.is_open:
            raise RuntimeError("Serial port not open. Call connect() first.")
        cmd_line = command.strip() + "\n"
        encoded = cmd_line.encode('ascii')
        # We will attempt a few retries if ack not received
        max_retries = 3
        for attempt in range(1, max_retries+1):
            # Send the command
            self.serial.write(encoded)
            self.serial.flush()
            if not wait_for_ack:
                return True  # command sent, no ack required (e.g., continuous mode initiation)
            # Wait for response until timeout
            start_time = time.time()
            response = b""
            while time.time() - start_time < 10.0:  # up to 10 seconds per move (adjustable)
                if self.serial.in_waiting:
                    line = self.serial.readline()
                    if line:
                        response = line.strip()
                        break
                time.sleep(0.05)  # small delay to avoid busy-wait
            if response:
                try:
                    resp_text = response.decode('ascii')
                except UnicodeDecodeError:
                    resp_text = ""
                resp_text = resp_text.strip()
                # If the response starts with "DONE", we consider it a successful ack.
                # (It might be "DONE 45.0 10.0" including positions, which is fine.)
                if resp_text.upper().startswith("DONE"):
                    # Acknowledge received
                    return True
                elif resp_text.upper().startswith("ERR"):
                    # Received an error response from tripod
                    raise RuntimeError(f"Tripod error response: {resp_text}")
                # If some other unexpected text, ignore and continue waiting (it might be initial banner or info).
            # If no response or no "DONE" within timeout:
            print(f"[Tripod] No ack for command '{command}' (attempt {attempt}).")
            if attempt < max_retries:
                print("[Tripod] Retrying command...")
                # Perhaps send a stop to clear any motion before retrying
                try:
                    self.serial.write(b"X\n")  # stop any ongoing motion
                except Exception:
                    pass
                time.sleep(1)  # small pause before retry
                continue
            else:
                # All retries failed
                raise RuntimeError(f"No acknowledgment from tripod for command '{command}'.")
        return False  # should not reach here normally

    def _move_to_start_position(self):
        """Move the tripod to the configured start angles (if not already there)."""
        # Assuming the tripod's current position at startup corresponds to 0 reference for pos tracking.
        # We need to move from "current" (which at connect we consider 0 offset) to the desired start angles.
        # That effectively means moving by (pan_start, tilt_start) from current orientation.
        # If start angles are 0,0, this does nothing.
        delta_pan = self.pan_start  # since current_pan is 0 relative at connect time
        delta_tilt = self.tilt_start  # current_tilt 0
        if abs(delta_pan) < 1e-6 and abs(delta_tilt) < 1e-6:
            return  # already at start (no move needed)
        # Send move command to reach start position
        cmd = f"M {delta_pan:.3f} {delta_tilt:.3f}"
        print(f"[Tripod] Moving to start position: pan={self.pan_start}°, tilt={self.tilt_start}°")
        self._send_command(cmd, wait_for_ack=True)
        # Update current known position
        self.current_pan = self.pan_start
        self.current_tilt = self.tilt_start
        # Allow a brief moment for settling (if needed)
        if self.settle_time > 0:
            time.sleep(self.settle_time)
        print("[Tripod] Reached start position.")

    def start_continuous_move(self):
        """
        Begin continuous motion from start to end angles. Computes total deltas and issues one move command.
        Does not wait for completion (frames will be captured during motion).
        """
        if self.movement_mode != "continuous":
            return False
        # Calculate the full range to move
        total_pan = self.pan_end - self.pan_start
        total_tilt = self.tilt_end - self.tilt_start
        # Issue one move command for the full range. 
        # The tripod will execute this over time. We do not wait for "DONE" here.
        cmd = f"M {total_pan:.3f} {total_tilt:.3f}"
        print(f"[Tripod] Starting continuous move: pan_delta={total_pan}°, tilt_delta={total_tilt}°")
        success = self._send_command(cmd, wait_for_ack=False)
        if not success:
            raise RuntimeError("Failed to start continuous movement on tripod.")
        # (We assume the motion will complete around the end of timelapse; we can check for DONE after frames.)
        return True

    def move_incremental_step(self):
        """
        In incremental mode, move to the next step position (adds one step of pan_step/tilt_step).
        Waits for completion acknowledgment.
        """
        if self.movement_mode != "incremental":
            return False
        # Compute the next target (for logging)
        next_pan = self.current_pan + self.pan_step
        next_tilt = self.current_tilt + self.tilt_step
        # Command the relative move by one step size
        cmd = f"M {self.pan_step:.3f} {self.tilt_step:.3f}"
        print(f"[Tripod] Moving to next position: pan->{next_pan:.3f}°, tilt->{next_tilt:.3f}° (delta step)")
        self._send_command(cmd, wait_for_ack=True)
        # Update current position
        self.current_pan = next_pan
        self.current_tilt = next_tilt
        # Optional settle wait
        if self.settle_time > 0:
            time.sleep(self.settle_time)
        return True

    def disconnect(self):
        """Close the serial connection to the tripod."""
        if self.serial:
            try:
                # Optionally, ensure motors are stopped before disconnecting
                self.serial.write(b"X\n")  # stop both axes
                self.serial.flush()
            except Exception:
                pass
            time.sleep(0.5)
            self.serial.close()
            self.serial = None
            print("[Tripod] Disconnected from tripod.")