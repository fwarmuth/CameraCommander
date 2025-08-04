import asyncio
import io
import logging
from typing import Any, Dict

import numpy as np
from PIL import Image

from camerawrapper import CameraWrapper, CameraError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global camera instance and lock
cam: CameraWrapper | None = None
camera_lock = asyncio.Lock()


async def initialize_camera() -> None:
    """Initializes the camera and enables the viewfinder."""
    global cam
    if cam is None:
        try:
            logger.info("Initializing camera...")
            cam = await asyncio.to_thread(CameraWrapper.select_camera, "Canon")
            await asyncio.to_thread(cam.apply_settings, {"main.actions.viewfinder": 1})
            logger.info("Camera initialized and viewfinder enabled.")
        except CameraError as exc:  # pragma: no cover - hardware dependent
            logger.error("Could not open camera: %s", exc)
            cam = None


async def close_camera() -> None:
    """Disables the viewfinder and releases the camera."""
    global cam
    if cam:
        try:
            logger.info("Disabling viewfinder and shutting down camera...")
            await asyncio.to_thread(cam.apply_settings, {"main.actions.viewfinder": 0})
            await asyncio.to_thread(cam.__exit__, None, None, None)
            logger.info("Camera shut down.")
        except CameraError as exc:  # pragma: no cover - hardware dependent
            logger.error("Error closing camera: %s", exc)
        finally:
            cam = None


async def get_live_frame(crop_state: Dict[str, Any] | None = None) -> np.ndarray:
    """Capture a frame from the camera's live view."""
    if cam is None:
        return np.zeros((480, 640, 3), dtype=np.uint8)

    async with camera_lock:
        try:
            frame_data = await asyncio.to_thread(cam.capture_preview)
            image = Image.open(io.BytesIO(frame_data.getvalue()))
            original_size = image.size

            if crop_state and "center" in crop_state and "size" in crop_state:
                center_x, center_y = crop_state["center"]
                size = crop_state["size"]

                width, height = original_size
                left = max(0, center_x - size // 2)
                top = max(0, center_y - size // 2)
                right = min(width, center_x + size // 2)
                bottom = min(height, center_y + size // 2)

                if left < right and top < bottom:
                    cropped_image = image.crop((left, top, right, bottom))
                    image = cropped_image.resize(original_size, Image.NEAREST)

            return np.array(image)
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.error("Error capturing preview: %s", exc)
            return np.zeros((480, 640, 3), dtype=np.uint8)


async def focus_camera(direction: str, step_size: int) -> str:
    """Adjust the camera focus."""
    if cam is None:
        logger.warning("Focus adjustment attempted but camera is not available.")
        return "Camera not available."

    async with camera_lock:
        try:
            logger.info("Adjusting focus: %s %s", direction, step_size)
            await asyncio.to_thread(cam.focus_step, direction, step_size, live_view=True)
            return f"Focus adjusted: {direction}, Step: {step_size}"
        except CameraError as exc:  # pragma: no cover - hardware dependent
            logger.error("Error setting focus: %s", exc)
            return f"Error: {exc}"
