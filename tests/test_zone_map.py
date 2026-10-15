"""Tests for intervals.icu → iGPSPORT zone mapping."""

from __future__ import annotations

import dataclasses

import pytest

from intervalssync.igpsport.zone_map import (
    POWER_INTERIOR_CAP,
    POWER_LAST_ZONE_END,
    ZoneModel,
    detect_hr_zone_scheme,
    detect_power_zone_scheme,
    hr_upper_bounds_bpm,
    map_hr_zones,
    map_power_zones,
    map_strict_hr_zones,
    map_strict_power_zones,
    power_upper_bounds_watts,
    recognize_zone_model,
)


def _template_zones(count: int) -> list[dict]:
    return [{"id": index, "start": 0, "end": 0} for index in range(count)]


# settings-ride.json reference values
FTP = 242
POWER_PCT = [55, 75, 90, 105, 120, 999]
HR_BPM = [120, 146, 166, 185, 193]
MAX_HR = 193
FRIEL_NAMES = [
    "Recovery",
    "Aerobic",
    "Tempo",
    "SubThreshold",
    "SuperThreshold",
    "Aerobic Capacity",
    "Anaerobic",
]
COGGAN_NAMES = [
    "Active Recovery",
    "Endurance",
    "Tempo",
    "Lactate Threshold",
    "VO2 Max",
    "Anaerobic Capacity",
    "Neuromuscular Power",
]
FRIEL_BPM = [120, 146, 166, 175, 185, 190, 193]
COGGAN_PCT = [55, 75, 90, 105, 120, 150, 999]


def test_recognize_zone_model_normalizes_names_and_narrow_aliases():
    friel_names = [
        "1. RECOVERY",
        "Zone 2 - Aerobic",
        "z3: Tempo",
        "4 Sub Threshold",
        "Zone 5a Super-Threshold",
        "Z5b Aerobic Capacity",
        "5c Anaerobic",
    ]
    coggan_names = [
        "Z1 Active Recovery",
        "2. Endurance",
        "Zone 3: TEMPO",
        "4 Lactate Threshold",
        "Z5 VO2 Max",
        "6 Anaerobic Capacity",
        "Zone 7 - Neuromuscular Power",
    ]

    assert recognize_zone_model("hr", friel_names) is ZoneModel.FRIEL_7
    assert recognize_zone_model("power", coggan_names) is ZoneModel.COGGAN_7
    assert recognize_zone_model(
        "hr", ["Recovery", "Endurance", "Tempo", "Threshold", "VO2"]
    ) is ZoneModel.DIRECT_5
    assert recognize_zone_model("hr", ["One", "Two", "Three"]) is ZoneModel.UNKNOWN


@pytest.mark.parametrize(
    ("bounds", "lthr", "max_hr"),
    [
        ([120, 146, 145, 175, 185, 190, 193], 180, 193),
        (FRIEL_BPM, 0, 193),
        (FRIEL_BPM, 180, 0),
        (FRIEL_BPM, 174, 193),
        (FRIEL_BPM, 186, 193),
    ],
)
def test_detect_hr_zone_scheme_requires_valid_boundaries_and_thresholds(
    bounds, lthr, max_hr
):
    scheme = detect_hr_zone_scheme(FRIEL_NAMES, bounds, lthr=lthr, max_hr=max_hr)

    assert scheme.model is ZoneModel.UNKNOWN


def test_detect_hr_zone_scheme_is_immutable_when_valid():
    scheme = detect_hr_zone_scheme(FRIEL_NAMES, FRIEL_BPM, lthr=180, max_hr=193)

    assert scheme.model is ZoneModel.FRIEL_7
    assert scheme.upper_bounds == tuple(FRIEL_BPM)
    with pytest.raises(dataclasses.FrozenInstanceError):
        scheme.model = ZoneModel.UNKNOWN


