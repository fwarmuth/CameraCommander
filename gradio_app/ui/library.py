"""Library tab surfacing completed timelapse sessions."""

from __future__ import annotations

import asyncio
import uuid
import zipfile
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, List, Optional, Sequence, Tuple

import gradio as gr

from ..models import RecordingAssetType
from ..state import AppState
from ..store.session_repository import StoredSession
from .utils import unwrap_app_state

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def render_tab(
    shared_app_state: gr.State, planner_clone_state: Optional[gr.State] = None
) -> gr.Blocks:
    """Render the Library tab contents."""

    if planner_clone_state is None:
        planner_clone_state = gr.State(value=None)

    selected_session_state = gr.State(value=None)

    with gr.Blocks() as tab:
        gr.Markdown("## Library")

        with gr.Row():
            with gr.Column(scale=1, min_width=280):
                session_overview = gr.Markdown("No sessions have been ingested yet.")
                session_dropdown = gr.Dropdown(
                    label="Stored Sessions",
                    choices=[],
                    value=None,
                    allow_custom_value=False,
                )
                refresh_button = gr.Button("Refresh Sessions", variant="secondary")
            with gr.Column(scale=2):
                summary_markdown = gr.Markdown("Select a session to view details.")
                assets_markdown = gr.Markdown("")
                video_player = gr.Video(
                    label="Rendered Video",
                    interactive=False,
                    visible=False,
                )
                gallery_component = gr.Gallery(
                    label="Captured Frames",
                    value=[],
                    columns=[4],
                    height=320,
                    allow_preview=True,
                    visible=False,
                )
                archive_file = gr.File(
                    label="Session Archive",
                    interactive=False,
                    visible=False,
                )
                with gr.Row():
                    archive_button = gr.Button(
                        "Prepare Archive Download", variant="secondary"
                    )
                    clone_button = gr.Button(
                        "Clone Settings to Planner", variant="secondary"
                    )
                    delete_button = gr.Button("Delete Session", variant="stop")
                status_message = gr.Markdown("")

        tab.load(
            _initialise_library,
            inputs=[shared_app_state, selected_session_state],
            outputs=[
                session_dropdown,
                session_overview,
                selected_session_state,
                summary_markdown,
                assets_markdown,
                video_player,
                gallery_component,
                archive_file,
                status_message,
            ],
        )

        refresh_button.click(
            _refresh_library,
            inputs=[shared_app_state, selected_session_state],
            outputs=[
                session_dropdown,
                session_overview,
                selected_session_state,
                summary_markdown,
                assets_markdown,
                video_player,
                gallery_component,
                archive_file,
                status_message,
            ],
        )

        session_dropdown.change(
            _select_session,
            inputs=[shared_app_state, session_dropdown],
            outputs=[
                selected_session_state,
                summary_markdown,
                assets_markdown,
                video_player,
                gallery_component,
                archive_file,
                status_message,
            ],
        )

        archive_button.click(
            _prepare_archive,
            inputs=[shared_app_state, selected_session_state],
            outputs=[archive_file, status_message],
        )

        clone_button.click(
            _clone_to_planner,
            inputs=[shared_app_state, selected_session_state],
            outputs=[planner_clone_state, status_message],
        )

        delete_button.click(
            _delete_session,
            inputs=[shared_app_state, selected_session_state],
            outputs=[
                session_dropdown,
                session_overview,
                selected_session_state,
                summary_markdown,
                assets_markdown,
                video_player,
                gallery_component,
                archive_file,
                status_message,
            ],
        )

        tab.load(
            _observe_repository_updates,
            inputs=[shared_app_state],
            outputs=[
                session_dropdown,
                session_overview,
                selected_session_state,
                summary_markdown,
                assets_markdown,
                video_player,
                gallery_component,
                archive_file,
                status_message,
            ],
        )

    return tab


async def _initialise_library(
    app_state_value: Any, current_selection: Optional[str]
) -> Tuple[
    Any,
    Any,
    Optional[str],
    Any,
    Any,
    Any,
    Any,
    Any,
    Any,
]:
    return await _refresh_library(app_state_value, current_selection)


async def _refresh_library(
    app_state_value: Any,
    selection_hint: Optional[str],
    *,
    keep_status: bool = False,
) -> Tuple[
    Any,
    Any,
    Optional[str],
    Any,
    Any,
    Any,
    Any,
    Any,
    Any,
]:
    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        sessions = await asyncio.to_thread(
            lambda: list(app_state.sessions.list_sessions())
        )

    session_ids = [session.summary.session_id for session in sessions]
    selection = _determine_selection(
        session_ids, selection_hint, app_state.library_selected_session
    )

    app_state.library_selected_session = selection

    dropdown_update = gr.Dropdown.update(
        choices=_format_dropdown_choices(sessions), value=selection
    )
    overview_update = gr.Markdown.update(value=_format_session_overview(sessions))

    session_lookup = {session.summary.session_id: session for session in sessions}
    stored = session_lookup.get(selection) if selection else None

    summary_update, assets_update, video_update, gallery_update = await asyncio.to_thread(
        _render_session_details, stored
    )

    archive_update = gr.File.update(value=None, visible=False)
    status_update = gr.Markdown.update() if keep_status else gr.Markdown.update(value="")

    return (
        dropdown_update,
        overview_update,
        selection,
        summary_update,
        assets_update,
        video_update,
        gallery_update,
        archive_update,
        status_update,
    )


