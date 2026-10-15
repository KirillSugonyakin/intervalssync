"""Upload planned workouts from intervals.icu to iGPSPORT custom workouts.

Uses the mobile JSON API (EditCustomWorkOut), not FIT upload. Source data is
intervals.icu ``workout_doc`` from calendar events (see intervals.icu forum
guide on downloading planned workouts).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

import requests

from .. import intervals_icu
from .core import SyncError, login
from .http import HTTP_TIMEOUT
from .region import IgpRegionConfig, resolve_region

IGPS_API = "https://prod.en.igpsport.com"
IGPS_WORKOUT_LIST_URL = f"{IGPS_API}/service/mobile/api/WorkOut/CustomWorkout"
IGPS_WORKOUT_EDIT_URL = f"{IGPS_API}/service/mobile/api/WorkOut/EditCustomWorkOut"

# intervals.icu cycling types we accept in v1.
_CYCLING_TYPES = frozenset(
    {
        "Ride",
        "MountainBikeRide",
        "GravelRide",
        "VirtualRide",
        "EBikeRide",
        "EMountainBikeRide",
        "TrackRide",
        "Cyclocross",
        "Handcycle",
        "Velomobile",
    }
)

Progress = Callable[[str], None]


class UnsupportedWorkoutError(ValueError):
    """The source workout has semantics this relay must not silently change."""


def _noop(_message: str) -> None:
    pass


@dataclass
class WorkoutUploadResult:
    listed: int = 0
    uploaded: int = 0
    updated: int = 0
    recreated: int = 0
    skipped: int = 0
    failed: int = 0
    conflicted: int = 0
    no_steps: int = 0
    unsupported: int = 0
    verification_failed: int = 0
    description_truncated: int = 0
    uploaded_map: dict[str, int] = field(default_factory=dict)
    synced_map: dict[str, int] = field(default_factory=dict)
    workout_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    pruned_keys: list[str] = field(default_factory=list)


@dataclass
class WorkoutUploadConfig:
    igp_user: str
    igp_password: str
    intervals_api_key: str
    igp_region: str = "international"
    oldest: date | None = None
    newest: date | None = None
    workout_days_ahead: int = 1
    uploaded_workouts: dict[str, int] = field(default_factory=dict)
    workout_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    force_resync: bool = False
    checkpoint: Callable[[WorkoutUploadResult], None] | None = None


def list_custom_workouts(
    session: requests.Session,
    auth_headers: dict[str, str],
    page_index: int = 1,
    page_size: int = 20,
    region: IgpRegionConfig | str | None = None,
) -> dict[str, Any]:
    """Return the iGPSPORT custom-workout list response JSON."""
    cfg = resolve_region(region.name if isinstance(region, IgpRegionConfig) else region)
    resp = session.get(
        cfg.workout_list_url,
        params={"PageIndex": page_index, "PageSize": page_size},
        headers=auth_headers,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _workout_id_from_item(item: dict[str, Any]) -> int | None:
    for key in ("workoutId", "id", "workout_id"):
        value = item.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def fetch_all_custom_workout_ids(
    session: requests.Session,
    auth_headers: dict[str, str],
    *,
    page_size: int = 50,
    region: IgpRegionConfig | str | None = None,
) -> set[int]:
    """Return every custom-workout ID currently on iGPSPORT."""
    ids: set[int] = set()
    page_index = 1
    while True:
        data = list_custom_workouts(
            session, auth_headers, page_index, page_size, region=region
        )
        if not isinstance(data, dict) or data.get("code") != 0:
            raise SyncError("iGPSPORT returned an incomplete custom-workout list")
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise SyncError("iGPSPORT custom-workout list has invalid data")
        items = payload.get("items")
        if not isinstance(items, list):
            raise SyncError("iGPSPORT custom-workout list has invalid items")
        if not items:
            break
        for item in items:
            if not isinstance(item, dict):
                raise SyncError("iGPSPORT custom-workout list has invalid item")
            workout_id = _workout_id_from_item(item)
            if workout_id is None or workout_id <= 0:
                raise SyncError("iGPSPORT custom-workout list has invalid ID")
            if workout_id in ids:
                raise SyncError("iGPSPORT custom-workout pagination repeated an ID")
            ids.add(workout_id)
        if len(items) < page_size:
            break
        page_index += 1
    return ids


def apply_uploaded_workout_map(
    uploaded_workouts: dict[str, int],
    result: WorkoutUploadResult,
) -> None:
    """Merge upload results into config and drop entries for deleted workouts."""
    uploaded_workouts.update(result.uploaded_map)
    uploaded_workouts.update(result.synced_map)
    for key in result.pruned_keys:
        uploaded_workouts.pop(key, None)


def apply_workout_state(
    uploaded_workouts: dict[str, int],
    workout_records: dict[str, dict[str, Any]],
    result: WorkoutUploadResult,
) -> None:
    """Apply authoritative records and rebuild the legacy event-id projection."""
    next_records = {
        str(key): dict(value) for key, value in result.workout_records.items()
    }
    for event_id, remote_id in result.uploaded_map.items():
        if not any(
            record.get("event_id") == str(event_id) for record in next_records.values()
        ):
            next_records[f"event:{event_id}"] = {
                "event_id": str(event_id),
                "remote_id": int(remote_id),
                "source_key": None,
                "slot_key": None,
                "export_fingerprint": None,
                "start_date_local": "",
                "verification_version": None,
                "verification_status": None,
            }
    workout_records.clear()
    workout_records.update(next_records)
    uploaded_workouts.clear()
    for record in workout_records.values():
        event_id = str(record.get("event_id") or "")
        remote_id = record.get("remote_id")
        if event_id and isinstance(remote_id, int) and remote_id > 0:
            uploaded_workouts[event_id] = remote_id


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_identity_key(workout: intervals_icu.CalendarWorkout) -> str | None:
    """Return a non-reversible stable source identity when provider data exists."""
    if not workout.oauth_client_id or not workout.external_id:
        return None
    return _digest(f"{workout.oauth_client_id}\0{workout.external_id}")


def fallback_slot_key(workout: intervals_icu.CalendarWorkout) -> str | None:
    """Return a non-reversible provider/type/local-date fallback identity."""
    if not workout.oauth_client_id or not workout.start_date_local:
        return None
    return _digest(
        f"{workout.oauth_client_id}\0{workout.activity_type}\0"
        f"{workout.start_date_local[:10]}"
    )


def export_fingerprint(body: dict[str, Any]) -> str:
    """Hash the normalized remote export while ignoring generated UUIDs and id."""

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: normalize(child)
                for key, child in sorted(value.items())
                if key not in {"uuid", "id"}
            }
        if isinstance(value, list):
            return [normalize(child) for child in value]
        return value

    encoded = json.dumps(
        normalize(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _digest(encoded)


def _has_skip_marker(workout: intervals_icu.CalendarWorkout) -> bool:
    text = f"{workout.name}\n{workout.description}".lower()
    return "[skip-igp]" in text


def _valid_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        remote_id = int(value["remote_id"])
    except (KeyError, TypeError, ValueError):
        return None
    if remote_id <= 0:
        return None
    record = {
        "event_id": str(value.get("event_id") or ""),
        "remote_id": remote_id,
        "source_key": value.get("source_key") or None,
        "slot_key": value.get("slot_key") or None,
        "export_fingerprint": value.get("export_fingerprint") or None,
        "start_date_local": str(value.get("start_date_local") or "")[:10],
    }
    if value.get("verification_version") == 1:
        record["verification_version"] = 1
    if value.get("verification_status") in {"verified", "pending"}:
        record["verification_status"] = value["verification_status"]
    return record


def fetch_custom_workout_detail(
    session: requests.Session,
    auth_headers: dict[str, str],
    workout_id: int,
    region: IgpRegionConfig | str | None = None,
) -> dict[str, Any] | None:
    """Return a single iGPSPORT workout detail, or None for an invalid response."""
    cfg = resolve_region(region.name if isinstance(region, IgpRegionConfig) else region)
    response = session.get(
        f"{cfg.mobile_api_base}/WorkOut/GetWorkOutDetail",
        params={"id": workout_id},
        headers=auth_headers,
        timeout=HTTP_TIMEOUT,
    )
    if not response.ok:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    data = (
        payload.get("data")
        if isinstance(payload, dict) and payload.get("code") == 0
        else None
    )
    return data if isinstance(data, dict) else None


def _target_shape(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value[key]
        for key in ("unit", "value", "minValue", "maxValue")
        if key in value
    }


def _structure_shape(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, list):
        return None
    result: list[dict[str, Any]] = []
    for step in value:
        if not isinstance(step, dict):
            return None
        shape = {
            key: step[key]
            for key in ("type", "intensityClass", "openDuration", "length")
            if key in step
        }
        for key in ("intensityTarget", "secondaryIntensityTarget"):
            if key in step:
                target = _target_shape(step[key])
                if target is None:
                    return None
                shape[key] = target
        if "steps" in step:
            nested = _structure_shape(step["steps"])
            if nested is None:
                return None
            shape["steps"] = nested
        result.append(shape)
    return result


def _verification_shape(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    structure = _structure_shape(data.get("structure"))
    if structure is None:
        return None
    return {"title": data.get("title"), "structure": structure}


def verify_custom_workout(
    session: requests.Session,
    auth_headers: dict[str, str],
    workout_id: int,
    expected_body: dict[str, Any],
    region: IgpRegionConfig | str | None = None,
) -> bool:
    """Read back and compare executable workout semantics after a write."""
    try:
        remote = fetch_custom_workout_detail(
            session, auth_headers, workout_id, region=region
        )
    except requests.RequestException:
        return False
    expected = _verification_shape(expected_body.get("data"))
    actual = _verification_shape(remote)
    return expected is not None and expected == actual


def upload_custom_workout(
    session: requests.Session,
    auth_headers: dict[str, str],
    body: dict[str, Any],
    region: IgpRegionConfig | str | None = None,
) -> int | None:
    """Create or update a custom workout; return workoutId on success."""
    cfg = resolve_region(region.name if isinstance(region, IgpRegionConfig) else region)
    resp = session.post(
        cfg.workout_edit_url,
        json=body,
        headers=auth_headers,
        timeout=HTTP_TIMEOUT,
    )
    if not resp.ok:
        return None
    data = resp.json()
    if data.get("code") != 0:
        return None
    payload = data.get("data") or {}
    workout_id = payload.get("workoutId")
    return int(workout_id) if workout_id is not None else None


def _step_name(step: dict[str, Any], index: int) -> str:
    text = str(step.get("text") or "").strip()
    if text:
        return text[:64]
    return f"Step {index}"


def _intensity_class(step: dict[str, Any]) -> str:
    intensity = str(step.get("intensity") or "").lower()
    if step.get("warmup") or intensity == "warmup":
        return "WarmUp"
    if step.get("cooldown") or intensity == "cooldown":
        return "CoolDown"
    if intensity in ("rest", "recovery"):
        return "Rest"
    return "Active"


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any, *, field: str, positive: bool = False) -> int:
    number = _num(value)
    if number is None or not number.is_integer() or (positive and number <= 0):
        qualifier = "a positive whole number" if positive else "a whole number"
        raise UnsupportedWorkoutError(f"{field} must be {qualifier}")
    return int(number)


def _map_power_target(step: dict[str, Any]) -> dict[str, Any] | None:
    resolved = step.get("_power")
    if isinstance(resolved, dict):
        start = _num(resolved.get("start"))
        end = _num(resolved.get("end"))
        value = _num(resolved.get("value"))
        min_v = start if start is not None else value
        max_v = end if end is not None else value
        if min_v is None and max_v is None:
            return None
        min_v = int(min_v if min_v is not None else max_v)
        max_v = int(max_v if max_v is not None else min_v)
        if min_v > max_v:
            min_v, max_v = max_v, min_v
        return {
            "unit": "PowerCustom",
            "value": 0,
            "minValue": min_v,
            "maxValue": max_v,
        }

    power = step.get("power")
    if not isinstance(power, dict):
        return None

    units = str(power.get("units") or "").lower()
    start = _num(power.get("start"))
    end = _num(power.get("end"))
    value = _num(power.get("value"))
    min_v = start if start is not None else value
    max_v = end if end is not None else value
    if min_v is None and max_v is None:
        return None
    min_v = int(min_v if min_v is not None else max_v)
    max_v = int(max_v if max_v is not None else min_v)
    if min_v > max_v:
        min_v, max_v = max_v, min_v

    if units == "%ftp":
        return {
            "unit": "PercentOfFtp",
            "value": 0,
            "minValue": min_v,
            "maxValue": max_v,
        }
    if units not in {"w", "watt", "watts"}:
        raise UnsupportedWorkoutError(f"unsupported power units: {units or 'missing'}")
    return {
        "unit": "PowerCustom",
        "value": 0,
        "minValue": min_v,
        "maxValue": max_v,
    }


def _map_hr_target(step: dict[str, Any]) -> dict[str, Any] | None:
    resolved = step.get("_hr")
    if isinstance(resolved, dict):
        start = _num(resolved.get("start"))
        end = _num(resolved.get("end"))
        value = _num(resolved.get("value"))
        min_v = start if start is not None else value
        max_v = end if end is not None else value
        if min_v is not None and max_v is not None:
            return {
                "unit": "HeartRateCustom",
                "value": 0,
                "minValue": int(min_v),
                "maxValue": int(max_v),
            }
        if value is not None:
            if 1 <= value <= 5 and value == int(value):
                return {"unit": "HeartRate", "value": int(value)}
            return {
                "unit": "HeartRateCustom",
                "value": 0,
                "minValue": int(value),
                "maxValue": int(value),
            }

    hr = step.get("hr")
    if not isinstance(hr, dict):
        return None
    units = str(hr.get("units") or "").lower()
    value = _num(hr.get("value"))
    start = _num(hr.get("start"))
    end = _num(hr.get("end"))
    if units == "hr_zone":
        if value is None or value != int(value) or not 1 <= value <= 5:
            raise UnsupportedWorkoutError("heart-rate zone must be an integer from 1 to 5")
        return {"unit": "HeartRate", "value": int(value)}
    if units not in {"bpm", "heart_rate"}:
        raise UnsupportedWorkoutError(
            f"unsupported heart-rate units: {units or 'missing'}"
        )
    if start is not None and end is not None:
        return {
            "unit": "HeartRateCustom",
            "value": 0,
            "minValue": int(start),
            "maxValue": int(end),
        }
    if value is not None:
        if 1 <= value <= 5 and value == int(value):
            return {"unit": "HeartRate", "value": int(value)}
        return {
            "unit": "HeartRateCustom",
            "value": 0,
            "minValue": int(value),
            "maxValue": int(value),
        }
    return None


def _map_cadence_target(step: dict[str, Any]) -> dict[str, Any] | None:
    cadence = step.get("cadence")
    if not isinstance(cadence, dict):
        return None
    units = str(cadence.get("units") or "").lower()
    if units not in {"rpm", ""}:
        raise UnsupportedWorkoutError(f"unsupported cadence units: {units}")
    value = _num(cadence.get("value"))
    start = _num(cadence.get("start"))
    end = _num(cadence.get("end"))
    min_v = start if start is not None else value
    max_v = end if end is not None else value
    if min_v is None and max_v is None:
        return None
    min_v = int(min_v if min_v is not None else max_v)
    max_v = int(max_v if max_v is not None else min_v)
    return {
        "unit": "Cadence",
        "value": 0,
        "minValue": min_v,
        "maxValue": max_v,
    }


def _workout_target(workout_doc: dict[str, Any]) -> str:
    raw_target = workout_doc.get("target")
    if raw_target is None or str(raw_target).strip() == "":
        return "AUTO"
    target = str(raw_target).upper()
    if target in {"AUTO", "POWER", "HR"}:
        return target
    raise UnsupportedWorkoutError(f"unsupported workout target: {target}")


def _primary_target(step: dict[str, Any], target: str) -> dict[str, Any] | None:
    if target == "POWER":
        return _map_power_target(step)
    if target == "HR":
        return _map_hr_target(step)

    has_power = isinstance(step.get("power"), dict) or isinstance(
        step.get("_power"), dict
    )
    has_hr = isinstance(step.get("hr"), dict) or isinstance(step.get("_hr"), dict)
    if has_power and has_hr:
        raise UnsupportedWorkoutError("ambiguous automatic power and heart-rate targets")
    if has_power:
        return _map_power_target(step)
    if has_hr:
        return _map_hr_target(step)
    return None


def _assert_supported_step(step: dict[str, Any], target: str) -> None:
    for field in ("ramp", "freeride", "maxeffort"):
        if step.get(field):
            raise UnsupportedWorkoutError(f"unsupported step feature: {field}")
    if isinstance(step.get("pace"), dict):
        raise UnsupportedWorkoutError("pace targets are not supported")
    if step.get("calories") is not None or step.get("calorie") is not None:
        raise UnsupportedWorkoutError("calorie duration is not supported")
    has_power = any(isinstance(step.get(key), dict) for key in ("power", "_power"))
    has_hr = any(isinstance(step.get(key), dict) for key in ("hr", "_hr"))
    if target == "POWER" and (
        isinstance(step.get("hr"), dict) or (has_hr and not has_power)
    ):
        raise UnsupportedWorkoutError("secondary heart-rate targets are not supported")
    if target == "HR" and (
        isinstance(step.get("power"), dict) or (has_power and not has_hr)
    ):
        raise UnsupportedWorkoutError("secondary power targets are not supported")
    if isinstance(step.get("secondary_hr"), dict):
        raise UnsupportedWorkoutError("secondary heart-rate targets are not supported")


def _step_length(step: dict[str, Any]) -> dict[str, Any]:
    if step.get("distance") is not None:
        return {
            "unit": "Meter",
            "value": _integer(step["distance"], field="distance", positive=True),
        }
    if step.get("duration") is None:
        raise UnsupportedWorkoutError("step has neither duration nor distance")
    return {
        "unit": "Second",
        "value": _integer(step["duration"], field="duration", positive=True),
    }


def _map_icu_step(step: dict[str, Any], index: int, target: str) -> dict[str, Any] | None:
    _assert_supported_step(step, target)
    reps = step.get("reps")
    nested = step.get("steps")
    if reps is not None:
        repetitions = _integer(reps, field="repetitions", positive=True)
        if not isinstance(nested, list) or not nested:
            raise UnsupportedWorkoutError("repetition has no valid child steps")
        child_steps = []
        for i, child in enumerate(nested, start=1):
            if not isinstance(child, dict):
                raise UnsupportedWorkoutError("repetition contains an invalid step")
            mapped = _map_icu_step(child, i, target)
            if mapped is not None:
                child_steps.append(mapped)
        if not child_steps:
            return None
        return {
            "type": "Repetition",
            "name": _step_name(step, index),
            "uuid": str(uuid.uuid4()),
            "intensityClass": "Active",
            "openDuration": "false",
            "length": {"unit": "Repetition", "value": repetitions},
            "steps": child_steps,
        }

    open_duration = bool(step.get("until_lap_press"))
    igp_step: dict[str, Any] = {
        "type": "Step",
        "name": _step_name(step, index),
        "uuid": str(uuid.uuid4()),
        "intensityClass": _intensity_class(step),
        "openDuration": "true" if open_duration else "false",
    }

    if not open_duration:
        igp_step["length"] = _step_length(step)

    primary_target = _primary_target(step, target)
    if primary_target:
        igp_step["intensityTarget"] = primary_target

    cadence_target = _map_cadence_target(step)
    if cadence_target:
        if primary_target is None:
            raise UnsupportedWorkoutError("cadence requires a supported primary target")
        igp_step["secondaryIntensityTarget"] = cadence_target

    return igp_step


def _total_time(structure: list[dict[str, Any]], workout_doc: dict[str, Any]) -> int:
    doc_duration = workout_doc.get("duration")
    if isinstance(doc_duration, int) and doc_duration > 0:
        return doc_duration
    if isinstance(doc_duration, float) and doc_duration > 0:
        return int(doc_duration)

    total = 0

    def walk(steps: list[dict[str, Any]], repeat: int = 1) -> None:
        nonlocal total
        for step in steps:
            if step.get("type") == "Repetition":
                reps = step.get("length", {}).get("value", 1)
                nested = step.get("steps") or []
                walk(nested, repeat * int(reps))
            elif step.get("openDuration") != "true":
                length = step.get("length") or {}
                if length.get("unit") == "Second":
                    total += int(length.get("value", 0)) * repeat

    walk(structure)
    return total


def icu_workout_doc_to_igps(
    name: str,
    description: str,
    workout_doc: dict[str, Any],
    *,
    existing_workout_id: int | None = None,
) -> dict[str, Any] | None:
    """Map intervals.icu workout_doc to an EditCustomWorkOut request body."""
    raw_steps = workout_doc.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return None

    target = _workout_target(workout_doc)
    structure: list[dict[str, Any]] = []
    for i, step in enumerate(raw_steps, start=1):
        if not isinstance(step, dict):
            raise UnsupportedWorkoutError("workout contains an invalid step")
        mapped = _map_icu_step(step, i, target)
        if mapped is not None:
            structure.append(mapped)

    if not structure:
        return None

    doc_description = str(description or workout_doc.get("description") or "")
    data: dict[str, Any] = {
        "title": name[:64],
        "description": doc_description[:500],
        "totalTime": _total_time(structure, workout_doc),
        "workoutType": "bike",
        "sportBigType": 1,
        "allowDeletion": True,
        "structure": structure,
    }
    if existing_workout_id:
        data["id"] = str(existing_workout_id)

    return {"data": data}


def upload_workouts(
    config: WorkoutUploadConfig,
    progress: Progress | None = None,
) -> WorkoutUploadResult:
    """Fetch upcoming intervals.icu workouts and push them to iGPSPORT."""
    report = progress or _noop
    result = WorkoutUploadResult()

    today = date.today()
    oldest = config.oldest or today
    days = max(1, config.workout_days_ahead)
    newest = config.newest or (today + timedelta(days=days - 1))

    report("Logging in to iGPSPORT…")
    session = requests.Session()
    region = resolve_region(config.igp_region)
    auth_headers = login(session, config.igp_user, config.igp_password, region)
    report("Logged in.")

    try:
        live_ids = fetch_all_custom_workout_ids(session, auth_headers, region=region)
    except (requests.RequestException, ValueError) as exc:
        raise SyncError(f"Could not list iGPSPORT workouts: {exc}") from exc
    report(f"Found {len(live_ids)} custom workouts on iGPSPORT.")

    report("Fetching planned workouts from intervals.icu…")
    try:
        calendar = intervals_icu.fetch_calendar_workouts(
            config.intervals_api_key, oldest, newest
        )
    except (requests.RequestException, ValueError) as exc:
        raise SyncError(f"Could not fetch intervals.icu workouts: {exc}") from exc

    if not isinstance(calendar, list):
        raise SyncError("intervals.icu workouts response must be a list")
    result.listed = len(calendar)
    report(f"Found {len(calendar)} planned workouts.")

    records: dict[str, dict[str, Any]] = {}
    for key, raw_record in config.workout_records.items():
        record = _valid_record(raw_record)
        if record is not None:
            records[str(key)] = record

    # Convert legacy event-id mappings into records before making decisions.
    current_by_event = {str(item.event_id): item for item in calendar}
    current_sources = {
        key for item in calendar if (key := source_identity_key(item)) is not None
    }
    source_keys = [source_identity_key(item) for item in calendar]
    known_sources = [key for key in source_keys if key is not None]
    if len(current_by_event) != len(calendar) or len(known_sources) != len(current_sources):
        raise SyncError("intervals.icu calendar has ambiguous workout identities")
    for event_key, raw_remote_id in config.uploaded_workouts.items():
        try:
            remote_id = int(raw_remote_id)
        except (TypeError, ValueError):
            continue
        if remote_id <= 0 or any(
            record["event_id"] == str(event_key) for record in records.values()
        ):
            continue
        current = current_by_event.get(str(event_key))
        source_key = source_identity_key(current) if current else None
        record_key = source_key or f"event:{event_key}"
        records[record_key] = {
            "event_id": str(event_key),
            "remote_id": remote_id,
            "source_key": source_key,
            "slot_key": fallback_slot_key(current) if current else None,
            "export_fingerprint": None,
            "start_date_local": current.start_date_local[:10] if current else "",
            "verification_version": None,
            "verification_status": None,
        }

    slot_counts: dict[str, int] = {}
    for item in calendar:
        slot = fallback_slot_key(item)
        if slot:
            slot_counts[slot] = slot_counts.get(slot, 0) + 1

    matched_record_keys: set[str] = set()

    for workout in calendar:
        event_key = str(workout.event_id)

        if workout.activity_type not in _CYCLING_TYPES:
            report(
                f"↷ Skipping {workout.name} — unsupported type "
                f"{workout.activity_type!r} (cycling only in v1)."
            )
            result.skipped += 1
            continue

        source_key = source_identity_key(workout)
        slot_key = fallback_slot_key(workout)
        candidates: list[str] = []
        if source_key and source_key in records:
            candidates = [source_key]
        else:
            candidates = [
                key
                for key, record in records.items()
                if record["event_id"] == event_key
            ]
        if not candidates and slot_key:
            slot_candidates = [
                key
                for key, record in records.items()
                if record.get("slot_key") == slot_key
                and record["event_id"] not in current_by_event
                and record.get("source_key") not in current_sources
            ]
            if slot_candidates and (
                slot_counts.get(slot_key) != 1 or len(slot_candidates) != 1
            ):
                report(
                    f"✗ Conflicting fallback identity for {workout.name}; "
                    "no write performed."
                )
                result.conflicted += 1
                continue
            if len(slot_candidates) == 1:
                candidates = slot_candidates
        if len(candidates) > 1:
            report(f"✗ Conflicting identity for {workout.name}; no write performed.")
            result.conflicted += 1
            continue

        old_key = candidates[0] if candidates else None
        old_record = records.get(old_key) if old_key else None
        stored_id = int(old_record["remote_id"]) if old_record else None
        on_igpsport = stored_id is not None and stored_id in live_ids

        if _has_skip_marker(workout):
            report(f"↷ Skipping {workout.name} — [skip-igp] is present.")
            result.skipped += 1
            if old_key:
                matched_record_keys.add(old_key)
            continue

        update_id = stored_id if on_igpsport else None
        try:
            body = icu_workout_doc_to_igps(
                workout.name,
                workout.description,
                workout.workout_doc,
                existing_workout_id=update_id,
            )
        except UnsupportedWorkoutError as exc:
            report(f"⚠ Skipping {workout.name} — unsupported workout: {exc}.")
            result.unsupported += 1
            result.failed += 1
            if old_key:
                matched_record_keys.add(old_key)
            continue
        if body is None:
            report(
                f"⚠ Skipping {workout.name} — no structured steps "
                "(open the workout in intervals.icu first)."
            )
            result.no_steps += 1
            continue

        selected_description = str(
            workout.description or workout.workout_doc.get("description") or ""
        )
        if len(selected_description) > 500:
            result.description_truncated += 1
        fingerprint = export_fingerprint(body)

        event_rebound = bool(old_record and old_record["event_id"] != event_key)
        unchanged_candidate = bool(
            on_igpsport
            and old_record
            and old_record.get("export_fingerprint") == fingerprint
            and not event_rebound
            and not config.force_resync
        )
        if unchanged_candidate and old_record.get("verification_version") == 1:
            report(f"↷ Skipping {workout.name} — export is unchanged.")
            result.skipped += 1
            if old_key:
                matched_record_keys.add(old_key)
            continue
        if unchanged_candidate:
            if verify_custom_workout(
                session, auth_headers, stored_id, body, region=region
            ):
                old_record["verification_version"] = 1
                old_record["verification_status"] = "verified"
                result.synced_map[event_key] = stored_id
                result.skipped += 1
                if old_key:
                    matched_record_keys.add(old_key)
                continue
            report(f"✗ Could not verify legacy export for {workout.name}.")
            result.verification_failed += 1
            result.failed += 1
            if old_key:
                matched_record_keys.add(old_key)
            continue

        action = "update" if on_igpsport else ("recreate" if old_record else "upload")
        action_label = {
            "update": "Updating",
            "recreate": "Recreating",
            "upload": "Uploading",
        }[action]
        report(f"{action_label} {workout.name}…")
        workout_id = upload_custom_workout(session, auth_headers, body, region=region)
        if workout_id is None:
            report(f"✗ Failed to upload {workout.name}.")
            result.failed += 1
            if old_key:
                matched_record_keys.add(old_key)
            continue
        if action == "update" and workout_id != stored_id:
            report(f"✗ iGPSPORT changed the ID while updating {workout.name}.")
            result.failed += 1
            if old_key:
                matched_record_keys.add(old_key)
            continue

        live_ids.add(workout_id)

        new_key = source_key or f"event:{event_key}"
        if old_key and old_key != new_key:
            records.pop(old_key, None)
        records[new_key] = {
            "event_id": event_key,
            "remote_id": workout_id,
            "source_key": source_key,
            "slot_key": slot_key,
            "export_fingerprint": None,
            "start_date_local": workout.start_date_local[:10],
            "verification_version": None,
            "verification_status": "pending",
        }
        result.workout_records = records
        if config.checkpoint:
            try:
                config.checkpoint(result)
            except Exception as exc:  # noqa: BLE001
                raise SyncError("Could not checkpoint accepted workout ID") from exc
        if not verify_custom_workout(
            session, auth_headers, workout_id, body, region=region
        ):
            report(f"✗ Could not verify iGPSPORT export for {workout.name}.")
            result.verification_failed += 1
            result.failed += 1
            matched_record_keys.add(new_key)
            continue

        records[new_key]["export_fingerprint"] = fingerprint
        records[new_key]["verification_version"] = 1
        records[new_key]["verification_status"] = "verified"
        if action == "update":
            result.updated += 1
        elif action == "recreate":
            result.recreated += 1
        else:
            result.uploaded += 1
            result.uploaded_map[event_key] = workout_id
        result.synced_map[event_key] = workout_id
        matched_record_keys.add(new_key)

    # A complete list is authoritative. Missing mappings outside the active
    # window are forgotten, but live historical mappings remain available.
    for record_key, record in list(records.items()):
        if (
            record_key in matched_record_keys
            or record["event_id"] in current_by_event
            or record.get("source_key") in current_sources
        ):
            continue
        if record["remote_id"] not in live_ids:
            result.pruned_keys.append(record["event_id"])
            records.pop(record_key)

    result.workout_records = records

    return result
