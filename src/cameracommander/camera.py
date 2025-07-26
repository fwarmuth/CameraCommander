# camera.py

import gphoto2 as gp
import os
import time
import sys
from . import logger

class Camera:
    def __init__(self):
        camera_list = gp.Camera.autodetect()
        if not camera_list:
            logger.error("No camera detected.")
            raise RuntimeError("No camera detected.")

        # Assuming the first detected camera is the one we want to use
        idx = 0
        camera_name = camera_list[idx][0]
        camera_port = camera_list[idx][1]

        port_info_list = gp.PortInfoList()
        port_info_list.load()
        camera_info_list = gp.CameraAbilitiesList()
        camera_info_list.load()

        camera_index = camera_info_list.lookup_model(camera_name)
        camera_abilities = camera_info_list.get_abilities(camera_index)
        port_index = port_info_list.lookup_path(camera_port)
        port_info = port_info_list.get_info(port_index)

        self.camera = gp.Camera()
        self.camera.set_abilities(camera_abilities)
        self.camera.set_port_info(port_info)
        try:
            self.camera.init()
            logger.info(f"Camera '{camera_name}' initialized on port '{camera_port}'.")
        except gp.GPhoto2Error as e:
            logger.error(f"Failed to initialize camera: {e}")
            raise

    def exit(self):
        self.camera.exit()
        logger.info("Camera exited.")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.exit()

    def reinit(self):
        # Re-initialize the camera object
        self.__init__()
        self.set_camera_settings(self.settings)

    @staticmethod
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

    def list_all_camera_settings(self):
        settings = {}
        config = self.camera.get_config()
        def recurse_config(widget, path=''):
            for child in widget.get_children():
                name = child.get_name()
                label = child.get_label()
                child_path = f"{path}/{name}" if path else name
                widget_type = child.get_type()
                type_str = self.widget_type_to_string(widget_type)
                settings[child_path] = {
                    'label': label,
                    'type': type_str
                }
                recurse_config(child, child_path)
        recurse_config(config)
        return settings

    def get_setting_valid_values(self, setting_key):
        config = self.camera.get_config()
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

    def set_camera_settings(self, settings):
        self.settings = settings
        config = self.camera.get_config()
        for key, value in settings.items():
            try:
                keys = key.split('/')
                widget = config
                for k in keys:
                    widget = widget.get_child_by_name(k)
                
                widget_type = widget.get_type()
                if widget_type in (gp.GP_WIDGET_MENU, gp.GP_WIDGET_RADIO):
                    choices = [widget.get_choice(i) for i in range(widget.count_choices())]
                    if value not in choices:
                        logger.warning(f"Invalid value '{value}' for {key}. Available choices are: {choices}")
                        continue
                
                widget.set_value(value)
                logger.info(f"Set {key} to {value}")

            except gp.GPhoto2Error as e:
                logger.error(f"Failed to set {key} to {value}: {e}")
            except Exception as e:
                logger.error(f"An unexpected error occurred while setting {key}: {e}")
        
        try:
            self.camera.set_config(config)
            logger.info("Camera settings applied.")
        except gp.GPhoto2Error as e:
            logger.error(f"Failed to apply camera settings: {e}")

    def validate_settings(self, settings):
        config = self.camera.get_config()
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

    @staticmethod
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

    def capture_image(self, filename, long_exposure=None):
        if long_exposure is not None:
            # Set the camera to Bulb mode
            self.set_camera_settings({'shutterspeed': 'bulb'})
            # Start the exposure by setting eosremoterelease to 'Press Full'
            logger.info(f"Starting long exposure for {long_exposure} seconds...")
            self.set_camera_settings({'eosremoterelease': 'Press Full'})
            self.countdown_timer(long_exposure)
            # End the exposure by setting eosremoterelease to 'Release Full'
            logger.info("Ending long exposure.")
            self.set_camera_settings({'eosremoterelease': 'Release Full'})
            # Wait for the camera to process the image
            time.sleep(2)
            # Retrieve the image
            event_type, event_data = self.camera.wait_for_event(1000)
            while event_type != gp.GP_EVENT_FILE_ADDED:
                event_type, event_data = self.camera.wait_for_event(1000)
            file_path = event_data
        else:
            # Regular capture
            max_retries = 2
            attempt = 0
            while attempt <= max_retries:
                try:
                    file_path = self.camera.capture(gp.GP_CAPTURE_IMAGE)
                    break
                except gp.GPhoto2Error as e:
                    attempt += 1
                    logger.warning(f"Attempt {attempt} to capture image failed: {e}")
                    time.sleep(1)  # Wait for 1 second before retrying
                    if attempt > max_retries:
                        logger.error(f"Failed to capture image after {max_retries} attempts. Restarting USB")
                        self.reinit()
                        attempt = 0

        # Get the image file
        camera_file = self.camera.file_get(
            file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL)
        # Save the image to local disk
        target = os.path.join(os.getcwd(), filename)
        camera_file.save(target)
        logger.info(f"Image saved to {target}")
        return target

    def start_timelapse(self, script_settings, tripod=None):
        interval = script_settings.get('interval', 10)
        frames = script_settings.get('frames', 10)
        duration = script_settings.get('duration', None)
        target_path = script_settings.get('target_path', os.getcwd())
        
        target_path = os.path.join(target_path, f"timelapse_{time.strftime('%Y%m%d_%H%M%S')}")
        if not os.path.exists(target_path):
            os.makedirs(target_path, exist_ok=True)
            logger.info(f"Created target directory: {target_path}")

        if duration is not None:
            duration_seconds = duration * 3600
            total_time = 0

        logger.info("Starting time-lapse capture...")
        for i in range(frames):
            start_time = time.time()
            if duration is not None and total_time >= duration_seconds:
                logger.info("Reached duration limit.")
                break

            if tripod:
                if tripod.movement_mode == "incremental":
                    tripod.move_incremental_step(i)
                elif tripod.movement_mode == "continuous" and i == 0:
                    tripod.start_continuous_move()

            filename = os.path.join(target_path, f"image_{i+1:04d}.jpg")
            try:
                self.capture_image(filename)
                logger.info(f"Captured {filename}")
            except Exception as e:
                logger.error(f"Failed to capture image: {e}")
                continue

            if i < frames - 1:
                elapsed_time = time.time() - start_time
                wait_time = interval - elapsed_time
                if wait_time > 0:
                    time.sleep(wait_time)
                else:
                    logger.warning("Capture and processing took longer than the interval.")
                
                if duration is not None:
                    total_time += interval

            if i > 0 and i % 5 == 0:
                try:
                    battery_level = self.get_battery_level()
                    logger.info(f"Battery Level: {battery_level}")
                except gp.GPhoto2Error as e:
                    logger.warning(f"Could not retrieve battery level: {e}")

        if tripod and tripod.movement_mode == "continuous":
            tripod.stop_continuous_move()
            
        logger.info("Time-lapse capture completed.")

    def set_camera_settings_to_auto(self):
        config = self.camera.get_config()
        def recurse_and_set_auto(widget):
            for child in widget.get_children():
                widget_type = child.get_type()
                if widget_type in [gp.GP_WIDGET_MENU, gp.GP_WIDGET_RADIO]:
                    choices = [child.get_choice(i) for i in range(child.count_choices())]
                    if 'Auto' in choices:
                        child.set_value('Auto')
                        self.camera.set_config(config)
                        logger.info(f"Set {child.get_name()} to Auto")
                recurse_and_set_auto(child)
        recurse_and_set_auto(config)

    def get_current_camera_settings(self):
        settings = {}
        config = self.camera.get_config()
        def recurse_config(widget, path=''):
            for child in widget.get_children():
                name = child.get_name()
                child_path = f"{path}/{name}" if path else name
                try:
                    value = child.get_value()
                    settings[child_path] = value
                except gp.GPhoto2Error:
                    pass  # Some widgets may not be readable
                recurse_config(child, child_path)
        recurse_config(config)
        return settings

    def get_battery_level(self):
        config = self.camera.get_config()
        try:
            battery_widget = config.get_child_by_name('batterylevel')
            battery_level = battery_widget.get_value()
            return battery_level
        except gp.GPhoto2Error as e:
            logger.error(f"Could not get battery level: {e}")
            return "Unknown"