def test_detect_hr_zone_scheme_accepts_open_final_band_ending_at_max_hr():
    open_ended = [120, 146, 166, 175, 185, 190, 999]

    scheme = detect_hr_zone_scheme(
        FRIEL_NAMES, open_ended, lthr=180, max_hr=193
    )

    assert scheme.model is ZoneModel.FRIEL_7
    assert map_strict_hr_zones(
        scheme, max_hr=193, igpsport_zones=_template_zones(5)
    )[-1]["end"] == 193


def test_detect_direct_five_hr_without_lthr_preserves_explicit_bpm_shape():
    names = ["Recovery", "Endurance", "Tempo", "Threshold", "VO2 Max"]
    bounds = [120, 145, 165, 180, 193]

    scheme = detect_hr_zone_scheme(names, bounds, lthr=None, max_hr=193)

    assert scheme.model is ZoneModel.DIRECT_5
    assert scheme.basis == "custom"


@pytest.mark.parametrize(
    ("bounds", "ftp"),
    [
        ([55, 75, 90, 89, 120, 150, 999], 242),
        (COGGAN_PCT, 0),
        ([55, 75, 90, 105, 120, 150, 151], 242),
    ],
)
def test_detect_power_zone_scheme_requires_ordered_bounds_positive_ftp_and_open_top(
    bounds, ftp
):
    scheme = detect_power_zone_scheme(COGGAN_NAMES, bounds, ftp=ftp)

    assert scheme.model is ZoneModel.UNKNOWN


def test_detect_power_zone_scheme_accepts_valid_coggan_shape():
    scheme = detect_power_zone_scheme(COGGAN_NAMES, COGGAN_PCT, ftp=242)

    assert scheme.model is ZoneModel.COGGAN_7


def test_map_strict_friel_seven_to_five_uses_semantic_union_without_interpolation():
    scheme = detect_hr_zone_scheme(FRIEL_NAMES, FRIEL_BPM, lthr=180, max_hr=193)

    zones = map_strict_hr_zones(scheme, max_hr=193, igpsport_zones=_template_zones(5))

    assert [zone["end"] for zone in zones] == [120, 146, 166, 175, 193]
    assert [zone["start"] for zone in zones] == [0, 120, 146, 166, 175]


def test_map_strict_coggan_seven_to_seven_rounds_half_up_and_preserves_cap():
    scheme = detect_power_zone_scheme(COGGAN_NAMES, COGGAN_PCT, ftp=230)
    template = _template_zones(7)
    template[-1]["end"] = 2000

    zones = map_strict_power_zones(scheme, igpsport_zones=template)

    assert [zone["end"] for zone in zones] == [127, 173, 207, 242, 276, 345, 2000]
    assert [zone["start"] for zone in zones] == [0, 127, 173, 207, 242, 276, 345]


def test_map_strict_power_never_multiplies_open_sentinel():
    scheme = detect_power_zone_scheme(COGGAN_NAMES, COGGAN_PCT, ftp=200)
    template = _template_zones(7)
    template[-1]["end"] = 1777

    zones = map_strict_power_zones(scheme, igpsport_zones=template)

    assert zones[-1]["end"] == 1777
    assert all(zone["end"] != 1998 for zone in zones)


def test_map_strict_coggan_seven_to_five_uses_semantic_union():
    scheme = detect_power_zone_scheme(COGGAN_NAMES, COGGAN_PCT, ftp=200)
    template = _template_zones(5)
    template[-1]["end"] = 2000

    zones = map_strict_power_zones(scheme, igpsport_zones=template)

    assert [zone["end"] for zone in zones] == [110, 150, 180, 210, 2000]


@pytest.mark.parametrize("slot_count", [0, 4, 6, 8])
def test_map_strict_power_rejects_unsupported_destination_shapes(slot_count):
    scheme = detect_power_zone_scheme(COGGAN_NAMES, COGGAN_PCT, ftp=200)

    with pytest.raises(ValueError, match="unsupported power zone mapping"):
        map_strict_power_zones(scheme, igpsport_zones=_template_zones(slot_count))


