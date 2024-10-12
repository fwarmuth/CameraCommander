# camera.py

import gphoto2 as gp
import os
import time
import sys

def init_camera():
    camera = gp.Camera()
    camera.init()
    return camera

def exit_camera(camera):
    camera.exit()

def widget_type_to_string(widget_type):
    if widget_type == gp.GP_WIDGET_WINDOW:
        return 'WINDOW'
    elif widget_type == gp.GP_WIDGET_SECTION:
        return 'SECTION'
    elif widget_type == gp.GP_WIDGET_TEXT:
        return 'TEXT'
    elif widget_type == gp.GP_WIDGET_RANGE:
        return 'RANGE'
    elif widget_type == gp.GP_WIDGET_TOGGLE:
        return 'TOGGLE'
    elif widget_type == gp.GP_WIDGET_RADIO:
        return 'RADIO'
    elif widget_type == gp.GP_WIDGET_MENU:
        return 'MENU'
    elif widget_type == gp.GP_WIDGET_BUTTON:
        return 'BUTTON'
    elif widget_type == gp.GP_WIDGET_DATE:
        return 'DATE'
    else:
        return 'UNKNOWN'

def list_all_camera_settings(camera):
    settings = {}
    config = camera.get_config()
    def recurse_config(widget, path=''):
        for child in widget.get_children():
            name = child.get_name()
            label = child.get_label()
            child_path = f"{path}/{name}" if path else name
            widget_type = child.get_type()
            type_str = widget_type_to_string(widget_type)
            settings[child_path] = {
                'label': label,
                'type': type_str
            }
            recurse_config(child, child_path)
    recurse_config(config)
    return settings

def get_setting_valid_values(camera, setting_key):
    config = camera.get_config()
    keys = setting_key.split('/')
    widget = config
    try:
        for key in keys:
            widget = widget.get_child_by_name(key)
    except gp.GPhoto2Error:
        return None
    widget_type = widget.get_type()
    valid_values = None
    if widget_type in [gp.GP_WIDGET_MENU, gp.GP_WIDGET_RADIO]:
        valid_values = [widget.get_choice(i) for i in range(widget.count_choices())]
    elif widget_type == gp.GP_WIDGET_RANGE:
        min_value, max_value, increment = widget.get_range()
        valid_values = (min_value, max_value, increment)
    elif widget_type == gp.GP_WIDGET_TOGGLE:
        valid_values = [True, False]
    return valid_values

def set_camera_settings(camera, settings):
    config = camera.get_config()
    for key, value in settings.items():
        try:
            keys = key.split('/')
            widget = config
            for k in keys:
                widget = widget.get_child_by_name(k)
            if widget.get_type() == gp.GP_WIDGET_MENU:
                choices = [widget.get_choice(i) for i in range(widget.count_choices())]
                if value not in choices:
                    print(f"Invalid value '{value}' for {key}. Available choices are: {choices}")
                    continue
            widget.set_value(value)
            camera.set_config(config)
            print(f"Set {key} to {value}")
        except gp.GPhoto2Error as e:
            print(f"Failed to set {key} to {value}: {e}")
        except Exception as e:
            print(f"Error setting {key}: {e}")

def validate_settings(camera, settings):
    config = camera.get_config()
    for key, value in settings.items():
        try:
            keys = key.split('/')
            widget = config
            for k in keys:
                widget = widget.get_child_by_name(k)
            if widget.get_type() == gp.GP_WIDGET_MENU:
                choices = [widget.get_choice(i) for i in range(widget.count_choices())]
                if value not in choices:
                    raise ValueError(f"Invalid value '{value}' for {key}. Available choices are: {choices}")
            # Additional validation can be added here
        except gp.GPhoto2Error as e:
            raise ValueError(f"Failed to access setting {key}: {e}")
        except Exception as e:
            raise ValueError(f"Error validating setting {key}: {e}")


def countdown_timer(duration):
    """Display a countdown timer with a progress bar for the specified duration in seconds."""
    total_duration = duration
    start_time = time.time()
    bar_length = 30  # Length of the progress bar
    while True:
        elapsed_time = time.time() - start_time
        remaining_time = int(duration - elapsed_time)
        if remaining_time < 0:
            remaining_time = 0
        # Calculate progress
        progress = elapsed_time / total_duration
        if progress > 1:
            progress = 1
        # Build progress bar
        filled_length = int(bar_length * progress)
        bar = '#' * filled_length + '-' * (bar_length - filled_length)
        # Format time
        mins, secs = divmod(remaining_time, 60)
        time_format = f"{mins:02d}:{secs:02d}"
        # Display
        sys.stdout.write(f"\r[{bar}] {time_format} remaining")
        sys.stdout.flush()
        if remaining_time <= 0:
            break
        time.sleep(1)
    # Clear the line after countdown finishes
    sys.stdout.write("\r" + " " * (bar_length + 30) + "\r")
    sys.stdout.flush()


