import asyncio
import logging
import io
import tempfile
from typing import Any, Dict

import gradio as gr
import numpy as np
from PIL import Image
import yaml

from camerawrapper import CameraWrapper, CameraError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global camera instance and lock
cam: CameraWrapper = None
camera_lock = asyncio.Lock()

CROP_STATE = None


async def initialize_camera():
    """Initializes the camera and enables the viewfinder."""
    global cam
    if cam is None:
        try:
            logger.info("Initializing camera...")
            cam = await asyncio.to_thread(CameraWrapper.select_camera, "Canon")
            await asyncio.to_thread(cam.apply_settings, {"main.actions.viewfinder": 1})
            logger.info("Camera initialized and viewfinder enabled.")
        except CameraError as e:
            logger.error(f"Could not open camera: {e}")
            cam = None

async def close_camera():
    """Disables the viewfinder and releases the camera."""
    global cam
    if cam:
        try:
            logger.info("Disabling viewfinder and shutting down camera...")
            await asyncio.to_thread(cam.apply_settings, {"main.actions.viewfinder": 0})
            await asyncio.to_thread(cam.__exit__, None, None, None)
            logger.info("Camera shut down.")
        except CameraError as e:
            logger.error(f"Error closing camera: {e}")
        finally:
            cam = None

async def get_live_frame(crop_state=None):
    """Captures a single frame from the camera's live view."""
    if cam is None:
        return np.zeros((480, 640, 3), dtype=np.uint8)  # Return a black image if camera is not available

    async with camera_lock:
        try:
            frame_data = await asyncio.to_thread(cam.capture_preview)
            image = Image.open(io.BytesIO(frame_data.getvalue()))
            original_size = image.size

            if crop_state and 'center' in crop_state and 'size' in crop_state:
                center_x, center_y = crop_state['center']
                size = crop_state['size']
                
                width, height = original_size
                
                left = max(0, center_x - size // 2)
                top = max(0, center_y - size // 2)
                right = min(width, center_x + size // 2)
                bottom = min(height, center_y + size // 2)

                if left >= right or top >= bottom:
                    return np.array(image)
                
                cropped_image = image.crop((left, top, right, bottom))
                
                # Resize to original size using nearest neighbor
                resized_image = cropped_image.resize(original_size, Image.NEAREST)
                return np.array(resized_image)

            return np.array(image)
        except Exception as e:
            logger.error(f"Error capturing preview: {e}")
            return np.zeros((480, 640, 3), dtype=np.uint8)

async def focus_camera(direction: str, step_size: int):
    """
    Adjusts the camera focus.

    Args:
        direction (str): The direction to focus ('near' or 'far').
        step_size (int): The amount to adjust the focus.
    """
    if cam is None:
        logger.warning("Focus adjustment attempted but camera is not available.")
        return "Camera not available."

    async with camera_lock:
        try:
            logger.info(f"Adjusting focus: {direction} {step_size}")
            await asyncio.to_thread(cam.focus_step, direction, step_size, live_view=True)
            return f"Focus adjusted: {direction}, Step: {step_size}"
        except CameraError as e:
            logger.error(f"Error setting focus: {e}")
            return f"Error: {e}"

def create_gradio_interface():
    """Creates and returns the Gradio interface."""
    with gr.Blocks() as demo:
        crop_state_val = gr.State(None)

        gr.Markdown("# Camera Commander - Advanced Live View")

        # Fetch current camera settings to populate UI choices
        iso_choices: list[Any] = []
        shutter_choices: list[Any] = []
        aperture_choices: list[Any] = []
        wb_choices: list[Any] = []
        iso_val = shutter_val = aperture_val = wb_val = None
        if cam:
            current: Dict[str, Dict[str, Any]] = cam.query_settings()

            def _get(key: str):
                return current.get(key, {})

            iso_entry = _get("main.imgsettings.iso")
            shutter_entry = _get("main.capturesettings.shutterspeed")
            aperture_entry = _get("main.capturesettings.aperture")
            wb_entry = _get("main.imgsettings.whitebalance")

            iso_choices = iso_entry.get("choices") or []
            shutter_choices = shutter_entry.get("choices") or []
            aperture_choices = aperture_entry.get("choices") or []
            wb_choices = wb_entry.get("choices") or []
            iso_val = iso_entry.get("current")
            shutter_val = shutter_entry.get("current")
            aperture_val = aperture_entry.get("current")
            wb_val = wb_entry.get("current")

        with gr.Row():
            with gr.Column():
                live_image = gr.Image(label="Live View", type="numpy")
            with gr.Column():
                gr.Markdown("## Focus Control")
                focus_in_btn = gr.Button("Focus In/Near")
                focus_out_btn = gr.Button("Focus Out/Far")
                step_size_slider = gr.Slider(minimum=1, maximum=3, step=1, value=3, label="Step Size")
                focus_status = gr.Textbox(label="Focus Status")

                gr.Markdown("## Crop Control")
                crop_size_slider = gr.Slider(minimum=50, maximum=500, step=10, value=200, label="Crop Size")
                reset_crop_btn = gr.Button("Reset Crop")

                gr.Markdown("## Camera Settings")
                iso_dropdown = gr.Dropdown(
                    choices=iso_choices, value=iso_val, label="ISO"
                )
                shutter_dropdown = gr.Dropdown(
                    choices=shutter_choices, value=shutter_val, label="Shutter Speed"
                )
                aperture_dropdown = gr.Dropdown(
                    choices=aperture_choices, value=aperture_val, label="Aperture"
                )
                wb_dropdown = gr.Dropdown(
                    choices=wb_choices, value=wb_val, label="White Balance"
                )
                apply_settings_btn = gr.Button("Apply Camera Settings")
                settings_status = gr.Textbox(label="Settings Status")
                export_btn = gr.Button("Export Camera Settings")
                export_file = gr.File(label="Settings YAML")


        async def focus_in_handler(step):
            return await focus_camera('near', step)

        async def focus_out_handler(step):
            return await focus_camera('far', step)

        focus_in_btn.click(fn=focus_in_handler, inputs=step_size_slider, outputs=focus_status)
        focus_out_btn.click(fn=focus_out_handler, inputs=step_size_slider, outputs=focus_status)

        def set_crop(crop_size, evt: gr.SelectData):
            global CROP_STATE
            state = {'center': evt.index, 'size': crop_size}
            CROP_STATE = state
            return state

        def reset_crop():
            global CROP_STATE
            CROP_STATE = None
            return None

        async def apply_camera_settings(iso, shutter, aperture, wb):
            if cam is None:
                return "Camera not available."
            settings = {
                "main.imgsettings.iso": iso,
                "main.capturesettings.shutterspeed": shutter,
                "main.capturesettings.aperture": aperture,
                "main.imgsettings.whitebalance": wb,
            }
            try:
                await asyncio.to_thread(cam.apply_settings, settings)
                return "Settings applied."
            except CameraError as e:
                return f"Error: {e}"

        def export_camera_settings(iso, shutter, aperture, wb):
            data = {
                "camera": {
                    "main.imgsettings.iso": iso,
                    "main.capturesettings.shutterspeed": shutter,
                    "main.capturesettings.aperture": aperture,
                    "main.imgsettings.whitebalance": wb,
                }
            }
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as fh:
                yaml.safe_dump(data, fh)
                return fh.name

        live_image.select(set_crop, [crop_size_slider], crop_state_val)
        reset_crop_btn.click(reset_crop, None, crop_state_val)
        apply_settings_btn.click(
            apply_camera_settings,
            [iso_dropdown, shutter_dropdown, aperture_dropdown, wb_dropdown],
            settings_status,
        )
        export_btn.click(
            export_camera_settings,
            [iso_dropdown, shutter_dropdown, aperture_dropdown, wb_dropdown],
            export_file,
        )

        async def live_view_stream():
            while True:
                global CROP_STATE
                frame = await get_live_frame(CROP_STATE)
                yield frame
                await asyncio.sleep(0.1)

        demo.load(live_view_stream, None, live_image)


    return demo

if __name__ == "__main__":
    # Initialize the camera before starting the UI
    asyncio.run(initialize_camera())

    # Create and launch the Gradio app
    app = create_gradio_interface()
    try:
        app.launch(server_name="0.0.0.0", server_port=8000)
    finally:
        # Ensure the camera is closed when the app is shut down
        asyncio.run(close_camera())