async def _select_session(
    app_state_value: Any, session_id: Optional[str]
) -> Tuple[
    Optional[str],
    Any,
    Any,
    Any,
    Any,
    Any,
    Any,
]:
    app_state = unwrap_app_state(app_state_value)
    app_state.library_selected_session = session_id

    summary_update, assets_update, video_update, gallery_update = await _load_session_details(
        app_state_value, session_id
    )

    archive_update = gr.File.update(value=None, visible=False)
    status_update = gr.Markdown.update(value="" if session_id else "Select a session.")

    return (
        session_id,
        summary_update,
        assets_update,
        video_update,
        gallery_update,
        archive_update,
        status_update,
    )


async def _prepare_archive(
    app_state_value: Any, session_id: Optional[str]
) -> Tuple[Any, Any]:
    if not session_id:
        return (
            gr.File.update(value=None, visible=False),
            gr.Markdown.update(value="Select a session to prepare an archive."),
        )

    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        stored = await asyncio.to_thread(app_state.sessions.get_session, session_id)

    if stored is None:
        return (
            gr.File.update(value=None, visible=False),
            gr.Markdown.update(value=f"Session `{session_id}` not found."),
        )

    archive_path = await asyncio.to_thread(_create_archive, stored)

    return (
        gr.File.update(value=str(archive_path), visible=True),
        gr.Markdown.update(value=f"Archive ready for session `{session_id}`."),
    )


async def _clone_to_planner(
    app_state_value: Any, session_id: Optional[str]
) -> Tuple[Any, Any]:
    if not session_id:
        return (
            gr.update(),
            gr.Markdown.update(value="Select a session to clone into the planner."),
        )

    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        stored = await asyncio.to_thread(app_state.sessions.get_session, session_id)

    if stored is None or stored.settings is None:
        return (
            gr.update(),
            gr.Markdown.update(
                value=f"Session `{session_id}` does not include saved planner settings."
            ),
        )

    request = {"session_id": session_id, "nonce": uuid.uuid4().hex}
    return (
        request,
        gr.Markdown.update(value=f"Planner preset loaded from session `{session_id}`."),
    )


async def _delete_session(
    app_state_value: Any, session_id: Optional[str]
) -> Tuple[Any, ...]:
    if not session_id:
        return (
            gr.update(),
            gr.update(),
            session_id,
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.Markdown.update(value="Select a session to delete."),
        )

    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        try:
            await asyncio.to_thread(app_state.sessions.delete_session, session_id)
        except FileNotFoundError:
            message = f"Session `{session_id}` was already removed."
            deleted = False
        except Exception as exc:  # pragma: no cover - defensive guard
            message = f"Failed to delete session `{session_id}`: {exc}"
            deleted = False
        else:
            message = f"Deleted session `{session_id}`."
            deleted = True
            app_state.library_selected_session = None

    dropdown_update, overview_update, selection, summary_update, assets_update, video_update, gallery_update, archive_update, _ = await _refresh_library(
        app_state_value,
        None if deleted else session_id,
        keep_status=True,
    )

    status_update = gr.Markdown.update(value=message)

    return (
        dropdown_update,
        overview_update,
        selection,
        summary_update,
        assets_update,
        video_update,
        gallery_update,
        archive_update,
        status_update,
    )


async def _load_session_details(
    app_state_value: Any, session_id: Optional[str]
) -> Tuple[Any, Any, Any, Any]:
    if not session_id:
        return await asyncio.to_thread(_render_session_details, None)

    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        stored = await asyncio.to_thread(app_state.sessions.get_session, session_id)

    return await asyncio.to_thread(_render_session_details, stored)


async def _observe_repository_updates(
    app_state_value: Any,
) -> AsyncIterator[
    Tuple[
        Any,
        Any,
        Optional[str],
        Any,
        Any,
        Any,
        Any,
        Any,
        Any,
    ]
]:
    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        async for _ in app_state.subscribe_sessions():
            selection = app_state.library_selected_session
            payload = await _refresh_library(
                app_state_value, selection, keep_status=True
            )
            yield payload


