"""Command line interface for Camera Commander.

Provides camera snapshots, tripod control, timelapse sessions and a Gradio
configuration UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import click
import yaml

from camerawrapper import CameraWrapper, CameraError
from tripodwrapper import TripodController
from timelapse import TimelapseSession, TimelapseError
from advanced_live_view import timelapse_config_ui


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Return mapping parsed from YAML *path*."""
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise click.ClickException("Config file must define a mapping")
    return data


@click.group()
def cli() -> None:
    """Camera Commander command line interface."""


@cli.command()
@click.argument("config", type=click.Path(exists=True, path_type=Path))
@click.argument("output", type=click.Path(path_type=Path))
def snapshot(config: Path, output: Path) -> None:
    """Capture image defined by CONFIG and store at OUTPUT."""
    cfg = _load_yaml(config)
    cam_cfg = cfg.get("camera", cfg).copy()
    model_sub = cam_cfg.pop("model_substring", None)

    try:
        if model_sub:
            camera = CameraWrapper.select_camera(model_sub)
        else:
            discovered = CameraWrapper.discover_cameras()
            if not discovered:
                raise click.ClickException("No USB camera detected")
            model, port = discovered[0].rsplit(" (", 1)
            port = port.rstrip(")")
            camera = CameraWrapper(model, port)
    except CameraError as exc:  # pragma: no cover - hardware dependent
        raise click.ClickException(str(exc)) from exc

    with camera:
        if cam_cfg:
            camera.apply_settings(cam_cfg)
        output.parent.mkdir(parents=True, exist_ok=True)
        camera.capture_image(output)
    click.echo(f"Saved snapshot to {output}")


@cli.command()
@click.argument("config", type=click.Path(exists=True, path_type=Path))
def tripod(config: Path) -> None:
    """Interactively move tripod using CONFIG."""
    cfg = _load_yaml(config)
    trip_cfg = cfg.get("tripod", cfg)
    controller = TripodController(trip_cfg)
    click.echo("Enter 'pan tilt' degrees, e.g. '10 -5', or 'q' to quit.")
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
                click.echo("Please enter two numbers or 'q' to quit.")
                continue
            controller.move_blocking(pan, tilt)
            click.echo(f"Moved pan {pan}° tilt {tilt}°")
    finally:
        controller.enable_drivers(False)
        controller.close()


@cli.command()
@click.argument("config", type=click.Path(exists=True, path_type=Path))
def timelapse(config: Path) -> None:
    """Run timelapse session from CONFIG."""
    session = TimelapseSession(config)

    def _progress(done: int, total: int) -> None:
        pct = done / total * 100
        click.echo(f"\rCaptured {done}/{total} frames ({pct:5.1f} %)", nl=False)

    try:
        video = session.run(_progress)
    except TimelapseError as exc:  # pragma: no cover - hardware dependent
        raise click.ClickException(str(exc)) from exc
    click.echo(f"\nDone. Video at {video}")


@cli.command(name="ui")
@click.option("--share", is_flag=True, help="Enable public sharing")
def launch_ui(share: bool) -> None:
    """Launch Gradio configuration builder."""
    demo = timelapse_config_ui.create_gradio_interface()
    demo.launch(share=share)


if __name__ == "__main__":  # pragma: no cover
    cli()
