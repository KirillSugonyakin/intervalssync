"""Map intervals.icu zone definitions onto iGPSPORT fixed zone slots."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
from math import isfinite
from typing import Any

POWER_INTERIOR_CAP = 1999
POWER_LAST_ZONE_END = 2500
# intervals.icu's open-ended top power zone (e.g. "151%+") has no real upper
# bound; its API reports this zone's upper bound as this sentinel percentage
# rather than omitting it.
OPEN_ZONE_SENTINEL_PCT = 999


class ZoneModel(StrEnum):
    """Versioned semantic models supported by the strict zone adapters."""

    FRIEL_7 = "friel_7"
    COGGAN_7 = "coggan_7"
    DIRECT_5 = "direct_5"
    UNKNOWN = "unknown"


ZONE_ADAPTER_VERSION = 1


@dataclass(frozen=True)
class ZoneScheme:
    """Immutable, validated source-zone description for strict adapters."""

    metric: str
    basis: str
    model: ZoneModel
    names: tuple[str, ...]
    upper_bounds: tuple[float, ...]
    threshold: float
    adapter_version: int = ZONE_ADAPTER_VERSION


_ZONE_PREFIX_RE = re.compile(
    r"^\s*(?:(?:zone|z)\s*)?\d+(?:[a-c])?\s*(?:[-:._)\]]+\s*)?",
    re.IGNORECASE,
)

_FRIEL_7_NAMES = (
    frozenset({"recovery"}),
    frozenset({"aerobic", "endurance"}),
    frozenset({"tempo"}),
    frozenset({"subthreshold"}),
    frozenset({"superthreshold", "suprathreshold"}),
    frozenset({"aerobiccapacity"}),
    frozenset({"anaerobic"}),
)
_COGGAN_7_NAMES = (
    frozenset({"recovery", "activerecovery"}),
    frozenset({"endurance"}),
    frozenset({"tempo"}),
    frozenset({"threshold", "lactatethreshold"}),
    frozenset({"vo2", "vo2max"}),
    frozenset({"anaerobic", "anaerobiccapacity"}),
    frozenset({"neuromuscular", "neuromuscularpower"}),
)
_DIRECT_5_NAMES = (
    frozenset({"recovery", "activerecovery"}),
    frozenset({"aerobic", "endurance"}),
    frozenset({"tempo"}),
    frozenset({"threshold", "lactatethreshold"}),
    frozenset({"vo2", "vo2max"}),
)


def _normalize_zone_name(name: str) -> str:
    without_prefix = _ZONE_PREFIX_RE.sub("", str(name), count=1)
    return re.sub(r"[^a-z0-9]+", "", without_prefix.casefold())


def _names_match(names: list[str], aliases: tuple[frozenset[str], ...]) -> bool:
    return len(names) == len(aliases) and all(
        _normalize_zone_name(name) in accepted
        for name, accepted in zip(names, aliases, strict=True)
    )


def recognize_zone_model(metric: str, names: list[str]) -> ZoneModel:
    """Recognize a supported semantic model from ordered, normalized labels."""
    normalized_metric = str(metric).strip().casefold()
    if normalized_metric == "hr" and _names_match(names, _FRIEL_7_NAMES):
        return ZoneModel.FRIEL_7
    if normalized_metric == "power" and _names_match(names, _COGGAN_7_NAMES):
        return ZoneModel.COGGAN_7
    if normalized_metric in {"hr", "power"} and _names_match(names, _DIRECT_5_NAMES):
        return ZoneModel.DIRECT_5
    return ZoneModel.UNKNOWN


def _numeric_bounds(values: list[float]) -> tuple[float, ...] | None:
    try:
        bounds = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return None
    if not bounds or not all(isfinite(value) and value > 0 for value in bounds):
        return None
    return bounds


def _strictly_increasing(values: tuple[float, ...]) -> bool:
    return all(left < right for left, right in zip(values, values[1:]))


def _zone_scheme(
    metric: str,
    basis: str,
    model: ZoneModel,
    names: list[str],
    bounds: tuple[float, ...],
    threshold: float,
) -> ZoneScheme:
    return ZoneScheme(
        metric=metric,
        basis=basis,
        model=model,
        names=tuple(_normalize_zone_name(name) for name in names),
        upper_bounds=bounds,
        threshold=threshold,
    )


def detect_hr_zone_scheme(
    names: list[str],
    upper_bounds: list[float],
    *,
    lthr: float | None,
    max_hr: float,
) -> ZoneScheme:
    """Detect a supported HR model only when its numeric structure is valid."""
    bounds = _numeric_bounds(upper_bounds) or ()
    try:
        normalized_lthr = float(lthr)
    except (TypeError, ValueError):
        normalized_lthr = 0.0
    try:
        normalized_max_hr = float(max_hr)
    except (TypeError, ValueError):
        normalized_max_hr = 0.0
    model = recognize_zone_model("hr", names)
    valid_thresholds = (
        isfinite(normalized_lthr)
        and normalized_lthr > 0
        and isfinite(normalized_max_hr)
        and normalized_max_hr > 0
    )
    if (
        model is ZoneModel.FRIEL_7
        and len(bounds) == 7
        and valid_thresholds
        and _strictly_increasing(bounds)
        and all(bound < normalized_max_hr for bound in bounds[:-1])
        and bounds[-1] >= normalized_max_hr
        and bounds[3] <= normalized_lthr <= bounds[4]
    ):
        return _zone_scheme("hr", "lthr", model, names, bounds, normalized_lthr)
    if (
        model is ZoneModel.DIRECT_5
        and len(bounds) == 5
        and isfinite(normalized_max_hr)
        and normalized_max_hr > 0
        and _strictly_increasing(bounds)
        and all(bound < normalized_max_hr for bound in bounds[:-1])
        and bounds[-1] == normalized_max_hr
    ):
        return _zone_scheme("hr", "custom", model, names, bounds, normalized_max_hr)
    return _zone_scheme(
        "hr", "custom", ZoneModel.UNKNOWN, names, bounds, normalized_lthr
    )


def detect_power_zone_scheme(
    names: list[str],
    upper_bounds: list[float],
    *,
    ftp: float,
) -> ZoneScheme:
    """Detect Coggan power only when FTP, finite ends, and open top are valid."""
    bounds = _numeric_bounds(upper_bounds) or ()
    try:
        normalized_ftp = float(ftp)
    except (TypeError, ValueError):
        normalized_ftp = 0.0
    model = recognize_zone_model("power", names)
    if (
        model is ZoneModel.COGGAN_7
        and len(bounds) == 7
        and isfinite(normalized_ftp)
        and normalized_ftp > 0
        and _strictly_increasing(bounds)
        and bounds[-1] >= OPEN_ZONE_SENTINEL_PCT
    ):
        return _zone_scheme("power", "ftp", model, names, bounds, normalized_ftp)
    return _zone_scheme(
        "power", "custom", ZoneModel.UNKNOWN, names, bounds, normalized_ftp
    )


def _round_positive_half_up(value: float | Decimal) -> int:
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("zone boundary must be numeric") from exc
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ValueError("zone boundary must be positive and finite")
    return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def map_strict_hr_zones(
    scheme: ZoneScheme,
    *,
    max_hr: float,
    igpsport_zones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map only recognized HR models without inventing zone boundaries."""
    if scheme.model is ZoneModel.FRIEL_7:
        if len(igpsport_zones) != 5 or len(scheme.upper_bounds) != 7:
            raise ValueError("unsupported HR zone mapping")
        ends = [
            *(_round_positive_half_up(value) for value in scheme.upper_bounds[:4]),
            _round_positive_half_up(max_hr),
        ]
    elif scheme.model is ZoneModel.DIRECT_5:
        if len(igpsport_zones) != 5 or len(scheme.upper_bounds) != 5:
            raise ValueError("unsupported HR zone mapping")
        ends = [_round_positive_half_up(value) for value in scheme.upper_bounds]
    else:
        raise ValueError("unsupported HR zone model")
    if not all(left < right for left, right in zip(ends, ends[1:])):
        raise ValueError("HR zone boundaries must be strictly increasing")
    return _apply_end_values(ends, igpsport_zones)


