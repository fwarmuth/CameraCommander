"""Gradio based UI for building timelapse configuration files.

The interface allows users to interactively tweak camera focus, apply capture
settings and export the resulting configuration for later use.  It mirrors the
options used by :class:`timelapse.TimelapseSession` so that prototypes closely
match full timelapse runs.
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict

import gradio as gr

from .camera import focus_camera, get_live_frame
from .config import build_settings, export_settings, run_prototype_timelapse
from .tripod import move_tripod_to

# Global crop selection used by ``get_live_frame``
CROP_STATE: Dict[str, Any] | None = None


def create_gradio_interface() -> gr.Blocks:
    """Create and return the Gradio interface."""
    # Limit the height of the configuration panel so that expanding multiple
    # accordions does not push the live view off screen. The panel becomes
    # scrollable instead, keeping the live preview in view.
    css = ".config-scroll{overflow-y:auto; max-height:600px;}"
    with gr.Blocks(css=css) as demo:
        # Store current crop rectangle between interactions
        crop_state_val = gr.State(None)

        # Title reflects the new focus of the tool – building timelapse configs
        gr.Markdown("# Camera Commander - Timelapse Config Builder")

        # Fetch current camera settings to populate UI choices
        iso_choices: list[Any] = []
        shutter_choices: list[Any] = []
        aperture_choices: list[Any] = []
        wb_choices: list[Any] = []
        iso_val = shutter_val = aperture_val = wb_val = None
        from .camera import cam  # imported lazily to avoid circular import during startup
        if cam:
            current: Dict[str, Dict[str, Any]] = cam.query_settings()

            def _get(key: str):
                return current.get(key, {})

            iso_entry = _get("main.imgsettings.iso")
            shutter_entry = _get("main.capturesettings.shutterspeed")
            aperture_entry = _get("main.capturesettings.aperture")
            wb_entry = _get("main.imgsettings.whitebalance")

            # Available options and currently selected values
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
                crop_size_slider = gr.Slider(
                    minimum=50, maximum=500, step=10, value=200, label="Crop Size"
                )
                reset_crop_btn = gr.Button("Reset Crop")

            with gr.Column():
                # All configuration accordions live in a scrollable column so the
                # live view remains visible even when many options are expanded.
                with gr.Column(elem_classes="config-scroll"):
                    with gr.Accordion("Focus Control", open=False):
                        focus_in_btn = gr.Button("Focus In/Near")
                        focus_out_btn = gr.Button("Focus Out/Far")
                        step_size_slider = gr.Slider(
                            minimum=1, maximum=3, step=1, value=3, label="Step Size"
                        )
                        focus_status = gr.Textbox(label="Focus Status")

                    with gr.Accordion("Camera Settings", open=False):
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
                        snapshot_btn = gr.Button("Take Snapshot")
                        settings_status = gr.Textbox(label="Settings Status")
                        snapshot_image = gr.Image(label="Snapshot", type="filepath")
                        snapshot_status = gr.Textbox(label="Snapshot Status")

                    with gr.Accordion("Tripod Settings", open=False):
                        serial_port_input = gr.Textbox(
                            value="/dev/ttyUSB0", label="Serial Port"
                        )
                        microstep_input = gr.Dropdown(
                            [1, 2, 4, 8, 16], value=16, label="Microstep"
                        )
                        start_pan_input = gr.Number(value=0.0, label="Start Pan (°)")
                        start_tilt_input = gr.Number(value=0.0, label="Start Tilt (°)")
                        end_pan_input = gr.Number(value=0.0, label="End Pan (°)")
                        end_tilt_input = gr.Number(value=0.0, label="End Tilt (°)")
                        go_start_btn = gr.Button("Go to Start Position")
                        go_end_btn = gr.Button("Go to End Position")
                        tripod_status = gr.Textbox(label="Tripod Status")

                    with gr.Accordion("Timelapse Settings", open=False):
                        total_frames_input = gr.Number(value=10, label="Total Frames")
                        interval_input = gr.Number(value=1.5, label="Interval (s)")
                        settle_input = gr.Number(value=0.3, label="Settle Time (s)")
                        output_dir_input = gr.Textbox(
                            value="./output", label="Output Dir"
                        )
                        video_fps_input = gr.Number(value=25, label="Video FPS")

                    with gr.Accordion("Prototype Timelapse", open=False):
                        proto_frames_input = gr.Number(
                            value=5, label="Prototype Frames"
                        )
                        run_proto_btn = gr.Button("Run Prototype")
                        proto_gallery = gr.Gallery(label="Prototype Frames")
                        proto_status = gr.Textbox(label="Prototype Status")

                # Export options apply to the entire configuration and sit
                # outside any specific accordion.
                export_btn = gr.Button("Export Settings")
                export_file = gr.File(label="Settings YAML")

        # Focus helpers ----------------------------------------------------
        async def focus_in_handler(step: int):
            return await focus_camera("near", step)

        async def focus_out_handler(step: int):
            return await focus_camera("far", step)

        focus_in_btn.click(fn=focus_in_handler, inputs=step_size_slider, outputs=focus_status)
        focus_out_btn.click(fn=focus_out_handler, inputs=step_size_slider, outputs=focus_status)

        def set_crop(crop_size: int, evt: gr.SelectData):
            global CROP_STATE
            # Store the selected crop centre + size so the live view generator
            # can return a zoomed region on subsequent updates
            state = {"center": evt.index, "size": crop_size}
            CROP_STATE = state
            return state

        def reset_crop():
            global CROP_STATE
            CROP_STATE = None
            return None

        async def apply_camera_settings(iso, shutter, aperture, wb):
            from .camera import cam
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
            except Exception as exc:  # pragma: no cover - hardware dependent
                return f"Error: {exc}"

        async def snapshot_handler(iso, shutter, aperture, wb):
            from .camera import cam, camera_lock
            if cam is None:
                return None, "Camera not available."
            settings = {
                "main.imgsettings.iso": iso,
                "main.capturesettings.shutterspeed": shutter,
                "main.capturesettings.aperture": aperture,
                "main.imgsettings.whitebalance": wb,
            }
            async with camera_lock:
                try:
                    await asyncio.to_thread(cam.apply_settings, settings)
                    await asyncio.to_thread(
                        cam.apply_settings, {"main.actions.viewfinder": 0}
                    )
                    path = Path(tempfile.gettempdir()) / "snapshot.jpg"
                    await asyncio.to_thread(cam.capture_image, dest=path)
                    return str(path), "Snapshot captured."
                except Exception as exc:  # pragma: no cover - hardware dependent
                    return None, f"Error: {exc}"
                finally:
                    try:
                        await asyncio.to_thread(
                            cam.apply_settings, {"main.actions.viewfinder": 1}
                        )
                    except Exception:  # pragma: no cover - hardware dependent
                        pass

        def export_settings_handler(
            iso,
            shutter,
            aperture,
            wb,
            serial_port,
            microstep,
            start_pan,
            start_tilt,
            end_pan,
            end_tilt,
            total_frames,
            interval,
            settle,
            output_dir,
            video_fps,
        ):
            # Assemble a settings mapping and serialise to YAML
            data = build_settings(
                iso,
                shutter,
                aperture,
                wb,
                serial_port,
                microstep,
                start_pan,
                start_tilt,
                end_pan,
                end_tilt,
                total_frames,
                interval,
                settle,
                output_dir,
                video_fps,
            )
            return export_settings(data)

        async def prototype_handler(
            iso,
            shutter,
            aperture,
            wb,
            serial_port,
            microstep,
            start_pan,
            start_tilt,
            end_pan,
            end_tilt,
            total_frames,
            interval,
            settle,
            output_dir,
            video_fps,
            proto_frames,
        ):
            # Build settings and run a short test timelapse using the real
            # TimelapseSession implementation
            cfg = build_settings(
                iso,
                shutter,
                aperture,
                wb,
                serial_port,
                microstep,
                start_pan,
                start_tilt,
                end_pan,
                end_tilt,
                total_frames,
                interval,
                settle,
                output_dir,
                video_fps,
            )
            return await run_prototype_timelapse(cfg, proto_frames)

        live_image.select(set_crop, [crop_size_slider], crop_state_val)
        reset_crop_btn.click(reset_crop, None, crop_state_val)
        apply_settings_btn.click(
            apply_camera_settings,
            [iso_dropdown, shutter_dropdown, aperture_dropdown, wb_dropdown],
            settings_status,
        )
        snapshot_btn.click(
            snapshot_handler,
            [iso_dropdown, shutter_dropdown, aperture_dropdown, wb_dropdown],
            [snapshot_image, snapshot_status],
        )
        go_start_btn.click(
            move_tripod_to,
            [start_pan_input, start_tilt_input, serial_port_input, microstep_input],
            tripod_status,
        )
        go_end_btn.click(
            move_tripod_to,
            [end_pan_input, end_tilt_input, serial_port_input, microstep_input],
            tripod_status,
        )
        export_btn.click(
            export_settings_handler,
            [
                iso_dropdown,
                shutter_dropdown,
                aperture_dropdown,
                wb_dropdown,
                serial_port_input,
                microstep_input,
                start_pan_input,
                start_tilt_input,
                end_pan_input,
                end_tilt_input,
                total_frames_input,
                interval_input,
                settle_input,
                output_dir_input,
                video_fps_input,
            ],
            export_file,
        )
        run_proto_btn.click(
            prototype_handler,
            [
                iso_dropdown,
                shutter_dropdown,
                aperture_dropdown,
                wb_dropdown,
                serial_port_input,
                microstep_input,
                start_pan_input,
                start_tilt_input,
                end_pan_input,
                end_tilt_input,
                total_frames_input,
                interval_input,
                settle_input,
                output_dir_input,
                video_fps_input,
                proto_frames_input,
            ],
            [proto_gallery, proto_status],
        )

        async def live_view_stream():
            while True:
                global CROP_STATE
                # Continuously fetch frames from the camera, applying the
                # current crop selection if one is set.
                frame = await get_live_frame(CROP_STATE)
                yield frame
                await asyncio.sleep(0.1)

        demo.load(live_view_stream, None, live_image)

    return demo
