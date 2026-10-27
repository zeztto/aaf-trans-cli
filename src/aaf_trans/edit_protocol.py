from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from itertools import chain
from typing import Any

from aaf2.auid import AUID
from aaf2.mobslots import TimelineMobSlot

from .models import Diagnostic

TOP_LEVEL_USAGE_MISSING = "TOP_LEVEL_USAGE_MISSING"
TOP_LEVEL_USAGE_AMBIGUOUS = "TOP_LEVEL_USAGE_AMBIGUOUS"
SOUND_TRACK_NAME_MISSING = "SOUND_TRACK_NAME_MISSING"
SOUND_TRACK_NAME_DUPLICATE = "SOUND_TRACK_NAME_DUPLICATE"
SOUND_PHYSICAL_TRACK_NUMBER_MISSING = "SOUND_PHYSICAL_TRACK_NUMBER_MISSING"
SOUND_PHYSICAL_TRACK_NUMBER_INVALID = "SOUND_PHYSICAL_TRACK_NUMBER_INVALID"
PRIMARY_TIMECODE_MISSING = "PRIMARY_TIMECODE_MISSING"
PRIMARY_TIMECODE_AMBIGUOUS = "PRIMARY_TIMECODE_AMBIGUOUS"
USAGE_TOP_LEVEL_AUID = AUID("0d010102-0101-0700-060e-2b3404010101")
VALID_TIMECODE_FPS = (24, 25, 30)


@dataclass(frozen=True)
class EditProtocolState:
    top_level_compositions: tuple[Any, ...]
    top_level_timecode_tracks: int
    primary_timecode_tracks: int
    sound_tracks: int
    sound_track_names_missing: int
    sound_physical_track_numbers_missing: int
    diagnostics: tuple[Diagnostic, ...]


def usage_auid(mob: Any) -> AUID | None:
    data = mob["UsageCode"].data
    return AUID(bytes_le=data) if data is not None else None


def _sound_slots(mob: Any) -> list[Any]:
    return [
        slot
        for slot in mob.slots
        if isinstance(slot, TimelineMobSlot) and slot.media_kind == "Sound"
    ]


def _timecode_slots(mob: Any) -> list[Any]:
    return [
        slot
        for slot in mob.slots
        if isinstance(slot, TimelineMobSlot) and slot.media_kind == "Timecode"
    ]


def _physical_track_number(slot: Any) -> int | None:
    value = slot["PhysicalTrackNumber"].value
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _timecode_target(
    composition_mobs: list[Any],
    top_level_compositions: tuple[Any, ...],
) -> Any | None:
    if len(top_level_compositions) == 1:
        return top_level_compositions[0]
    if (
        not top_level_compositions
        and len(composition_mobs) == 1
        and usage_auid(composition_mobs[0]) is None
    ):
        return composition_mobs[0]
    return None


def _primary_timecode_diagnostics(
    composition_mobs: list[Any],
    top_level_compositions: tuple[Any, ...],
) -> tuple[int, int, list[Diagnostic]]:
    target = _timecode_target(composition_mobs, top_level_compositions)
    if target is None:
        return 0, 0, []
    if len(target.slots) == 0:
        return 0, 0, []

    slots = _timecode_slots(target)
    primary_slots = [
        slot for slot in slots if _physical_track_number(slot) == 1
    ]
    if len(primary_slots) == 1:
        return len(slots), 1, []

    if not slots:
        return (
            0,
            0,
            [
                Diagnostic(
                    code=PRIMARY_TIMECODE_MISSING,
                    severity="error",
                    message=(
                        "Top-level CompositionMob에 Primary Timecode track이 없습니다. "
                        "기본 25 fps, non-drop, start 0으로 보강할 수 있습니다."
                    ),
                    fixable=True,
                    mob_name=target.name,
                    media_kind="timecode",
                    affected_count=1,
                )
            ],
        )

    return (
        len(slots),
        len(primary_slots),
        [
            Diagnostic(
                code=PRIMARY_TIMECODE_AMBIGUOUS,
                severity="error",
                message=(
                    "Top-level CompositionMob에 Timecode track이 있지만 Primary "
                    "PhysicalTrackNumber=1을 하나로 결정할 수 없습니다."
                ),
                fixable=False,
                mob_name=target.name,
                media_kind="timecode",
                affected_count=len(slots),
            )
        ],
    )


