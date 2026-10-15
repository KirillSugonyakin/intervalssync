"""Exercise activity persistence through the GUI's sync button."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft
import pytest

from intervalssync.gui import config as config_module, sync_view
from intervalssync.igpsport import core, workout
from intervalssync import intervals_icu
from intervalssync.intervals_icu import ActivityIdentities, ActivityUploadResult


def _walk(control):
    yield control
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)
    for child in getattr(control, "controls", []):
        yield from _walk(child)


def _click_sync(config, *, workouts=False):
    page = SimpleNamespace(
        platform=ft.PagePlatform.WINDOWS,
        theme_mode=ft.ThemeMode.LIGHT,
        update=Mock(),
        run_thread=lambda fn, *args: fn(*args),
    )
    store = SimpleNamespace(get=AsyncMock(return_value="offline-test-value"))
    view = sync_view.build_sync_view(page, config, store)
    button_type = ft.OutlinedButton if workouts else ft.FilledButton
    button = next(c for c in _walk(view) if isinstance(c, button_type))
    asyncio.run(button.on_click(None))
    return "\n".join(str(c.value) for c in _walk(view) if isinstance(c, ft.Text))


@pytest.fixture
def saved_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(sync_view, "get_dropbox_app_key", lambda: None)
    config = config_module.AppConfig(
        igp_user="offline-test", download_dir=str(tmp_path), stats_seeded=True
    )
    config_module.save(config)
    return config


def test_gui_workouts_keep_identity_across_restart_and_event_replacement(saved_config, monkeypatch):
    event = intervals_icu.CalendarWorkout(
        event_id=42, name="Synthetic workout", description="", activity_type="Ride",
        workout_doc={"steps": [{"duration": 60}]}, external_id="plan-42",
        oauth_client_id="provider", start_date_local="2026-09-10",
    )
    monkeypatch.setattr(workout, "login", lambda *a, **k: {})
    remote = set()
    monkeypatch.setattr(workout, "fetch_all_custom_workout_ids", lambda *a, **k: set(remote))
    monkeypatch.setattr(intervals_icu, "fetch_calendar_workouts", lambda *a, **k: [event])
    calls = []
    def upload(session, headers, body, **kwargs):
        calls.append(body)
        remote.add(100)
        return 100
    monkeypatch.setattr(workout, "upload_custom_workout", upload)

    assert "uploaded 1" in _click_sync(saved_config, workouts=True)
    reloaded = config_module.load()
    assert reloaded.workout_records
    assert "skipped 1" in _click_sync(reloaded, workouts=True)
    assert len(calls) == 1
    event.event_id = 43
    assert "updated 1" in _click_sync(config_module.load(), workouts=True)
    assert len(calls) == 2
    assert calls[-1]["data"]["id"] == "100"
    assert config_module.load().uploaded_workouts == {"43": 100}


def test_gui_remembers_link_after_restart(saved_config, monkeypatch):
    monkeypatch.setattr(core, "login", lambda *a: {})
    monkeypatch.setattr(core, "list_activities", lambda *a: [
        core.Activity(123, "Ride", "2026-09-01")
    ])
    monkeypatch.setattr(core, "fetch_activity_identities", lambda *a:
        ActivityIdentities({"existing-activity"}, {"original.fit": "existing-activity"})
    )
    resolve = Mock(return_value="https://offline.invalid/ride.fit")
    download = Mock(side_effect=lambda url, path: path.write_bytes(b"FIT"))
    upload = Mock(return_value=ActivityUploadResult("existing-activity", created=False))
    monkeypatch.setattr(core, "resolve_fit_url", resolve)
    monkeypatch.setattr(core, "download_fit", download)
    monkeypatch.setattr(core, "upload_to_intervals", upload)

    assert "linked 1" in _click_sync(saved_config)
    reloaded = config_module.load()
    assert reloaded.uploaded_activities == {"123": "existing-activity"}
    assert reloaded.lifetime_activities_uploaded == 0

    assert "skipped 1" in _click_sync(reloaded)
    assert resolve.call_count == download.call_count == upload.call_count == 1


@pytest.mark.parametrize("new_links", [{}, {"2": "existing-2"}])
def test_gui_saves_pruning_and_links_when_sync_returns_failures(
    saved_config, monkeypatch, new_links
):
    saved_config.uploaded_activities = {"1": "deleted", "3": "keep"}
    monkeypatch.setattr(sync_view, "igpsport_sync", lambda *a, **kw:
        core.SyncResult(failed=1, activity_map=new_links, pruned_keys=["1"])
    )

    assert "failed 1" in _click_sync(saved_config)
    assert config_module.load().uploaded_activities == {"3": "keep", **new_links}
