import click
from .settings import load_settings, save_settings
from .camera import (
    list_all_camera_settings,
    get_setting_valid_values,
    capture_image,
    set_camera_settings,
    start_timelapse,
    init_camera,
    exit_camera,
    validate_settings,
    set_camera_settings_to_auto,
    get_current_camera_settings
)

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
        camera = init_camera()
        validate_settings(camera, camera_settings)
        exit_camera(camera)
        click.echo("Settings are valid.")
    except Exception as e:
        click.echo(f"Settings validation failed: {e}")

@cli.command()
def list_settings():
    """Show all possible camera settings and their keys."""
    try:
        camera = init_camera()
        settings = list_all_camera_settings(camera)
        exit_camera(camera)
        for path, info in settings.items():
            click.echo(f"{path}: {info['label']} (Type: {info['type']})")
    except Exception as e:
        click.echo(f"Failed to list camera settings: {e}")

@cli.command()
@click.option('--settings-file', default='settings.yaml', help='Path to the settings YAML file.')
def list_available_values(settings_file):
    """Show possible setting values for camera settings in the given settings.yaml."""
    try:
        settings = load_settings(settings_file)
        camera_settings = settings.get('camera_settings', {})
        camera = init_camera()
        for key in camera_settings.keys():
            valid_values = get_setting_valid_values(camera, key)
            if valid_values is not None:
                click.echo(f"\nSetting '{key}' valid values:")
                if isinstance(valid_values, list):
                    for val in valid_values:
                        click.echo(f"  - {val}")
                elif isinstance(valid_values, tuple):
                    min_value, max_value, increment = valid_values
                    click.echo(f"  Range: {min_value} to {max_value}, increment: {increment}")
            else:
                click.echo(f"Setting '{key}' valid values not available.")
        exit_camera(camera)
    except Exception as e:
        click.echo(f"Failed to list available values: {e}")

@cli.command()
@click.option('--settings-file', default='settings.yaml', help='Path to the settings YAML file.')
@click.option('--long-exposure', type=float, default=None, help='Exposure time in seconds for long exposure using Bulb mode.')
def snapshot(settings_file, long_exposure):
    """Create a snapshot using the camera settings in a given settings.yaml."""
    try:
        settings = load_settings(settings_file)
        camera_settings = settings.get('camera_settings', {})
        from camera import get_battery_level
        camera = init_camera()
        get_battery_level(camera)
        set_camera_settings(camera, camera_settings)
        capture_image(camera, 'snapshot.jpg', long_exposure=long_exposure)
        exit_camera(camera)
        click.echo("Snapshot taken and saved as 'snapshot.jpg'.")
    except Exception as e:
        click.echo(f"Failed to take snapshot: {e}")

@cli.command()
@click.option('--settings-file', default='settings.yaml', help='Path to the settings YAML file.')
def timelapse(settings_file):
    """Start a timelapse using settings in settings.yaml."""
    try:
        settings = load_settings(settings_file)
        script_settings = settings.get('script_settings', {})
        camera_settings = settings.get('camera_settings', {})
        try:
            camera = init_camera()
        except Exception as e:
            raise Exception(f"Init camera failed: {e}")
        set_camera_settings(camera, camera_settings)
        # Take test shot
        capture_image(camera, 'snapshot.jpg')
        # Downsampling the image for web display 800x600
        proceed = click.prompt("Check the test image (test_image.jpg). Do you want to proceed? (y/n)", default='n')
        if proceed.lower() != 'y':
            click.echo("Exiting.")
            exit_camera(camera)
            return
        # Start timelapse
        start_timelapse(camera, script_settings)
        exit_camera(camera)
    except Exception as e:
        click.echo(f"Timelapse failed: {e}")

@cli.command()
@click.option('--save-settings', is_flag=True, help='Save the detected settings to settings.yaml.')
def auto_adjust(save_settings):
    """Take a snapshot with all auto settings and print the used camera settings."""
    try:
        camera = init_camera()
        set_camera_settings_to_auto(camera)
        capture_image(camera, 'auto_adjust_snapshot.jpg')
        current_settings = get_current_camera_settings(camera)
        exit_camera(camera)
        click.echo("Current Camera Settings:")
        for key, value in current_settings.items():
            click.echo(f"{key}: {value}")
        if save_settings:
            settings_to_save = {
                'script_settings': {
                    'interval': 10,
                    'frames': 100
                },
                'camera_settings': current_settings
            }
            save_settings(settings_to_save, 'settings.yaml')
            click.echo("Settings saved to 'settings.yaml'.")
    except Exception as e:
        click.echo(f"Auto-adjust failed: {e}")

if __name__ == '__main__':
    cli()

