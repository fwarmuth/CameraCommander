"""Lazy-loading command line interface for Camera Commander.

This refactor replaces the previous Click-based CLI with a Typer application
that only imports heavy modules when the corresponding subcommand is executed.
As a result simply invoking ``--help`` is fast and does not trigger any camera
or UI related imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import typer
import yaml


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Return mapping parsed from YAML ``path``.

    The helper performs minimal validation and exits with an error message if
    the document does not define a mapping.
    """

    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        typer.echo("Config file must define a mapping", err=True)
        raise typer.Exit(code=1)
    return data


app = typer.Typer(help="Camera Commander command line interface.")


@app.command()
def snapshot(config: Path, output: Path) -> None:
    """Capture image defined by CONFIG and store at OUTPUT."""

    # Import the camera wrapper only when the command is invoked. Importing it
    # eagerly pulls in ``gphoto2`` which is expensive and unnecessary when, for
    # example, the user only requests ``--help``.
    from camerawrapper import CameraWrapper, CameraError

    cfg = _load_yaml(config)
    cam_cfg = cfg.get("camera", cfg).copy()
    model_sub = cam_cfg.pop("model_substring", None)

    try:
        if model_sub:
            camera = CameraWrapper.select_camera(model_sub)
        else:
            discovered = CameraWrapper.discover_cameras()
            if not discovered:
                typer.echo("No USB camera detected", err=True)
                raise typer.Exit(code=1)
            model, port = discovered[0].rsplit(" (", 1)
            port = port.rstrip(")")
            camera = CameraWrapper(model, port)
    except CameraError as exc:  # pragma: no cover - hardware dependent
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    with camera:
        if cam_cfg:
            camera.apply_settings(cam_cfg)
        output.parent.mkdir(parents=True, exist_ok=True)
        camera.capture_image(output)
    typer.echo(f"Saved snapshot to {output}")


@app.command()
def tripod(config: Path) -> None:
    """Interactively move tripod using CONFIG."""

    # ``TripodController`` talks to serial hardware and has non-trivial import
    # cost. Delay the import until needed.
    from tripodwrapper import TripodController

    cfg = _load_yaml(config)
    trip_cfg = cfg.get("tripod", cfg)
    controller = TripodController(trip_cfg)
    typer.echo("Enter 'pan tilt' degrees, e.g. '10 -5', or 'q' to quit.")
    try:
        while True:
            cmd = input("pan tilt> ").strip()
            if cmd.lower() in {"q", "quit", "exit"}:
                break
            try:
                pan_str, tilt_str = cmd.split()
                pan = float(pan_str)
                tilt = float(tilt_str)
            except ValueError:
                typer.echo("Please enter two numbers or 'q' to quit.")
                continue
            controller.move_blocking(pan, tilt)
            typer.echo(f"Moved pan {pan}° tilt {tilt}°")
    finally:
        controller.enable_drivers(False)
        controller.close()


@app.command()
def timelapse(config: Path) -> None:
    """Run timelapse session from CONFIG."""

    # ``TimelapseSession`` depends on both camera and tripod wrappers; importing
    # it lazily keeps CLI startup lean.
    from timelapse import TimelapseSession, TimelapseError

    session = TimelapseSession(config)

    def _progress(done: int, total: int) -> None:
        pct = done / total * 100
        typer.echo(f"\rCaptured {done}/{total} frames ({pct:5.1f} %)", nl=False)

    try:
        video = session.run(_progress)
    except TimelapseError as exc:  # pragma: no cover - hardware dependent
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    frame_dir = session.output_dir.resolve()
    if video is None:
        typer.echo("\nDone.")
    else:
        typer.echo(f"\nDone. Video at {video}")
    typer.echo(f"Frames at {frame_dir}")


@app.command(name="ui")
def launch_ui(share: bool = typer.Option(False, help="Enable public sharing")) -> None:
    """Launch Gradio configuration builder."""

    # Importing ``advanced_live_view`` pulls in Gradio which is large. Only load
    # it when the UI command is executed.
    from advanced_live_view import timelapse_config_ui

    demo = timelapse_config_ui.create_gradio_interface()
    demo.launch(share=share)


def main() -> None:  # pragma: no cover - thin wrapper for entry points
    """Entry point used by ``python -m cli`` for convenience."""

    app()


if __name__ == "__main__":  # pragma: no cover
    main()

