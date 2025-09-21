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


async def close_tripod() -> None:
    """Close and clear the cached TripodController instance."""
    global tripod
    if tripod is not None:
        try:
            await asyncio.to_thread(tripod.close)
        finally:
            tripod = None



async def set_tripod_drivers(enable: bool, serial_port: str, microstep: int) -> tuple[str, bool]:
    """Enable or disable the tripod motor drivers."""
    try:
        controller = await get_tripod(serial_port, microstep)
        await asyncio.to_thread(controller.enable_drivers, enable)
        state = 'enabled' if enable else 'disabled'
        return f'Tripod drivers {state}', True
    except Exception as exc:  # pragma: no cover - hardware dependent
        logger.error('Tripod driver toggle failed: %s', exc)
        return f'Tripod error: {exc}', False

async def move_tripod_to(pan: float, tilt: float, serial_port: str, microstep: int) -> str:
    """Move tripod to the requested relative pan/tilt angles."""
    try:
        t = await get_tripod(serial_port, microstep)
        await asyncio.to_thread(t.move_to_blocking, pan_deg=pan, tilt_deg=tilt)
        return "Tripod moved"
    except Exception as exc:  # pragma: no cover - hardware dependent
        logger.error("Tripod move failed: %s", exc)
        return f"Tripod error: {exc}"

async def get_tripod_status(serial_port: str, microstep: int) -> str:
    try:
        controller = await get_tripod(serial_port, microstep)
        pan, tilt, drivers = await asyncio.to_thread(controller.status)
        drivers_state = "enabled" if drivers else "disabled"
        return f"Pan {pan:.2f} deg, Tilt {tilt:.2f} deg, drivers {drivers_state}"
    except Exception as exc:  # pragma: no cover - hardware dependent
        logger.error("Tripod status query failed: %s", exc)
        return f"Tripod error: {exc}"

async def set_tripod_microstep(microstep: int, serial_port: str) -> str:
    try:
        controller = await get_tripod(serial_port, microstep)
        await asyncio.to_thread(controller.set_microstep, microstep)
        return f"Tripod microstep set to {microstep}"
    except ValueError as exc:
        return f"Tripod error: {exc}"
    except Exception as exc:  # pragma: no cover - hardware dependent
        logger.error("Tripod microstep update failed: %s", exc)
        return f"Tripod error: {exc}"

