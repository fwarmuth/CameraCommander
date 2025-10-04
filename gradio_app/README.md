# Gradio application overview

The `gradio_app` package contains everything that backs the in-progress Gradio workflow for CameraCommander. The modules are organised around a few key responsibilities:

- **State container** — [`state.py`](state.py) defines `AppState`, a context-bound object that exposes the shared service layer (hardware resource manager, timelapse job runner, and session repository). Gradio callbacks use `AppState.current()` to grab dependencies instead of importing singletons.
- **Services** — [`services/`](services/) contains adapters for hardware and long-running jobs. Notable pieces are `AsyncResourceManager`, `CameraAdapter`, `TripodAdapter`, and the timelapse runner glue that turns planner output into capture jobs.
- **Store** — [`store/`](store/) currently ships with `SessionRepository`, a filesystem-backed catalogue that persists completed captures under `~/.cameracommander/recordings`. It also publishes change notifications so the UI can refresh when new sessions land.
- **UI tabs** — [`ui/`](ui/) houses tab renderers that plug into the shared Gradio `Blocks` instance:
  - `live_control.py` for camera focus/exposure helpers and manual tripod nudges.
  - `planner.py` to configure timelapse jobs and feed them to the runner.
  - `session_monitor.py` to watch in-flight jobs and surface hardware lock state.
  - `library.py` for browsing stored sessions and reloading their settings.

## Launching the Gradio entry point

The package exposes a module entry point that brings up Gradio with sensible defaults:

```bash
python -m gradio_app
```

This resolves the app state, builds the UI layout, enables the background queue, and launches the server on `0.0.0.0:7860`. Shutdown signals are forwarded so hardware sessions and background tasks are cleaned up before exit.

### Using `uv` for local runs

If you prefer ephemeral environments, `uv` can materialise the dependencies declared in `pyproject.toml` and execute the module without polluting your global interpreter:

```bash
uv run python -m gradio_app
```

Because the project is marked as an installable package, `uv run` exposes both the module (`python -m gradio_app`) and the console script (`gradiocommander`).

### Bootstrapping a fresh deployment with `uv`

For a longer-lived environment (for example, a remote host or brand-new workstation), `uv` can manage the virtual environment and install the package in editable mode:

```bash
git clone https://github.com/<your-org>/CameraCommander.git
cd CameraCommander/gradio_app
uv sync
source .venv/bin/activate
gradiocommander
```

`uv sync` creates a local `.venv`, installs the declared dependencies, and links the `gradio_app` package so the `gradiocommander` command is immediately available. When you are done, deactivate the environment with `deactivate`.

## Hardware configuration

`AsyncResourceManager` lazily instantiates the camera and tripod adapters the first time a callback requests them. Before driving the tripod tab, make sure a factory has been registered:

- Call `resources.configure_tripod(lambda: TripodAdapter({...}))` with serial port and driver defaults that match your firmware build. Passing `None` disables tripod controls until a new factory is installed.
- Override `resources.configure_camera(custom_factory)` if you have a mocked or pre-configured `CameraAdapter` implementation; otherwise autodetection falls back to `gphoto2`.

These factories can be injected from a small bootstrap script (for example, before you hand `AppState` to `build_application`) or from diagnostics tabs that prompt the user for hardware info.

## Session persistence

When the timelapse runner marks a job as complete, the associated `RecordingSummary`, `RecordingSettings`, and generated artefacts are registered with `SessionRepository`. Each session lands inside `~/.cameracommander/recordings/<session-id>/` and includes:

- `metadata.json` — summary of the run plus relative asset paths.
- `settings.json` — capture settings cloned from the planner (optional).
- `output/` — copied or linked media artefacts (frames, assembled videos, logs).

The repository triggers change listeners whenever it mutates, allowing the Library tab to refresh without manual reloads.

## Linking documentation

See the [root README](../README.md) for the broader CameraCommander architecture and CLI workflow. Conversely, the README now points back here so contributors can quickly discover the Gradio-specific components.