def add_primary_timecode(handle: Any, mob: Any, timecode_fps: int) -> Any:
    """Add a missing Primary Timecode track using the selected FPS."""
    if timecode_fps not in VALID_TIMECODE_FPS:
        raise ValueError(f"unsupported timecode fps: {timecode_fps}")
    if _timecode_slots(mob):
        raise ValueError("top-level CompositionMob already has a Timecode track")

    durations: list[Fraction] = []
    for slot in mob.slots:
        segment = slot.segment
        if segment is None:
            raise ValueError(f"slot {slot.slot_id} has no Segment")
        length = segment.length
        if isinstance(length, bool) or not isinstance(length, int) or length < 0:
            raise ValueError(f"slot {slot.slot_id} has invalid length {length!r}")
        edit_rate = Fraction(str(slot.edit_rate))
        if edit_rate <= 0:
            raise ValueError(f"slot {slot.slot_id} has invalid edit rate {edit_rate}")
        durations.append(Fraction(length, 1) / edit_rate)

    if not durations:
        raise ValueError("top-level CompositionMob has no media track")

    timecode_length = math.ceil(max(durations) * timecode_fps)
    slot = mob.create_timeline_slot(edit_rate=timecode_fps)
    slot.segment = handle.create.Timecode(
        timecode_fps,
        drop=False,
        length=timecode_length,
    )
    slot.name = "TC1"
    slot["PhysicalTrackNumber"].value = 1
    slot.segment.start = 0
    return slot


def sound_track_metadata_state(
    mobs: Iterable[Any],
) -> tuple[int, int, int, int, int]:
    sound_tracks = 0
    missing_names = 0
    duplicate_names = 0
    missing_physical_numbers = 0
    invalid_physical_numbers = 0

    for mob in mobs:
        slots = _sound_slots(mob)
        sound_tracks += len(slots)

        normalized_names = [
            slot.name.strip().casefold()
            for slot in slots
            if isinstance(slot.name, str) and slot.name.strip()
        ]
        name_counts = Counter(normalized_names)
        missing_names += sum(
            not isinstance(slot.name, str) or not slot.name.strip() for slot in slots
        )
        duplicate_names += sum(count - 1 for count in name_counts.values() if count > 1)

        physical_numbers = [slot["PhysicalTrackNumber"].value for slot in slots]
        present_numbers = [number for number in physical_numbers if number is not None]
        missing_physical_numbers += len(physical_numbers) - len(present_numbers)
        number_counts = Counter(present_numbers)
        invalid_physical_numbers += sum(
            isinstance(number, bool)
            or not isinstance(number, int)
            or number < 1
            or number > len(slots)
            for number in present_numbers
        )
        invalid_physical_numbers += sum(
            count - 1 for count in number_counts.values() if count > 1
        )

    return (
        sound_tracks,
        missing_names,
        duplicate_names,
        missing_physical_numbers,
        invalid_physical_numbers,
    )


