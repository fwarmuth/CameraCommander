import asyncio
import logging

from tripodwrapper import TripodController

logger = logging.getLogger(__name__)

# Cached tripod instance
tripod: TripodController | None = None


async def get_tripod(serial_port: str, microstep: int) -> TripodController:
    """Return a cached TripodController instance creating it on demand."""
    global tripod
    if tripod is None:
        cfg = {"serial": {"port": serial_port, "baudrate": 9600}, "microstep": microstep}
        tripod = await asyncio.to_thread(TripodController, cfg)
    return tripod


async def move_tripod_to(pan: float, tilt: float, serial_port: str, microstep: int) -> str:
    """Move tripod to the requested relative pan/tilt angles."""
    try:
        t = await get_tripod(serial_port, microstep)
        await asyncio.to_thread(t.move_to_blocking, pan_deg=pan, tilt_deg=tilt)
        return "Tripod moved"
    except Exception as exc:  # pragma: no cover - hardware dependent
        logger.error("Tripod move failed: %s", exc)
        return f"Tripod error: {exc}"
