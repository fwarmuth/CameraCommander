
import asyncio
import logging
from camerawrapper import CameraWrapper, CameraError
import gradio as gr
import numpy as np
from PIL import Image
import io

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global camera instance and lock
cam: CameraWrapper = None
camera_lock = asyncio.Lock()

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

async def get_live_frame():
    """Captures a single frame from the camera's live view."""
    if cam is None:
        return np.zeros((480, 640, 3), dtype=np.uint8)  # Return a black image if camera is not available

    async with camera_lock:
        try:
            frame_data = await asyncio.to_thread(cam.capture_preview)
            image = Image.open(io.BytesIO(frame_data.getvalue()))
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
        gr.Markdown("# Camera Commander - Advanced Live View")

        with gr.Row():
            with gr.Column():
                live_image = gr.Image(label="Live View", type="numpy")
            with gr.Column():
                gr.Markdown("## Focus Control")
                focus_in_btn = gr.Button("Focus In/Near")
                focus_out_btn = gr.Button("Focus Out/Far")
                step_size_slider = gr.Slider(minimum=1, maximum=3, step=1, value=3, label="Step Size")
                focus_status = gr.Textbox(label="Focus Status")

        async def focus_in_handler(step):
            return await focus_camera('near', step)

        async def focus_out_handler(step):
            return await focus_camera('far', step)

        focus_in_btn.click(fn=focus_in_handler, inputs=step_size_slider, outputs=focus_status)
        focus_out_btn.click(fn=focus_out_handler, inputs=step_size_slider, outputs=focus_status)

        async def live_view_generator():
            while True:
                frame = await get_live_frame()
                yield frame
                await asyncio.sleep(0.1)

        # Setup live view streaming by returning a generator
        demo.load(live_view_generator, None, live_image)

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
