from __future__ import annotations

import wave
from collections.abc import Callable
from pathlib import Path

import aaf2
import pytest
from aaf2.auid import AUID


@pytest.fixture
def make_aaf(tmp_path: Path) -> Callable[..., Path]:
    def factory(
        name: str = "fixture.aaf",
        *,
        compatible: bool = False,
        empty_composition: bool = False,
        with_embedded_pcm: bool = False,
        missing_edit_protocol_metadata: bool = False,
        missing_primary_timecode: bool = False,
    ) -> Path:
        path = tmp_path / name
        wave_path = tmp_path / f"{path.stem}.wav"
        if with_embedded_pcm:
            with wave.open(str(wave_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(3)
                audio.setframerate(48000)
                audio.writeframes(b"\x00\x00\x00" * 16)

        with aaf2.open(str(path), "w") as handle:
            composition = handle.create.CompositionMob()
            composition.name = "fixture-composition"
            if not missing_edit_protocol_metadata:
                composition.usage = AUID(
                    "0d010102-0101-0700-060e-2b3404010101"
                )
            handle.content.mobs.append(composition)
            if empty_composition:
                return path
            slot = composition.create_sound_slot(edit_rate=48000)
            if not missing_edit_protocol_metadata:
                slot.name = "Dialogue"
                slot["PhysicalTrackNumber"].value = 1
            sequence = slot.segment
            sequence.components.append(handle.create.Filler("sound", length=100))
            sequence.components.append(handle.create.Filler("sound", length=100))
            sequence.length = 200 if compatible else 0
            if not missing_primary_timecode:
                timecode_slot = composition.create_timeline_slot(edit_rate=25)
                timecode_slot.segment = handle.create.Timecode(
                    25,
                    drop=False,
                    length=1,
                )
                timecode_slot.name = "TC1"
                timecode_slot["PhysicalTrackNumber"].value = 1
            if with_embedded_pcm:
                master = handle.create.MasterMob("fixture-audio")
                handle.content.mobs.append(master)
                master.import_audio_essence(str(wave_path))
                if not missing_edit_protocol_metadata:
                    for mob in handle.content.mobs:
                        for mob_slot in mob.slots:
                            if mob_slot.media_kind == "Sound":
                                mob_slot.name = mob_slot.name or "A1"
                                mob_slot["PhysicalTrackNumber"].value = 1
        return path

    return factory