def capture_image(camera, filename, long_exposure=None):
    if long_exposure is not None:
        # Set the camera to Bulb mode
        set_camera_settings(camera, {'shutterspeed': 'bulb'})
        # Start the exposure by setting eosremoterelease to 'Press Full'
        print(f"Starting long exposure for {long_exposure} seconds...")
        set_camera_settings(camera, {'eosremoterelease': 'Press Full'})
        countdown_timer(long_exposure)
        # End the exposure by setting eosremoterelease to 'Release Full'
        print("Ending long exposure.")
        set_camera_settings(camera, {'eosremoterelease': 'Release Full'})
        # Wait for the camera to process the image
        time.sleep(2)
        # Retrieve the image
        event_type, event_data = camera.wait_for_event(1000)
        while event_type != gp.GP_EVENT_FILE_ADDED:
            event_type, event_data = camera.wait_for_event(1000)
        file_path = event_data
    else:
        # Regular capture
        file_path = camera.capture(gp.GP_CAPTURE_IMAGE)
    # Get the image file
    camera_file = camera.file_get(
        file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL)
    # Save the image to local disk
    target = os.path.join(os.getcwd(), filename)
    camera_file.save(target)
    print(f"Image saved to {target}")
    return target

def start_timelapse(camera, script_settings):
    interval = script_settings.get('interval', 10)
    frames = script_settings.get('frames', 10)
    duration = script_settings.get('duration', None)
    target_path = script_settings.get('target_path', os.getcwd())
    # Create target directory with timestamp
    target_path = os.path.join(target_path, f"timelapse_{time.strftime('%Y%m%d_%H%M%S')}")
    if not os.path.exists(target_path):
        os.makedirs(target_path, exist_ok=True)
        print(f"Created target directory: {target_path}")
    if duration is not None:
        duration_seconds = duration * 3600  # Convert hours to seconds
        total_time = 0
    # Time-lapse capture
    print("Starting time-lapse capture...")
    for i in range(frames):
        start_time = time.time()
        if duration is not None and total_time >= duration_seconds:
            print("Reached duration limit.")
            break
        # Create unique filename
        filename = os.path.join(target_path, f"image_{i+1:04d}.jpg")
        try:
            capture_image(camera, filename)
            print(f"Captured {filename}")
        except Exception as e:
            print(f"Failed to capture image: {e}")
            continue
        if i < frames - 1:
            # Wait for the interval - time taken to capture the image
            elapsed_time = time.time() - start_time
            if elapsed_time < interval:
                time.sleep(interval - elapsed_time)
            else:
                print(f"Warning: Image capture took longer than the interval.")
            if duration is not None:
                total_time += interval
        if i % 5 == 0:
            battery_level = get_battery_level(camera)
            print(f"Battery Level: {battery_level}")
    print("Time-lapse capture completed.")

def set_camera_settings_to_auto(camera):
    config = camera.get_config()
    def recurse_and_set_auto(widget):
        for child in widget.get_children():
            widget_type = child.get_type()
            if widget_type in [gp.GP_WIDGET_MENU, gp.GP_WIDGET_RADIO]:
                choices = [child.get_choice(i) for i in range(child.count_choices())]
                if 'Auto' in choices:
                    child.set_value('Auto')
                    camera.set_config(config)
                    print(f"Set {child.get_name()} to Auto")
            recurse_and_set_auto(child)
    recurse_and_set_auto(config)

def get_current_camera_settings(camera):
    settings = {}
    config = camera.get_config()
    def recurse_config(widget, path=''):
        for child in widget.get_children():
            name = child.get_name()
            child_path = f"{path}/{name}" if path else name
            widget_type = child.get_type()
            try:
                value = child.get_value()
                settings[child_path] = value
            except gp.GPhoto2Error:
                pass  # Some widgets may not be readable
            recurse_config(child, child_path)
    recurse_config(config)
    return settings

def get_battery_level(camera):
    config = camera.get_config()
    battery_widget = config.get_child_by_name('batterylevel')
    battery_level = battery_widget.get_value()
    return battery_level

