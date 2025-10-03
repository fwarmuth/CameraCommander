# CameraCommander Firmware

ESP8266 firmware for a two‑axis stepper tripod. Simple serial protocol, absolute moves, DONE/S/OK replies.

## Setup

- Requires: PlatformIO CLI, NodeMCU v3 (ESP8266), A4988/TMC step/dir/enable drivers.
- Build/flash/monitor from repo root (PlatformIO points `src_dir` to `firmware/src`):
  - Build: `pio run`
  - Flash: `pio upload`
  - Monitor: `pio device monitor`

## Commands

```
V                            # version
M <pan_deg> <tilt_deg>       # absolute move; replies DONE when finished
S                            # STATUS <pan> <tilt> <drivers 0|1>
1 2 4 8 6                    # microstep (6 == 16)
n/c/r/x                      # pan: step / revolution / toggle dir / stop
w/p/t/z                      # tilt: step / revolution / toggle dir / stop
X                            # stop both axes
+ / -                        # faster / slower (10%)
d / e                        # disable / enable drivers (resets position to 0)
```

## Layout

- `src/GearedStepper.h/.cpp` — AccelStepper wrapper with gearing + microstep control
- `src/main.cpp` — command parser, pin map, motion control
- Pin mapping and mechanics are defined in `src/main.cpp`.

## Mock firmware server

A pure-Python emulator lives in `firmware/mock_firmware_server.py`. It speaks the same
serial protocol as the ESP firmware so you can exercise the desktop app without
hardware attached.

Start it in a shell:

```
python -m firmware.mock_firmware_server --host 127.0.0.1 --port 9999 --deg-per-second 60
```

Then point the tripod configuration at `socket://127.0.0.1:9999`. Adjust
`--deg-per-second` (movement speed) and `--settle-delay` if you want slower or faster
responses. Stop the mock with `Ctrl+C`.
