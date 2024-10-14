# CameraComander

A CLI application for creating time-lapse videos using a gphoto2 compatiable camera. So far only tested with a Canon EOS 6D2 and EOS R50 tethered (USB).
The application allows you to configure camera settings utilizing a file which makes it easier to reproduce captures.
Main function is to capture snapshots and start time lapse recordings, by continuously capturing images at a given interval. The images are saved to a folder and can be used to create a time-lapse videos, e.g. using ffmpeg.

## Example `settings.yaml`

```yaml
script_settings:
  interval: 10
  frames: 100

camera_settings:
  iso: 100
  aperture: 2.8
  shutter_speed: 1/60
  white_balance: auto
```

## Example Workflow (timelapse)
1. Connect Camera via USB
2. Run `cameracommander auto_adjust --save-settings` to detect camera settings and save them to `settings.yaml`
3. Modify `settings.yaml` to your needs
4. Run `cameracommander timelapse --settings-file path/to/settings.yaml` to start a timelapse
5. Use ffmpeg to create a video from the images

Here is one example result of a timelapse created with this tool without any post processing:

![Example](docs/nothernlights.gif)


## Commands

The CLI provides several commands to interact with the camera. Below are some examples of how to use these commands.

### Check Settings
Check the validity of a given `settings.yaml` file by applying it to the camera.
```sh
$ cameracommander check_settings --settings-file path/to/settings.yaml
```
### List Settings
Show all possible camera settings and their keys.
```sh
$ cameracommander list_settings
```

### List Available Values
Show all possible setting values for camera settings in the given `settings.yaml`.
```sh
$ cameracommander list_available_values --settings-file path/to/settings.yaml
```

### Snapshot
Create a snapshot using the camera settings in a given `settings.yaml`.
```sh
$ cameracommander snapshot --settings-file path/to/settings.yaml
```
### Timelapse
Start a timelapse using settings in `settings.yaml`.
```sh
$ cameracommander timelapse --settings-file path/to/settings.yaml
```

### Auto Adjust

Take a snapshot with all auto settings and print the used camera settings. Optionally, save the detected settings to `settings.yaml`.

```sh
$ cameracommander auto_adjust [--save-settings] 
```

## How to install
### From Source, in a venv
This project uses uv as a package manager. So install it first -> https://docs.astral.sh/uv/getting-started/installation/)

Then:
```sh
$ git clone https://github.com/fwarmuth/CameraCommander.git
$ cd CameraCommander
$ uv run cameracommander --help
```

### From PyPi
```sh
$ pip install cameracommander
$ cameracommander --help
```