def _render_session_details(
    stored: Optional[StoredSession],
) -> Tuple[Any, Any, Any, Any]:
    if stored is None:
        return (
            gr.Markdown.update(value="Select a session to view details."),
            gr.Markdown.update(value=""),
            gr.Video.update(value=None, visible=False),
            gr.Gallery.update(value=[], visible=False),
        )

    summary = stored.summary
    created = summary.created_at.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    duration = summary.duration_s or 0.0
    duration_minutes = duration / 60.0 if duration else None

    output_dir = Path(summary.output_dir)
    try:
        output_rel = output_dir.resolve().relative_to(stored.base_path.resolve())
        output_display = output_rel.as_posix() or "."
    except (ValueError, FileNotFoundError):
        output_display = output_dir.as_posix()

    heading = summary.plan_name or summary.session_id
    lines = [f"### {heading} (`{summary.session_id}`)"]
    lines.append(f"- Captured: {created}")
    lines.append(f"- Frames: {summary.total_frames}")
    if duration_minutes is not None:
        lines.append(f"- Duration: {duration_minutes:.1f} minutes")
    lines.append(f"- Output: `{output_display}`")
    if summary.tags:
        lines.append(f"- Tags: {', '.join(summary.tags)}")

    if summary.notes:
        note_lines = "\n> ".join(summary.notes.strip().splitlines())
        lines.append("")
        lines.append(f"> {note_lines}")

    assets_md = _format_assets_markdown(stored)
    video_update = _video_update(stored)
    gallery_items = _gather_gallery_items(stored)
    gallery_update = gr.Gallery.update(value=gallery_items, visible=bool(gallery_items))

    return (
        gr.Markdown.update(value="\n".join(lines)),
        gr.Markdown.update(value=assets_md),
        video_update,
        gallery_update,
    )


def _format_dropdown_choices(sessions: Sequence[StoredSession]) -> List[Tuple[str, str]]:
    choices: List[Tuple[str, str]] = []
    for stored in sessions:
        summary = stored.summary
        label = summary.plan_name or summary.session_id
        timestamp = summary.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
        display = f"{label} — {timestamp} ({summary.session_id})"
        choices.append((display, summary.session_id))
    return choices


def _format_session_overview(sessions: Sequence[StoredSession]) -> str:
    if not sessions:
        return "No sessions have been ingested yet."

    lines = ["**Stored Sessions**"]
    for stored in sessions:
        summary = stored.summary
        label = summary.plan_name or summary.session_id
        timestamp = summary.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"- **{label}** (`{summary.session_id}`) — {timestamp} — {summary.total_frames} frames"
        )
    return "\n".join(lines)


def _determine_selection(
    available: Sequence[str], *candidates: Optional[str]
) -> Optional[str]:
    for candidate in candidates:
        if candidate and candidate in available:
            return candidate
    return available[0] if available else None


def _format_assets_markdown(stored: StoredSession) -> str:
    lines = ["**Assets**"]
    base = stored.base_path.resolve()
    found = False

    for asset in stored.assets:
        path = Path(asset.path)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not _is_within_root(resolved, base):
            continue

        rel_path = _safe_relative(resolved, base)
        label = asset.label or asset.kind.value.title()
        description = asset.kind.value.title()
        if asset.size_bytes:
            description += f" — {_humanize_size(asset.size_bytes)}"

        lines.append(f"- **{label}** ({description}) — `{rel_path}`")
        found = True

    if not found:
        lines.append("- No artefacts recorded for this session.")

    return "\n".join(lines)


def _video_update(stored: StoredSession) -> Any:
    video_path = stored.summary.video_path
    if not video_path:
        return gr.Video.update(value=None, visible=False)

    path = Path(video_path)
    try:
        resolved = path.resolve()
    except OSError:
        return gr.Video.update(value=None, visible=False)

    if not resolved.is_file() or not _is_within_root(resolved, stored.base_path):
        return gr.Video.update(value=None, visible=False)

    return gr.Video.update(value=str(resolved), visible=True)


def _gather_gallery_items(stored: StoredSession, limit: int = 12) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    base = stored.base_path.resolve()

    for asset in stored.assets:
        if asset.kind is not RecordingAssetType.FRAME:
            continue

        path = Path(asset.path)
        try:
            resolved = path.resolve()
        except OSError:
            continue

        if not _is_within_root(resolved, base):
            continue

        if resolved.is_dir():
            for candidate in sorted(resolved.iterdir()):
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in _IMAGE_EXTENSIONS:
                    continue
                items.append((str(candidate), candidate.name))
                if len(items) >= limit:
                    return items
        elif resolved.is_file() and resolved.suffix.lower() in _IMAGE_EXTENSIONS:
            items.append((str(resolved), resolved.name))
            if len(items) >= limit:
                return items

    return items


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, FileNotFoundError):
        return False
    return True


def _safe_relative(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
        return rel.as_posix() or "."
    except ValueError:
        return path.name


def _humanize_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def _create_archive(stored: StoredSession) -> Path:
    base = stored.base_path.resolve()
    archive_path = base / f"{stored.summary.session_id}.zip"

    if archive_path.exists():
        archive_path.unlink()

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in base.rglob("*"):
            relative = path.relative_to(base)
            archive.write(path, arcname=relative.as_posix())

    return archive_path
