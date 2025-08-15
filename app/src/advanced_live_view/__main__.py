import asyncio

from advanced_live_view.camera import close_camera, initialize_camera
from advanced_live_view.tripod import tripod
from advanced_live_view.timelapse_config_ui import create_gradio_interface


def main(share: bool = False) -> None:
    """Run the advanced live view Gradio UI.

    Parameters
    ----------
    share:
        If *True* Gradio will create a publicly shareable link. The option is
        primarily used when invoking the UI from the CLI. Defaults to *False*
        which keeps the interface local only.
    """

    asyncio.run(initialize_camera())
    app = create_gradio_interface()
    try:
        app.launch(server_name="0.0.0.0", server_port=8000, share=share)
    finally:
        asyncio.run(close_camera())
        if tripod:
            tripod.close()


if __name__ == "__main__":
    main()