def map_strict_power_zones(
    scheme: ZoneScheme,
    *,
    igpsport_zones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map recognized Coggan zones while preserving the destination open cap."""
    if scheme.model is not ZoneModel.COGGAN_7:
        raise ValueError("unsupported power zone model")
    if len(scheme.upper_bounds) != 7 or len(igpsport_zones) not in {5, 7}:
        raise ValueError("unsupported power zone mapping")

    cap = igpsport_zones[-1].get("end")
    try:
        cap_value = int(cap)
    except (TypeError, ValueError) as exc:
        raise ValueError("iGPSPORT power terminal cap is invalid") from exc

    finite_percentages = (
        scheme.upper_bounds[:6]
        if len(igpsport_zones) == 7
        else scheme.upper_bounds[:4]
    )
    ftp = Decimal(str(scheme.threshold))
    ends = [
        _round_positive_half_up(ftp * Decimal(str(percentage)) / Decimal("100"))
        for percentage in finite_percentages
    ]
    if cap_value <= ends[-1]:
        raise ValueError("iGPSPORT power terminal cap must exceed finite boundaries")
    return _apply_end_values([*ends, cap_value], igpsport_zones)


def power_upper_bounds_watts(power_zones_pct: list[float], ftp: float) -> list[int]:
    """Convert intervals % FTP upper bounds to absolute watt boundaries."""
    if ftp <= 0 or not power_zones_pct:
        return []
    return [int(round(ftp * pct / 100)) for pct in power_zones_pct]


def hr_upper_bounds_bpm(hr_zones_bpm: list[float], max_hr: float) -> list[int]:
    """Return intervals HR zone upper bounds capped at max_hr."""
    if max_hr <= 0 or not hr_zones_bpm:
        return []
    cap = int(round(max_hr))
    bounds = [int(round(bpm)) for bpm in hr_zones_bpm]
    bounds[-1] = min(bounds[-1], cap)
    return bounds


def _prepare_intervals_ends(intervals_ends: list[int], slot_count: int, cap: int) -> list[int]:
    """Drop open-ended last intervals bound when extra iGPSPORT slots need the cap."""
    ends = list(intervals_ends)
    n = len(ends)
    p = slot_count
    pad_count = p - n
    if pad_count > 0 and n >= 2 and ends[-1] >= cap - pad_count:
        ends = ends[:-1]
    return ends


def _enforce_strictly_increasing(ends: list[int], cap: int) -> list[int]:
    """Ensure zone end values strictly increase and the last is capped."""
    if not ends:
        return ends
    out = [int(value) for value in ends]
    for index in range(1, len(out)):
        if out[index] <= out[index - 1]:
            out[index] = out[index - 1] + 1
    out[-1] = min(out[-1], cap)
    if len(out) > 1 and out[-1] <= out[-2]:
        index = len(out) - 2
        while index > 0 and out[index] >= out[index + 1]:
            out[index] = out[index + 1] - 1
            index -= 1
        if out[0] >= out[1]:
            out[0] = max(0, out[1] - 1)
    return out


def _slot_end_values(intervals_ends: list[int], slot_count: int, cap: int) -> list[int]:
    """Map intervals zone count onto a fixed iGPSPORT slot count."""
    if slot_count <= 0:
        return []
    if not intervals_ends:
        return list(range(cap, cap - slot_count, -1))

    ends = _prepare_intervals_ends(intervals_ends, slot_count, cap)
    n = len(ends)
    p = slot_count
    pad_count = p - n

    if n == p:
        result = list(ends)
    elif n < p:
        result = list(ends)
        for index in range(pad_count):
            result.append(cap - pad_count + 1 + index)
    else:
        result = list(ends[: p - 1])
        result.append(ends[-1])

    return _enforce_strictly_increasing(result, cap)


def _apply_end_values(
    end_values: list[int],
    igpsport_zones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rewrite start/end on iGPSPORT zone dicts, preserving other fields."""
    out: list[dict[str, Any]] = []
    start = 0
    for index, zone in enumerate(igpsport_zones):
        if not isinstance(zone, dict):
            continue
        updated = dict(zone)
        updated["start"] = start
        updated["end"] = end_values[index]
        start = end_values[index]
        out.append(updated)
    return out


def map_power_zones(
    intervals_pct: list[float],
    ftp: float,
    igpsport_zones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map intervals.icu power zones onto iGPSPORT power slots."""
    if not igpsport_zones:
        return []
    intervals_ends = power_upper_bounds_watts(intervals_pct, ftp)
    if (
        intervals_pct
        and intervals_pct[-1] >= OPEN_ZONE_SENTINEL_PCT
        and len(intervals_ends) > 1
    ):
        # Drop the open-ended zone's sentinel bound; POWER_LAST_ZONE_END covers it.
        intervals_ends = intervals_ends[:-1]
    slot_count = len(igpsport_zones)
    if slot_count == 1:
        slot_ends = [POWER_LAST_ZONE_END]
    else:
        interior = _slot_end_values(intervals_ends, slot_count - 1, POWER_INTERIOR_CAP)
        interior[-1] = min(interior[-1], POWER_INTERIOR_CAP)
        interior = _enforce_strictly_increasing(interior, POWER_INTERIOR_CAP)
        slot_ends = interior + [POWER_LAST_ZONE_END]
    return _apply_end_values(slot_ends, igpsport_zones)


def map_hr_zones(
    intervals_bpm: list[float],
    max_hr: float,
    igpsport_zones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map intervals.icu HR zones onto iGPSPORT heart-rate slots."""
    if not igpsport_zones:
        return []
    cap = int(round(max_hr))
    intervals_ends = hr_upper_bounds_bpm(intervals_bpm, max_hr)
    slot_ends = _slot_end_values(intervals_ends, len(igpsport_zones), cap)
    return _apply_end_values(slot_ends, igpsport_zones)