def test_map_strict_adapters_reject_unknown_source_models():
    unknown_hr = detect_hr_zone_scheme(
        ["One"] * 7, FRIEL_BPM, lthr=180, max_hr=193
    )
    unknown_power = detect_power_zone_scheme(
        ["One"] * 7, COGGAN_PCT, ftp=200
    )

    with pytest.raises(ValueError, match="unsupported HR zone model"):
        map_strict_hr_zones(
            unknown_hr, max_hr=193, igpsport_zones=_template_zones(5)
        )
    with pytest.raises(ValueError, match="unsupported power zone model"):
        map_strict_power_zones(
            unknown_power, igpsport_zones=_template_zones(7)
        )


def test_power_upper_bounds_from_settings_ride():
    assert power_upper_bounds_watts(POWER_PCT, FTP) == [133, 182, 218, 254, 290, 2418]


def test_hr_upper_bounds_from_settings_ride():
    assert hr_upper_bounds_bpm(HR_BPM, MAX_HR) == [120, 146, 166, 185, 193]


def test_map_power_same_count_seven_slots():
    zones = map_power_zones(POWER_PCT, FTP, _template_zones(6))
    assert len(zones) == 6
    assert zones[0]["start"] == 0 and zones[0]["end"] == 133
    assert zones[-1]["end"] == POWER_LAST_ZONE_END
    assert zones[-2]["end"] <= POWER_INTERIOR_CAP


def test_map_power_fewer_intervals_than_igpsport():
    zones = map_power_zones(POWER_PCT, FTP, _template_zones(7))
    assert len(zones) == 7
    assert zones[4]["end"] == 290
    assert zones[-1]["end"] == POWER_LAST_ZONE_END
    assert zones[-2]["end"] <= POWER_INTERIOR_CAP
    assert zones[-1]["start"] == zones[-2]["end"]


def test_map_power_more_intervals_than_igpsport():
    intervals_pct = [55, 65, 75, 85, 90, 105, 120]
    zones = map_power_zones(intervals_pct, FTP, _template_zones(5))
    assert len(zones) == 5
    assert zones[3]["end"] == int(round(FTP * 1.20))
    assert zones[3]["end"] <= POWER_INTERIOR_CAP
    assert zones[4]["start"] == zones[3]["end"]
    assert zones[4]["end"] == POWER_LAST_ZONE_END


def test_map_hr_same_count():
    zones = map_hr_zones(HR_BPM, MAX_HR, _template_zones(5))
    assert [zone["end"] for zone in zones] == [120, 146, 166, 185, 193]


def test_map_hr_fewer_intervals_than_igpsport():
    zones = map_hr_zones(HR_BPM[:3], MAX_HR, _template_zones(5))
    assert zones[2]["end"] == 166
    assert zones[-1]["end"] == MAX_HR
    assert zones[-2]["end"] == MAX_HR - 1


def test_map_hr_more_intervals_than_igpsport():
    intervals = [110, 130, 150, 165, 175, 185, MAX_HR]
    zones = map_hr_zones(intervals, MAX_HR, _template_zones(5))
    assert zones[3]["end"] == 165
    assert zones[4]["start"] == 165
    assert zones[4]["end"] == MAX_HR


def test_map_power_same_count_as_igpsport_with_open_zone():
    """intervals.icu zone count (incl. open-zone sentinel) equals iGPSPORT slot
    count exactly — the sentinel must be dropped, not treated as a real bound."""
    intervals_pct = [55, 75, 90, 105, 120, 150, 999]
    zones = map_power_zones(intervals_pct, 200, _template_zones(7))
    assert [zone["end"] for zone in zones] == [110, 150, 180, 210, 240, 300, POWER_LAST_ZONE_END]


def test_map_power_dedupes_equal_boundaries():
    zones = map_power_zones([55, 55, 75, 90, 105, 120], FTP, _template_zones(6))
    assert zones[0]["end"] == 133
    assert zones[1]["end"] > zones[0]["end"]


def test_map_power_preserves_zone_metadata():
    template = [{"id": "zone-a", "color": "#fff", "start": 99, "end": 999}]
    zones = map_power_zones([55], FTP, template)
    assert zones[0]["id"] == "zone-a"
    assert zones[0]["color"] == "#fff"
