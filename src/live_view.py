

import asyncio
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from camerawrapper import CameraWrapper, CameraError

logger = logging.getLogger(__name__)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="static")

cam: CameraWrapper = None
camera_lock = asyncio.Lock()

async def gen_live_view():
    """Generates live view frames from the camera."""
    while True:
        async with camera_lock:
            try:
                # Run the blocking capture in a thread to not block the event loop
                frame_data = await asyncio.to_thread(cam.capture_preview)
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_data.getvalue() + b'\r\n')
            except Exception as e:
                logger.error(f"Error capturing preview: {e}")
                # Avoid busy-looping on continuous errors
                await asyncio.sleep(1)
        # Lower sleep time for higher FPS, but be mindful of CPU usage.
        await asyncio.sleep(0.05)


@app.on_event("startup")
async def startup():
    """Initializes the camera on application startup."""
    global cam
    try:
        # Camera initialization is blocking, run in a thread
        cam = await asyncio.to_thread(CameraWrapper.select_camera, "Canon")
        # Enable viewfinder for live preview
        await asyncio.to_thread(cam.apply_settings, {"main.actions.viewfinder": 1})
        logger.info("Camera initialized and viewfinder enabled.")
    except CameraError as e:
        logger.error(f"Could not open camera: {e}")
        cam = None  # Ensure cam is None if initialization fails

@app.on_event("shutdown")
async def shutdown():
    """Cleans up camera resources on application shutdown."""
    if cam:
        logger.info("Shutting down camera.")
        # Camera operations are blocking, run in a thread
        await asyncio.to_thread(cam.apply_settings, {"main.actions.viewfinder": 0})
        await asyncio.to_thread(cam.__exit__, None, None, None)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serves the main HTML page."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/liveview")
async def live_view():
    """Streams the live view from the camera."""
    if cam is None:
        return HTMLResponse("Camera not available. Please check logs.", status_code=503)
    return StreamingResponse(gen_live_view(),
                             media_type='multipart/x-mixed-replace; boundary=frame')

@app.post("/focus")
async def focus(direction: str = Form(...), step_size: int = Form(...)):
    """Adjusts the camera focus."""
    if cam is None:
        return {"status": "error", "message": "Camera not available."}

    async with camera_lock:
        try:
            logger.info(f"Adjusting focus: {direction} {step_size}")
            # Focus step is blocking, run in a thread
            await asyncio.to_thread(cam.focus_step, direction, step_size, live_view=True)
        except CameraError as e:
            logger.error(f"Error setting focus: {e}")
            return {"status": "error", "message": str(e)}
    return {"status": "ok"}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000)

