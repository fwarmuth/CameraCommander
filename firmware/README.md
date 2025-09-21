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

