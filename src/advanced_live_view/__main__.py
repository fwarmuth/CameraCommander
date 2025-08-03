import asyncio

from advanced_live_view.camera import close_camera, initialize_camera
from advanced_live_view.tripod import tripod
from advanced_live_view.timelapse_config_ui import create_gradio_interface


def main() -> None:
    asyncio.run(initialize_camera())
    app = create_gradio_interface()
    try:
        app.launch(server_name="0.0.0.0", server_port=8000)
    finally:
        asyncio.run(close_camera())
        if tripod:
            tripod.close()


if __name__ == "__main__":
    main()
