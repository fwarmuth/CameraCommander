import click
from cameracommander.settings import load_settings, save_settings
from cameracommander.camera import Camera
from tripod_commander.tripod import Tripod
from cameracommander import logger


@click.group()
def cli():
    """Time-lapse CLI Application"""
    pass


@cli.command()
@click.option('--settings-file', default='settings.yaml', help='Path to the settings YAML file.')
def check_settings(settings_file):
    """Check a given settings.yaml for its validity by applying it to the camera."""
    try:
        settings = load_settings(settings_file)
        camera_settings = settings.get('camera_settings', {})
        with Camera() as camera:
            camera.validate_settings(camera_settings)
        logger.info("Settings are valid.")
    except Exception as e:
        logger.error(f"Settings validation failed: {e}")


@cli.command()
def list_settings():
    """Show all possible camera settings and their keys."""
    try:
        with Camera() as camera:
            settings = camera.list_all_camera_settings()
        for path, info in settings.items():
            logger.info(f"{path}: {info['label']} (Type: {info['type']})")
    except Exception as e:
        logger.error(f"Failed to list camera settings: {e}")


@cli.command()
@click.option('--settings-file', default='settings.yaml', help='Path to the settings YAML file.')
def list_available_values(settings_file):
    """Show possible setting values for camera settings in the given settings.yaml."""
    try:
        settings = load_settings(settings_file)
        camera_settings = settings.get('camera_settings', {})
        with Camera() as camera:
            for key in camera_settings.keys():
                valid_values = camera.get_setting_valid_values(key)
                if valid_values is not None:
                    logger.info(f"\nSetting '{key}' valid values:")
                    if isinstance(valid_values, list):
                        for val in valid_values:
                            logger.info(f"  - {val}")
                    elif isinstance(valid_values, tuple):
                        min_value, max_value, increment = valid_values
                        logger.info(f"  Range: {min_value} to {max_value}, increment: {increment}")
                else:
                    logger.warning(f"Setting '{key}' valid values not available.")
    except Exception as e:
        logger.error(f"Failed to list available values: {e}")


@cli.command()
@click.option('--settings-file', default='settings.yaml', help='Path to the settings YAML file.')
@click.option('--long-exposure', type=float, default=None, help='Exposure time in seconds for long exposure using Bulb mode.')
def snapshot(settings_file, long_exposure):
    """Create a snapshot using the camera settings in a given settings.yaml."""
    try:
        settings = load_settings(settings_file)
        camera_settings = settings.get('camera_settings', {})
        with Camera() as camera:
            battery_level = camera.get_battery_level()
            logger.info(f"Battery level: {battery_level}")
            camera.set_camera_settings(camera_settings)
            camera.capture_image('snapshot.jpg', long_exposure=long_exposure)
        logger.info("Snapshot taken and saved as 'snapshot.jpg'.")
    except Exception as e:
        logger.error(f"Failed to take snapshot: {e}")


@cli.command()
@click.option('--settings-file', default='settings.yaml', help='Path to the settings YAML file.')
@click.option('--tripod', is_flag=True, help='Enable tripod mode.')
def timelapse(settings_file, tripod):
    """Start a timelapse using settings in settings.yaml."""
    try:
        settings = load_settings(settings_file)
        script_settings = settings.get('script_settings', {})
        camera_settings = settings.get('camera_settings', {})
        tripod_settings = settings.get('tripod_settings', {}) if tripod else None

        with Camera() as camera:
            camera.set_camera_settings(camera_settings)
            proceed = 'n'
            while proceed == 'n':
                # Take test shot
                camera.capture_image('snapshot.jpg')
                # Downsampling the image for web display 800x600
                proceed = click.prompt("Check the test image (test_image.jpg). Do you want to proceed? (y/n/q)", default='n')
                if proceed.lower() == 'q':
                    logger.info("Exiting.")
                    return

            if tripod:
                with Tripod(
                    pan_start=tripod_settings.get('pan_start', 0),
                    pan_end=tripod_settings.get('pan_end', 0),
                    tilt_start=tripod_settings.get('tilt_start', 0),
                    tilt_end=tripod_settings.get('tilt_end', 0),
                    frames=script_settings.get('frames', 10),
                    port=tripod_settings.get('port', 'auto'),
                    movement_mode=tripod_settings.get('movement_mode', 'incremental'),
                    settle_time=tripod_settings.get('settle_time', 1.0),
                    microstep_resolution=tripod_settings.get('microstep_resolution', '1/16'),
                    command_timeout=tripod_settings.get('command_timeout', 10.0)
                ) as tripod_device:
                    camera.start_timelapse(script_settings, tripod=tripod_device)
            else:
                camera.start_timelapse(script_settings)

    except Exception as e:
        logger.error(f"Timelapse failed: {e}")


@cli.command()
@click.option('--save-settings', is_flag=True, help='Save the detected settings to settings.yaml.')
def auto_adjust(save_settings_flag):
    """Take a snapshot with all auto settings and print the used camera settings."""
    try:
        with Camera() as camera:
            camera.set_camera_settings_to_auto()
            camera.capture_image('auto_adjust_snapshot.jpg')
            current_settings = camera.get_current_camera_settings()
        logger.info("Current Camera Settings:")
        for key, value in current_settings.items():
            logger.info(f"{key}: {value}")
        if save_settings_flag:
            settings_to_save = {
                'script_settings': {
                    'interval': 10,
                    'frames': 100
                },
                'camera_settings': current_settings
            }
            save_settings(settings_to_save, 'settings.yaml')
            logger.info("Settings saved to 'settings.yaml'.")
    except Exception as e:
        logger.error(f"Auto-adjust failed: {e}")


if __name__ == '__main__':
    cli()
