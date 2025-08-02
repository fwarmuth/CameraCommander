from camerawrapper import CameraWrapper
from tripodwrapper import TripodController
from timelapse import TimelapseSession, TimelapseError
import sys
from pathlib import Path
import time
import logging

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------- #
# Demonstration
# ------------------------------------------------------------------------- #
if __name__ == "__main__":
    # 1. Discover cameras
    cams = CameraWrapper.discover_cameras()
    print("Discovered:", cams)

    # 2. Pick the first Canon body found
    cam = CameraWrapper.select_camera("canon")
    print("Selected:", cam._model)


    # Test manual focus:
    def focus_step(cam, direction="near", size=1):
        """
        direction = 'near' | 'far'
        size      = 1, 2 or 3   (larger = bigger rotation)
        """
        if direction not in ("near", "far"):
            raise ValueError("direction must be 'near' or 'far'")
        if size not in (1, 2, 3):
            raise ValueError("size must be 1, 2 or 3")
        label = f"{direction.capitalize()} {size}"
        cam.apply_settings({"main.actions.manualfocusdrive": label})
    pass
    cam.apply_settings({"main.actions.viewfinder": "0"})
    cam.capture_image(Path("focus_test/base_iso_auto.jpg"))
    for i in range(10):
        cam.apply_settings({"main.actions.viewfinder": "1"})
        time.sleep(0.1)
        focus_step(cam, "near", 3)
        cam.apply_settings({"main.actions.viewfinder": "0"})
        time.sleep(0.2)
        cam.capture_image(Path(f"focus_test/near_{i}.jpg"))

        

    # 3. Change ISO then capture
    print("Current ISO:", cam.get_current_settings().get("main.imgsettings.iso", "?"))
    img_path = cam.capture_image(Path("aaaa/iso_auto.jpg"))
    cam.apply_settings({"main.imgsettings.iso": "100"})
    img_path = cam.capture_image(Path("aaaa/iso_100.jpg"))

    # auto adjust
    cam.apply_settings({
                        "main.imgsettings.iso": "800",
                        "main.capturesettings.aperture": "5.6",
                        "main.capturesettings.shutterspeed": "1/80"
                        })
    cam.capture_image(Path("aaaa/auto_adjust.jpg"))


    # # Keep script alive a moment for async demo
    # time.sleep(10)
    
    # from tripodwrapper import TripodController, configure_logging
    # configure_logging(logging.DEBUG)
    # tripod = TripodController({"port": "/dev/ttyUSB0", "baudrate": 9600})
    # tripod.enable_drivers(True)
    # tripod.set_microstep(8)
    # tripod.move(15,-5)
    # while tripod.query_busy():   # poll firmware “Q”
    #     pass
    # tripod.set_microstep(16)
    # tripod.move_blocking(-15,5)
    # print(tripod.position)
    # tripod.close()
    

    # if len(sys.argv) != 2:
    #     print("Usage: python timelapse_session.py <config.yml|json>")
    #     sys.exit(1)

    # session = TimelapseSession(sys.argv[1])

    # def _progress(done: int, total: int) -> None:
    #     pct = done / total * 100
    #     print(f"\rCaptured {done}/{total} frames ({pct:5.1f} %)", end="", flush=True)

    # try:
    #     video = session.run(_progress)
    #     print(f"\nDone! Video at {video}")
    # except TimelapseError as exc:
    #     logger.error("Timelapse failed: %s", exc)
    #     sys.exit(2)