def _top_level_diagnostics(
    composition_mobs: list[Any],
    top_level_compositions: tuple[Any, ...],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if not top_level_compositions and composition_mobs:
        sole_composition_without_usage = (
            len(composition_mobs) == 1 and usage_auid(composition_mobs[0]) is None
        )
        diagnostics.append(
            Diagnostic(
                code=(
                    TOP_LEVEL_USAGE_MISSING
                    if sole_composition_without_usage
                    else TOP_LEVEL_USAGE_AMBIGUOUS
                ),
                severity="error",
                message=(
                    "유일한 CompositionMob의 UsageCode가 없습니다. "
                    "Edit Protocol의 Usage_TopLevel로 보강할 수 있습니다."
                    if sole_composition_without_usage
                    else "Usage_TopLevel CompositionMob을 하나로 결정할 수 없습니다."
                ),
                fixable=sole_composition_without_usage,
                affected_count=1,
            )
        )
    elif len(top_level_compositions) > 1:
        diagnostics.append(
            Diagnostic(
                code=TOP_LEVEL_USAGE_AMBIGUOUS,
                severity="error",
                message="Usage_TopLevel CompositionMob이 둘 이상입니다.",
                fixable=False,
                affected_count=len(top_level_compositions),
            )
        )
    return diagnostics


def _track_name_diagnostics(
    missing_names: int,
    duplicate_names: int,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if missing_names:
        diagnostics.append(
            Diagnostic(
                code=SOUND_TRACK_NAME_MISSING,
                severity="error",
                message=(
                    "Sound essence track의 SlotName이 비어 있습니다. "
                    "Edit Protocol 호환 A1, A2 순서로 보강할 수 있습니다."
                ),
                fixable=True,
                media_kind="sound",
                affected_count=missing_names,
            )
        )

    if duplicate_names:
        diagnostics.append(
            Diagnostic(
                code=SOUND_TRACK_NAME_DUPLICATE,
                severity="error",
                message="같은 Mob 안에 중복된 Sound essence track SlotName이 있습니다.",
                fixable=False,
                media_kind="sound",
                affected_count=duplicate_names,
            )
        )
    return diagnostics


def _physical_number_diagnostics(
    missing_physical_numbers: int,
    invalid_physical_numbers: int,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if missing_physical_numbers:
        diagnostics.append(
            Diagnostic(
                code=SOUND_PHYSICAL_TRACK_NUMBER_MISSING,
                severity="error",
                message=(
                    "Sound essence track의 PhysicalTrackNumber가 없습니다. "
                    "Mob 안의 audio track 순서대로 보강할 수 있습니다."
                ),
                fixable=True,
                media_kind="sound",
                affected_count=missing_physical_numbers,
            )
        )

    if invalid_physical_numbers:
        diagnostics.append(
            Diagnostic(
                code=SOUND_PHYSICAL_TRACK_NUMBER_INVALID,
                severity="error",
                message=(
                    "Sound essence track의 PhysicalTrackNumber가 중복되거나 "
                    "track 수 범위를 벗어납니다."
                ),
                fixable=False,
                media_kind="sound",
                affected_count=invalid_physical_numbers,
            )
        )
    return diagnostics


def analyze_edit_protocol(
    composition_mobs: list[Any],
    master_mobs: list[Any],
    source_mobs: list[Any],
) -> EditProtocolState:
    top_level_compositions = tuple(
        mob for mob in composition_mobs if usage_auid(mob) == USAGE_TOP_LEVEL_AUID
    )
    (
        top_level_timecode_tracks,
        primary_timecode_tracks,
        timecode_diagnostics,
    ) = _primary_timecode_diagnostics(
        composition_mobs,
        top_level_compositions,
    )
    (
        sound_tracks,
        missing_names,
        duplicate_names,
        missing_physical_numbers,
        invalid_physical_numbers,
    ) = sound_track_metadata_state(chain(composition_mobs, master_mobs, source_mobs))
    diagnostics = _top_level_diagnostics(
        composition_mobs,
        top_level_compositions,
    )
    diagnostics.extend(timecode_diagnostics)
    diagnostics.extend(_track_name_diagnostics(missing_names, duplicate_names))
    diagnostics.extend(
        _physical_number_diagnostics(
            missing_physical_numbers,
            invalid_physical_numbers,
        )
    )
    return EditProtocolState(
        top_level_compositions=top_level_compositions,
        top_level_timecode_tracks=top_level_timecode_tracks,
        primary_timecode_tracks=primary_timecode_tracks,
        sound_tracks=sound_tracks,
        sound_track_names_missing=missing_names,
        sound_physical_track_numbers_missing=missing_physical_numbers,
        diagnostics=tuple(diagnostics),
    )


def repair_sound_track_metadata(mobs: Iterable[Any]) -> tuple[int, int]:
    names_changed = 0
    physical_numbers_changed = 0

    for mob in mobs:
        slots = _sound_slots(mob)
        used_names = {
            slot.name.strip().casefold()
            for slot in slots
            if isinstance(slot.name, str) and slot.name.strip()
        }
        next_name_number = 1
        for slot in slots:
            if isinstance(slot.name, str) and slot.name.strip():
                continue
            while f"A{next_name_number}".casefold() in used_names:
                next_name_number += 1
            name = f"A{next_name_number}"
            slot.name = name
            used_names.add(name.casefold())
            next_name_number += 1
            names_changed += 1

        used_physical_numbers = {
            slot["PhysicalTrackNumber"].value
            for slot in slots
            if slot["PhysicalTrackNumber"].value is not None
        }
        available_numbers = iter(
            number
            for number in range(1, len(slots) + 1)
            if number not in used_physical_numbers
        )
        for slot in slots:
            if slot["PhysicalTrackNumber"].value is not None:
                continue
            slot["PhysicalTrackNumber"].value = next(available_numbers)
            physical_numbers_changed += 1

    return names_changed, physical_numbers_changed
