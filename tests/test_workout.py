"""Tests for intervals.icu → iGPSPORT workout upload."""

from __future__ import annotations

from datetime import date

import pytest

from intervalssync import intervals_icu
from intervalssync.igpsport import workout


class FakeResponse:
    def __init__(self, *, status=200, json_data=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if not self.ok:
            raise workout.requests.HTTPError(f"HTTP {self.status_code}")


def test_icu_workout_doc_maps_simple_ftp_step():
    doc = {
        "duration": 3600,
        "steps": [
            {
                "duration": 3600,
                "power": {"value": 100, "units": "%ftp"},
            }
        ],
    }
    body = workout.icu_workout_doc_to_igps("FTP Test", "desc", doc)
    assert body is not None
    data = body["data"]
    assert data["title"] == "FTP Test"
    assert data["workoutType"] == "bike"
    assert data["sportBigType"] == 1
    assert data["totalTime"] == 3600
    step = data["structure"][0]
    assert step["type"] == "Step"
    assert step["length"] == {"unit": "Second", "value": 3600}
    assert step["intensityTarget"] == {
        "unit": "PercentOfFTP",
        "minValue": 100,
        "maxValue": 100,
    }


def test_icu_workout_doc_prefers_resolved_power_watts():
    doc = {
        "steps": [
            {
                "duration": 600,
                "warmup": True,
                "ramp": True,
                "power": {"start": 35, "end": 55, "units": "%ftp"},
                "_power": {"start": 98, "end": 154},
            }
        ],
    }
    body = workout.icu_workout_doc_to_igps("Warmup", "", doc)
    assert body is not None
    step = body["data"]["structure"][0]
    assert step["intensityClass"] == "WarmUp"
    assert step["intensityTarget"] == {
        "unit": "PowerCustom",
        "value": 0,
        "minValue": 98,
        "maxValue": 154,
    }


def test_icu_workout_doc_maps_repetition_block():
    doc = {
        "steps": [
            {
                "reps": 4,
                "steps": [
                    {"duration": 300, "power": {"value": 110, "units": "%ftp"}},
                    {"duration": 90, "power": {"value": 55, "units": "%ftp"}},
                ],
            }
        ],
    }
    body = workout.icu_workout_doc_to_igps("Intervals", "", doc)
    assert body is not None
    rep = body["data"]["structure"][0]
    assert rep["type"] == "Repetition"
    assert rep["length"] == {"unit": "Repetition", "value": 4}
    assert len(rep["steps"]) == 2


def test_icu_workout_doc_open_duration_step():
    doc = {
        "steps": [
            {
                "until_lap_press": True,
                "intensity": "rest",
            }
        ],
    }
    body = workout.icu_workout_doc_to_igps("Open", "", doc)
    assert body is not None
    step = body["data"]["structure"][0]
    assert step["openDuration"] == "true"
    assert step["intensityClass"] == "Rest"
    assert "length" not in step


def test_icu_workout_doc_returns_none_without_steps():
    assert workout.icu_workout_doc_to_igps("Empty", "", {}) is None
    assert workout.icu_workout_doc_to_igps("Empty", "", {"steps": []}) is None


def test_low_power_first_and_last_steps_remain_active_without_explicit_marks():
    body = workout.icu_workout_doc_to_igps(
        "No inferred classifications",
        "",
        {
            "steps": [
                {"duration": 300, "power": {"value": 40, "units": "%ftp"}},
                {"duration": 300, "power": {"value": 100, "units": "%ftp"}},
                {"duration": 300, "power": {"value": 35, "units": "%ftp"}},
            ]
        },
    )
    assert [step["intensityClass"] for step in body["data"]["structure"]] == [
        "Active",
        "Active",
        "Active",
    ]


def test_icu_workout_doc_includes_existing_id_for_update():
    doc = {"steps": [{"duration": 60}]}
    body = workout.icu_workout_doc_to_igps("Update", "", doc, existing_workout_id=5012)
    assert body is not None
    assert body["data"]["id"] == "5012"


def test_upload_custom_workout_returns_id(monkeypatch):
    monkeypatch.setattr(
        workout.requests.Session,
        "post",
        lambda self, *a, **k: FakeResponse(
            json_data={"code": 0, "data": {"workoutId": 999}}
        ),
    )
    session = workout.requests.Session()
    wid = workout.upload_custom_workout(
        session, {"Authorization": "Bearer x"}, {"data": {}}
    )
    assert wid == 999


def test_upload_custom_workout_returns_none_on_error(monkeypatch):
    monkeypatch.setattr(
        workout.requests.Session,
        "post",
        lambda self, *a, **k: FakeResponse(status=400, json_data={"code": 1}),
    )
    session = workout.requests.Session()
    assert workout.upload_custom_workout(session, {}, {}) is None


def test_list_custom_workouts(monkeypatch):
    captured: dict = {}

    def fake_get(self, url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return FakeResponse(json_data={"code": 0, "data": {"items": []}})

    monkeypatch.setattr(workout.requests.Session, "get", fake_get)
    session = workout.requests.Session()
    workout.list_custom_workouts(
        session, {"Authorization": "Bearer t"}, page_index=2, page_size=5
    )
    assert captured["url"] == workout.IGPS_WORKOUT_LIST_URL
    assert captured["params"] == {"PageIndex": 2, "PageSize": 5}


def test_list_custom_workouts_china_region(monkeypatch):
    captured: dict = {}

    def fake_get(self, url, **kwargs):
        captured["url"] = url
        return FakeResponse(json_data={"code": 0, "data": {"items": []}})

    monkeypatch.setattr(workout.requests.Session, "get", fake_get)
    session = workout.requests.Session()
    workout.list_custom_workouts(session, {"Authorization": "Bearer t"}, region="china")
    assert (
        captured["url"]
        == "https://prod.zh.igpsport.com/service/mobile/api/WorkOut/CustomWorkout"
    )


def test_fetch_all_custom_workout_ids_paginates(monkeypatch):
    pages = {
        1: {"code": 0, "data": {"items": [{"workoutId": 10}, {"workoutId": 11}]}},
        2: {"code": 0, "data": {"items": [{"id": 12}]}},
        3: {"code": 0, "data": {"items": []}},
    }

    def fake_list(session, auth_headers, page_index, page_size, **kwargs):
        return pages[page_index]

    monkeypatch.setattr(workout, "list_custom_workouts", fake_list)
    session = workout.requests.Session()
    ids = workout.fetch_all_custom_workout_ids(session, {}, page_size=2)
    assert ids == {10, 11, 12}


def test_apply_uploaded_workout_map_updates_and_prunes():
    uploaded = {"1": 100, "2": 200, "3": 300}
    result = workout.WorkoutUploadResult(
        uploaded_map={"4": 400},
        pruned_keys=["2"],
    )
    workout.apply_uploaded_workout_map(uploaded, result)
    assert uploaded == {"1": 100, "3": 300, "4": 400}


def test_upload_workouts_seeds_legacy_mapping_with_update(monkeypatch):
    monkeypatch.setattr(
        workout, "login", lambda s, u, p, *a, **k: {"Authorization": "Bearer t"}
    )
    monkeypatch.setattr(workout, "fetch_all_custom_workout_ids", lambda *a, **k: {100})
    monkeypatch.setattr(
        intervals_icu,
        "fetch_calendar_workouts",
        lambda *a, **k: [
            intervals_icu.CalendarWorkout(
                event_id=42,
                name="Done",
                description="",
                activity_type="Ride",
                workout_doc={"steps": [{"duration": 60}]},
            )
        ],
    )
    upload_calls: list = []
    monkeypatch.setattr(
        workout,
        "upload_custom_workout",
        lambda *a, **k: upload_calls.append(1) or 100,
    )

    cfg = workout.WorkoutUploadConfig(
        igp_user="u",
        igp_password="p",
        intervals_api_key="k",
        uploaded_workouts={"42": 100},
    )
    result = workout.upload_workouts(cfg)
    assert result.updated == 1
    assert result.uploaded == 0
    assert upload_calls == [1]


def test_upload_workouts_reuploads_when_deleted_on_igpsport(monkeypatch):
    monkeypatch.setattr(
        workout, "login", lambda s, u, p, *a, **k: {"Authorization": "Bearer t"}
    )
    monkeypatch.setattr(workout, "fetch_all_custom_workout_ids", lambda *a, **k: set())
    monkeypatch.setattr(
        intervals_icu,
        "fetch_calendar_workouts",
        lambda *a, **k: [
            intervals_icu.CalendarWorkout(
                event_id=42,
                name="Done",
                description="",
                activity_type="Ride",
                workout_doc={"steps": [{"duration": 60}]},
            )
        ],
    )
    monkeypatch.setattr(workout, "upload_custom_workout", lambda *a, **k: 200)

    cfg = workout.WorkoutUploadConfig(
        igp_user="u",
        igp_password="p",
        intervals_api_key="k",
        uploaded_workouts={"42": 100},
    )
    result = workout.upload_workouts(cfg)
    assert result.recreated == 1
    assert result.uploaded == 0
    assert result.synced_map == {"42": 200}
    assert result.pruned_keys == []


def test_upload_workouts_force_resync_updates_live_workout(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        workout, "login", lambda s, u, p, *a, **k: {"Authorization": "Bearer t"}
    )
    monkeypatch.setattr(workout, "fetch_all_custom_workout_ids", lambda *a, **k: {100})
    monkeypatch.setattr(
        intervals_icu,
        "fetch_calendar_workouts",
        lambda *a, **k: [
            intervals_icu.CalendarWorkout(
                event_id=42,
                name="Update me",
                description="",
                activity_type="Ride",
                workout_doc={"steps": [{"duration": 60}]},
            )
        ],
    )

    def fake_upload(session, auth_headers, body, **kwargs):
        captured["body"] = body
        return 100

    monkeypatch.setattr(workout, "upload_custom_workout", fake_upload)

    cfg = workout.WorkoutUploadConfig(
        igp_user="u",
        igp_password="p",
        intervals_api_key="k",
        uploaded_workouts={"42": 100},
        force_resync=True,
    )
    result = workout.upload_workouts(cfg)
    assert result.updated == 1
    assert result.uploaded == 0
    assert captured["body"]["data"]["id"] == "100"


def test_upload_workouts_prunes_stale_config_entries(monkeypatch):
    monkeypatch.setattr(
        workout, "login", lambda s, u, p, *a, **k: {"Authorization": "Bearer t"}
    )
    monkeypatch.setattr(workout, "fetch_all_custom_workout_ids", lambda *a, **k: set())
    monkeypatch.setattr(intervals_icu, "fetch_calendar_workouts", lambda *a, **k: [])

    cfg = workout.WorkoutUploadConfig(
        igp_user="u",
        igp_password="p",
        intervals_api_key="k",
        uploaded_workouts={"99": 100},
    )
    result = workout.upload_workouts(cfg)
    assert result.pruned_keys == ["99"]


def test_upload_workouts_uploads_new_workout(monkeypatch):
    monkeypatch.setattr(
        workout, "login", lambda s, u, p, *a, **k: {"Authorization": "Bearer t"}
    )
    monkeypatch.setattr(workout, "fetch_all_custom_workout_ids", lambda *a, **k: set())
    monkeypatch.setattr(
        intervals_icu,
        "fetch_calendar_workouts",
        lambda *a, **k: [
            intervals_icu.CalendarWorkout(
                event_id=7,
                name="New",
                description="",
                activity_type="Ride",
                workout_doc={
                    "steps": [
                        {"duration": 120, "power": {"value": 90, "units": "%ftp"}}
                    ]
                },
            )
        ],
    )
    monkeypatch.setattr(workout, "upload_custom_workout", lambda *a, **k: 555)

    cfg = workout.WorkoutUploadConfig(
        igp_user="u",
        igp_password="p",
        intervals_api_key="k",
    )
    result = workout.upload_workouts(cfg)
    assert result.uploaded == 1
    assert result.uploaded_map == {"7": 555}


def test_upload_workouts_skips_non_cycling_type(monkeypatch):
    monkeypatch.setattr(
        workout, "login", lambda s, u, p, *a, **k: {"Authorization": "Bearer t"}
    )
    monkeypatch.setattr(workout, "fetch_all_custom_workout_ids", lambda *a, **k: set())
    monkeypatch.setattr(
        intervals_icu,
        "fetch_calendar_workouts",
        lambda *a, **k: [
            intervals_icu.CalendarWorkout(
                event_id=1,
                name="Run",
                description="",
                activity_type="Run",
                workout_doc={"steps": [{"duration": 60}]},
            )
        ],
    )
    result = workout.upload_workouts(
        workout.WorkoutUploadConfig(
            igp_user="u", igp_password="p", intervals_api_key="k"
        )
    )
    assert result.skipped == 1
    assert result.uploaded == 0


def test_upload_workouts_one_day_window_uses_today_only(monkeypatch):
    captured: dict = {}
    today = date(2026, 6, 17)

    class FixedDate(date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(workout, "date", FixedDate)
    monkeypatch.setattr(
        workout, "login", lambda s, u, p, *a, **k: {"Authorization": "Bearer t"}
    )
    monkeypatch.setattr(workout, "fetch_all_custom_workout_ids", lambda *a, **k: set())

    def fake_fetch(api_key, oldest, newest):
        captured["oldest"] = oldest
        captured["newest"] = newest
        return []

    monkeypatch.setattr(intervals_icu, "fetch_calendar_workouts", fake_fetch)

    workout.upload_workouts(
        workout.WorkoutUploadConfig(
            igp_user="u",
            igp_password="p",
            intervals_api_key="k",
            workout_days_ahead=1,
        )
    )
    assert captured["oldest"] == today
    assert captured["newest"] == today


def _calendar_workout(
    *,
    event_id=42,
    name="Workout",
    description="",
    external_id="restortrain-42",
    oauth_client_id="restortrain",
    start_date_local="2026-09-10",
    steps=None,
):
    return intervals_icu.CalendarWorkout(
        event_id=event_id,
        name=name,
        description=description,
        activity_type="Ride",
        workout_doc={"steps": steps or [{"duration": 60}]},
        external_id=external_id,
        oauth_client_id=oauth_client_id,
        start_date_local=start_date_local,
    )


def _patch_workout_io(monkeypatch, calendar, live_ids, returned_id=100):
    monkeypatch.setattr(workout, "login", lambda *a, **k: {"Authorization": "Bearer t"})
    monkeypatch.setattr(
        workout, "fetch_all_custom_workout_ids", lambda *a, **k: set(live_ids)
    )
    monkeypatch.setattr(
        intervals_icu, "fetch_calendar_workouts", lambda *a, **k: calendar
    )
    calls = []

    def fake_upload(*args, **kwargs):
        calls.append(args[2])
        return returned_id

    monkeypatch.setattr(workout, "upload_custom_workout", fake_upload)
    return calls


def test_event_description_precedes_nested_description_and_counts_truncation(
    monkeypatch,
):
    event_description = "E" * 501
    body = workout.icu_workout_doc_to_igps(
        "Workout",
        event_description,
        {"description": "nested", "steps": [{"duration": 60}]},
    )
    assert body["data"]["description"] == "E" * 500

    calls = _patch_workout_io(
        monkeypatch, [_calendar_workout(description=event_description)], set(), 101
    )
    result = workout.upload_workouts(workout.WorkoutUploadConfig("u", "p", "k"))
    assert len(calls) == 1
    assert result.description_truncated == 1


def test_changed_workout_updates_same_remote_id(monkeypatch):
    current = _calendar_workout(name="Changed")
    source_key = workout.source_identity_key(current)
    calls = _patch_workout_io(monkeypatch, [current], {100}, 100)
    cfg = workout.WorkoutUploadConfig(
        "u",
        "p",
        "k",
        uploaded_workouts={"41": 100},
        workout_records={
            source_key: {
                "event_id": "41",
                "remote_id": 100,
                "source_key": source_key,
                "slot_key": workout.fallback_slot_key(current),
                "export_fingerprint": "old",
                "start_date_local": "2026-09-09",
            }
        },
    )
    result = workout.upload_workouts(cfg)
    assert result.updated == 1
    assert result.uploaded == 0
    assert calls[0]["data"]["id"] == "100"
    assert result.synced_map == {"42": 100}
    assert result.workout_records[source_key]["event_id"] == "42"


def test_unchanged_workout_is_skipped_without_write(monkeypatch):
    current = _calendar_workout()
    body = workout.icu_workout_doc_to_igps(
        current.name, current.description, current.workout_doc, existing_workout_id=100
    )
    fingerprint = workout.export_fingerprint(body)
    source_key = workout.source_identity_key(current)
    calls = _patch_workout_io(monkeypatch, [current], {100}, 100)
    cfg = workout.WorkoutUploadConfig(
        "u",
        "p",
        "k",
        workout_records={
            source_key: {
                "event_id": "42",
                "remote_id": 100,
                "source_key": source_key,
                "slot_key": workout.fallback_slot_key(current),
                "export_fingerprint": fingerprint,
                "start_date_local": "2026-09-10",
            }
        },
    )
    result = workout.upload_workouts(cfg)
    assert result.skipped == 1
    assert result.updated == 0
    assert calls == []


def test_missing_mapped_workout_is_recreated_in_active_window(monkeypatch):
    current = _calendar_workout()
    source_key = workout.source_identity_key(current)
    calls = _patch_workout_io(monkeypatch, [current], set(), 200)
    cfg = workout.WorkoutUploadConfig(
        "u",
        "p",
        "k",
        workout_records={
            source_key: {
                "event_id": "42",
                "remote_id": 100,
                "source_key": source_key,
                "slot_key": workout.fallback_slot_key(current),
                "export_fingerprint": "old",
                "start_date_local": "2026-09-10",
            }
        },
    )
    result = workout.upload_workouts(cfg)
    assert result.recreated == 1
    assert result.uploaded == 0
    assert "id" not in calls[0]["data"]
    assert result.workout_records[source_key]["remote_id"] == 200


def test_skip_marker_suppresses_create_update_and_force(monkeypatch):
    current = _calendar_workout(name="Intervals [skip-igp]")
    calls = _patch_workout_io(monkeypatch, [current], {100}, 100)
    cfg = workout.WorkoutUploadConfig(
        "u", "p", "k", uploaded_workouts={"42": 100}, force_resync=True
    )
    result = workout.upload_workouts(cfg)
    assert result.skipped == 1
    assert calls == []


def test_ignore_marker_is_ordinary_text(monkeypatch):
    current = _calendar_workout(name="Intervals [ignore-igp]")
    calls = _patch_workout_io(monkeypatch, [current], set(), 100)
    result = workout.upload_workouts(workout.WorkoutUploadConfig("u", "p", "k"))
    assert result.uploaded == 1
    assert len(calls) == 1


def test_update_id_mismatch_fails_and_preserves_record(monkeypatch):
    current = _calendar_workout()
    source_key = workout.source_identity_key(current)
    original = {
        "event_id": "42",
        "remote_id": 100,
        "source_key": source_key,
        "slot_key": workout.fallback_slot_key(current),
        "export_fingerprint": "old",
        "start_date_local": "2026-09-10",
    }
    _patch_workout_io(monkeypatch, [current], {100}, 999)
    cfg = workout.WorkoutUploadConfig(
        "u", "p", "k", workout_records={source_key: dict(original)}
    )
    result = workout.upload_workouts(cfg)
    assert result.failed == 1
    assert result.updated == 0
    assert result.workout_records[source_key] == original


def test_incomplete_remote_listing_fails_closed(monkeypatch):
    monkeypatch.setattr(
        workout,
        "list_custom_workouts",
        lambda *a, **k: {"code": 7, "message": "temporary"},
    )
    with pytest.raises(workout.SyncError):
        workout.fetch_all_custom_workout_ids(workout.requests.Session(), {})


def test_ambiguous_fallback_identity_conflicts_without_write(monkeypatch):
    current = _calendar_workout(external_id=None)
    slot = workout.fallback_slot_key(current)
    calls = _patch_workout_io(monkeypatch, [current], {100, 101}, 100)
    cfg = workout.WorkoutUploadConfig(
        "u",
        "p",
        "k",
        workout_records={
            "old-a": {
                "event_id": "1",
                "remote_id": 100,
                "source_key": None,
                "slot_key": slot,
                "export_fingerprint": "a",
                "start_date_local": "2026-09-10",
            },
            "old-b": {
                "event_id": "2",
                "remote_id": 101,
                "source_key": None,
                "slot_key": slot,
                "export_fingerprint": "b",
                "start_date_local": "2026-09-10",
            },
        },
    )
    result = workout.upload_workouts(cfg)
    assert result.conflicted == 1
    assert result.failed == 0
    assert calls == []
