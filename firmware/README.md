# CameraCommander Firmware

Firmware for the motor control board of CameraCommander. It targets an
ESP8266 NodeMCU v3 and is built with [PlatformIO](https://platformio.org/)
using the Arduino framework. Two geared stepper motors are driven through
standard step/dir/enable drivers (e.g. A4988 or TMC series).

## Features

- Dual‑axis pan and tilt control
- Serial command interface
- Adjustable micro‑stepping and motor speed
- Driver enable/disable control

## Requirements

- [PlatformIO CLI](https://docs.platformio.org/en/latest/core/installation.html)
- NodeMCU v3 (ESP8266)
- Stepper drivers wired as defined in `src/main.cpp`
- AccelStepper library (installed automatically)

## Building

```sh
pio run
```

## Flashing

```sh
pio upload
```

## Serial Monitor

```sh
pio device monitor
```

## Command Reference

| Command | Description |
|---------|-------------|
| `V` | Print firmware version |
| `M <pan_deg> <tilt_deg>` | Move pan and tilt to absolute angles |
| `1` `2` `4` `8` `6` | Set micro‑step resolution (6 = 16) |
| `n` / `w` | One micro‑step on pan/tilt axis |
| `c` / `p` | One full revolution on pan/tilt axis |
| `r` / `t` | Toggle direction of pan/tilt axis |
| `x` / `z` | Stop pan/tilt axis |
| `X` | Stop both axes |
| `+` / `-` | Increase/decrease speed by 10% |
| `S` | Report current absolute angles and driver state |
| `d` / `e` | Disable/enable both drivers |

## Project Structure

```
firmware/
├── platformio.ini  # PlatformIO configuration
└── src
    ├── GearedStepper.h/.cpp  # Stepper helper class
    └── main.cpp              # Command interpreter and motor control
```

