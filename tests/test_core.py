from __future__ import annotations

import os
from pathlib import Path
from stat import S_IMODE
from types import SimpleNamespace

import pytest
from aaf2.components import Transition

from aaf_trans.core import (
    InputValidationError,
    OutputExistsError,
    UnfixableAAFError,
    analyze_aaf,
    calculate_sequence_length,
    convert_aaf,
    discover_aaf_inputs,
    sha256_file,
)


def test_analyze_detects_sequence_length_mismatch(make_aaf) -> None:
    source = make_aaf()

    report = analyze_aaf(source)

    assert report.compatible is False
    assert report.composition_mobs == 1
    assert report.composition_slots == 2
    assert report.sequence_count == 1
    assert [item.code for item in report.diagnostics] == ["SEQUENCE_LENGTH_MISMATCH"]
    issue = report.diagnostics[0]
    assert issue.stored_length == 0
    assert issue.calculated_length == 200
    assert issue.component_count == 2


def test_analyze_rejects_composition_without_tracks(make_aaf) -> None:
    source = make_aaf(empty_composition=True)

    report = analyze_aaf(source)

    assert report.compatible is False
    assert [item.code for item in report.diagnostics] == ["NO_COMPOSITION_SLOTS"]


def test_analyze_detects_missing_embedded_pcm_metadata(make_aaf) -> None:
    source = make_aaf(compatible=True, with_embedded_pcm=True)

    report = analyze_aaf(source)

    assert report.compatible is False
    assert report.pcm_descriptors == 1
    assert report.embedded_pcm_descriptors == 1
    assert [item.code for item in report.diagnostics] == [
        "PCM_CONTAINER_FORMAT_MISSING",
        "PCM_CODEC_DEFINITION_MISSING",
    ]
    assert [item.affected_count for item in report.diagnostics] == [1, 1]
    assert all(item.fixable for item in report.diagnostics)


def test_analyze_detects_missing_edit_protocol_track_metadata(make_aaf) -> None:
    source = make_aaf(
        compatible=True,
        missing_edit_protocol_metadata=True,
    )

    report = analyze_aaf(source)

    assert report.compatible is False
    assert report.top_level_compositions == 0
    assert report.sound_tracks == 1
    assert report.sound_track_names_missing == 1
    assert report.sound_physical_track_numbers_missing == 1
    assert [item.code for item in report.diagnostics] == [
        "TOP_LEVEL_USAGE_MISSING",
        "SOUND_TRACK_NAME_MISSING",
        "SOUND_PHYSICAL_TRACK_NUMBER_MISSING",
    ]
    assert all(item.fixable for item in report.diagnostics)


def test_analyze_detects_missing_primary_timecode(make_aaf) -> None:
    source = make_aaf(
        compatible=True,
        missing_primary_timecode=True,
    )

    report = analyze_aaf(source)

    assert report.compatible is False
    assert report.top_level_timecode_tracks == 0
    assert report.primary_timecode_tracks == 0
    assert [item.code for item in report.diagnostics] == [
        "PRIMARY_TIMECODE_MISSING"
    ]
    assert report.diagnostics[0].fixable is True


def test_sequence_length_subtracts_transition_overlap() -> None:
    class TransitionStub(Transition):
        @property
        def length(self) -> int:
            return 20

    transition = object.__new__(TransitionStub)
    sequence = SimpleNamespace(
        components=[
            SimpleNamespace(length=100),
            transition,
            SimpleNamespace(length=100),
        ]
    )

    assert calculate_sequence_length(sequence) == 180


def test_convert_repairs_copy_and_preserves_source(make_aaf, tmp_path: Path) -> None:
    source = make_aaf()
    output = tmp_path / "converted.aaf"
    source_hash = sha256_file(source)
    before = analyze_aaf(source)

    result = convert_aaf(source, output)

    assert result.status == "converted"
    assert result.sequences_fixed == 1
    assert result.pcm_container_formats_fixed == 0
    assert result.pcm_codec_definitions_fixed == 0
    assert result.codec_definitions_added == 0
    assert result.output == output
    assert output.exists()
    assert sha256_file(source) == source_hash
    assert analyze_aaf(source).compatible is False
    after = analyze_aaf(output)
    assert after.compatible is True
    assert before.timeline_fingerprint == after.timeline_fingerprint
    assert result.output_report is not None
    assert result.output_report.path == output


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
@pytest.mark.parametrize("source_mode", [0o444, 0o400, 0o644], ids=["0444", "0400", "0644"])
def test_convert_preserves_source_mode_and_uses_private_output(
    make_aaf,
    tmp_path: Path,
    source_mode: int,
) -> None:
    source = make_aaf(with_embedded_pcm=True, missing_primary_timecode=True)
    source.chmod(source_mode)
    source_hash = sha256_file(source)
    before = analyze_aaf(source, include_essence_fingerprint=True)
    output = tmp_path / "output" / "converted.aaf"

    result = convert_aaf(source, output)

    assert result.status == "converted"
    assert result.sequences_fixed == 1
    assert result.primary_timecode_tracks_added == 1
    assert sha256_file(source) == source_hash
    assert S_IMODE(source.stat().st_mode) == source_mode
    assert analyze_aaf(source).compatible is False
    assert S_IMODE(output.stat().st_mode) == 0o600
    after = analyze_aaf(output, include_essence_fingerprint=True)
    assert after.compatible is True
    assert after.media_timeline_structure_fingerprint == before.media_timeline_structure_fingerprint
    assert before.essence_fingerprint is not None
    assert after.essence_fingerprint == before.essence_fingerprint
    assert not list(output.parent.glob(f".{output.name}.*.aaf"))


def test_convert_repairs_embedded_pcm_metadata(make_aaf, tmp_path: Path) -> None:
    source = make_aaf(compatible=True, with_embedded_pcm=True)
    output = tmp_path / "converted-pcm.aaf"

    result = convert_aaf(source, output, verify_essence=True)

    assert result.status == "converted"
    assert result.sequences_fixed == 0
    assert result.pcm_container_formats_fixed == 1
    assert result.pcm_codec_definitions_fixed == 1
    assert result.codec_definitions_added == 1
    assert analyze_aaf(output).compatible is True

    import aaf2
    from aaf2.auid import AUID

    with aaf2.open(str(output), "r") as handle:
        codec = handle.dictionary.lookup_codecdef(
            AUID("90ac17c8-e3e2-4596-9e9e-a6dd1c70c892")
        )
        assert codec is not None
        assert codec.name == "PCM Codec"
        assert codec["FileDescriptorClass"].value.class_name == "PCMDescriptor"
        assert {item.name for item in codec["DataDefinitions"].value} == {
            "DataDef_Sound",
            "DataDef_LegacySound",
        }
        source_mob = next(iter(handle.content.sourcemobs()))
        assert source_mob.descriptor["ContainerFormat"].value.name == "ContainerDef_AAF"
        assert source_mob.descriptor["CodecDefinition"].value.auid == codec.auid


def test_convert_repairs_edit_protocol_track_metadata(
    make_aaf,
    tmp_path: Path,
) -> None:
    source = make_aaf(
        compatible=True,
        missing_edit_protocol_metadata=True,
    )
    output = tmp_path / "converted-edit-protocol.aaf"
    before = analyze_aaf(source)

    result = convert_aaf(source, output)

    assert result.composition_usage_codes_fixed == 1
    assert result.sound_track_names_fixed == 1
    assert result.sound_physical_track_numbers_fixed == 1
    after = analyze_aaf(output)
    assert after.compatible is True
    assert before.timeline_fingerprint != after.timeline_fingerprint
    assert (
        before.timeline_structure_fingerprint
        == after.timeline_structure_fingerprint
    )

    import aaf2
    from aaf2.auid import AUID

    with aaf2.open(str(output), "r") as handle:
        composition = next(iter(handle.content.compositionmobs()))
        slot = composition.slots[0]
        assert composition["UsageCode"].data is not None
        assert AUID(bytes_le=composition["UsageCode"].data) == AUID(
            "0d010102-0101-0700-060e-2b3404010101"
        )
        assert slot.name == "A1"
        assert slot["PhysicalTrackNumber"].value == 1


def test_convert_adds_primary_timecode_with_default_profile(
    make_aaf,
    tmp_path: Path,
) -> None:
    import aaf2

    source = make_aaf(
        compatible=True,
        missing_primary_timecode=True,
    )
    output = tmp_path / "converted-timecode.aaf"
    before = analyze_aaf(source)

    result = convert_aaf(source, output)

    assert result.primary_timecode_tracks_added == 1
    after = analyze_aaf(output)
    assert after.compatible is True
    assert after.top_level_timecode_tracks == 1
    assert after.primary_timecode_tracks == 1
    assert after.composition_slots == before.composition_slots + 1
    assert (
        before.media_timeline_structure_fingerprint
        == after.media_timeline_structure_fingerprint
    )
    assert (
        before.timeline_structure_fingerprint
        != after.timeline_structure_fingerprint
    )

    with aaf2.open(str(output), "r") as handle:
        composition = next(iter(handle.content.compositionmobs()))
        timecode_slots = [
            slot for slot in composition.slots if slot.media_kind == "Timecode"
        ]
        assert len(timecode_slots) == 1
        slot = timecode_slots[0]
        assert str(slot.edit_rate) == "25"
        assert slot.name == "TC1"
        assert slot["PhysicalTrackNumber"].value == 1
        assert slot.segment.fps == 25
        assert slot.segment.drop is False
        assert slot.segment.start == 0
        assert slot.segment.length == 1


def test_convert_supports_explicit_primary_timecode_fps(
    make_aaf,
    tmp_path: Path,
) -> None:
    import aaf2

    source = make_aaf(
        compatible=True,
        missing_primary_timecode=True,
    )
    output = tmp_path / "converted-timecode-30.aaf"

    result = convert_aaf(source, output, timecode_fps=30)

    assert result.primary_timecode_tracks_added == 1
    with aaf2.open(str(output), "r") as handle:
        composition = next(iter(handle.content.compositionmobs()))
        slot = next(
            slot for slot in composition.slots if slot.media_kind == "Timecode"
        )
        assert str(slot.edit_rate) == "30"
        assert slot.segment.fps == 30


def test_convert_rejects_unsupported_primary_timecode_fps(
    make_aaf,
    tmp_path: Path,
) -> None:
    source = make_aaf(
        compatible=True,
        missing_primary_timecode=True,
    )
    output = tmp_path / "must-not-exist.aaf"

    with pytest.raises(InputValidationError, match="24, 25, 30"):
        convert_aaf(source, output, timecode_fps=29)

    assert not output.exists()


def test_convert_refuses_ambiguous_existing_timecode_tracks(
    make_aaf,
    tmp_path: Path,
) -> None:
    import aaf2

    source = make_aaf(
        compatible=True,
        missing_primary_timecode=True,
    )
    with aaf2.open(str(source), "rw") as handle:
        composition = next(iter(handle.content.compositionmobs()))
        slot = composition.create_timeline_slot(edit_rate=25)
        slot.segment = handle.create.Timecode(25, drop=False, length=1)
        slot.name = "Alternative TC"
        slot["PhysicalTrackNumber"].value = 2

    report = analyze_aaf(source)

    assert "PRIMARY_TIMECODE_AMBIGUOUS" in {
        item.code for item in report.unfixable_errors
    }
    with pytest.raises(UnfixableAAFError, match="PRIMARY_TIMECODE_AMBIGUOUS"):
        convert_aaf(source, tmp_path / "must-not-exist.aaf")


def test_convert_fills_unused_in_range_physical_track_number(
    make_aaf,
    tmp_path: Path,
) -> None:
    import aaf2

    source = make_aaf(compatible=True)
    with aaf2.open(str(source), "rw") as handle:
        composition = next(iter(handle.content.compositionmobs()))
        first_slot = composition.slots[0]
        first_slot["PhysicalTrackNumber"].value = 2
        second_slot = composition.create_sound_slot(edit_rate=48000)
        second_slot.name = "Music"
        second_slot.segment.components.append(
            handle.create.Filler("sound", length=100)
        )
        second_slot.segment.length = 100

    output = tmp_path / "converted-partial-number.aaf"
    result = convert_aaf(source, output)

    assert result.sound_physical_track_numbers_fixed == 1
    with aaf2.open(str(output), "r") as handle:
        composition = next(iter(handle.content.compositionmobs()))
        assert [
            slot["PhysicalTrackNumber"].value
            for slot in composition.slots
            if slot.media_kind == "Sound"
        ] == [2, 1]


def test_convert_refuses_ambiguous_top_level_composition(
    make_aaf,
    tmp_path: Path,
) -> None:
    import aaf2

    source = make_aaf(
        compatible=True,
        missing_edit_protocol_metadata=True,
    )
    with aaf2.open(str(source), "rw") as handle:
        second = handle.create.CompositionMob("second-composition")
        handle.content.mobs.append(second)
        slot = second.create_sound_slot(edit_rate=48000)
        slot.name = "A2"
        slot["PhysicalTrackNumber"].value = 1

    report = analyze_aaf(source)

    assert "TOP_LEVEL_USAGE_AMBIGUOUS" in {
        item.code for item in report.unfixable_errors
    }
    with pytest.raises(UnfixableAAFError, match="TOP_LEVEL_USAGE_AMBIGUOUS"):
        convert_aaf(source, tmp_path / "must-not-exist.aaf")


def test_convert_does_not_guess_missing_external_pcm_container(
    make_aaf,
    tmp_path: Path,
) -> None:
    import aaf2

    source = make_aaf(compatible=True)
    with aaf2.open(str(source), "rw") as handle:
        source_mob = handle.create.SourceMob("offline-audio")
        handle.content.mobs.append(source_mob)
        slot = source_mob.create_empty_slot(edit_rate=48000, media_kind="sound")
        slot.name = "A1"
        slot["PhysicalTrackNumber"].value = 1
        slot.segment.length = 16
        descriptor = handle.create.PCMDescriptor()
        source_mob.descriptor = descriptor
        descriptor["Channels"].value = 1
        descriptor["BlockAlign"].value = 3
        descriptor["SampleRate"].value = 48000
        descriptor["AverageBPS"].value = 144000
        descriptor["QuantizationBits"].value = 24
        descriptor["AudioSamplingRate"].value = 48000
        descriptor["Length"].value = 16

    report = analyze_aaf(source)

    assert [item.code for item in report.diagnostics] == [
        "PCM_CONTAINER_FORMAT_AMBIGUOUS",
        "PCM_CODEC_DEFINITION_MISSING",
    ]
    assert report.diagnostics[0].fixable is False
    with pytest.raises(UnfixableAAFError, match="PCM_CONTAINER_FORMAT_AMBIGUOUS"):
        convert_aaf(source, tmp_path / "must-not-exist.aaf")


def test_convert_reports_already_compatible_without_writing(make_aaf, tmp_path: Path) -> None:
    source = make_aaf(compatible=True)
    output = tmp_path / "unused.aaf"

    result = convert_aaf(source, output)

    assert result.status == "already-compatible"
    assert result.output is None
    assert not output.exists()


def test_convert_dry_run_does_not_create_output_directory(make_aaf, tmp_path: Path) -> None:
    source = make_aaf()
    output = tmp_path / "not-created" / "converted.aaf"

    result = convert_aaf(source, output, dry_run=True)

    assert result.status == "dry-run"
    assert result.output == output
    assert not output.parent.exists()


def test_convert_refuses_source_overwrite(make_aaf) -> None:
    source = make_aaf()

    with pytest.raises(InputValidationError, match="원본 파일"):
        convert_aaf(source, source)


def test_convert_refuses_existing_output_without_flag(make_aaf, tmp_path: Path) -> None:
    source = make_aaf()
    output = make_aaf(name="existing.aaf")

    with pytest.raises(OutputExistsError, match="이미 있습니다"):
        convert_aaf(source, output)


def test_overwrite_replaces_output_symlink_without_touching_target(
    make_aaf,
    tmp_path: Path,
) -> None:
    source = make_aaf()
    target = make_aaf(name="target.aaf", compatible=True)
    target_hash = sha256_file(target)
    output = tmp_path / "linked-output.aaf"
    try:
        output.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = convert_aaf(source, output, overwrite=True)

    assert result.status == "converted"
    assert output.is_symlink() is False
    assert analyze_aaf(output).compatible is True
    assert sha256_file(target) == target_hash


def test_discover_inputs_is_recursive_deduplicated_and_excludes_outputs(
    make_aaf,
    tmp_path: Path,
) -> None:
    first = make_aaf(name="first.aaf")
    make_aaf(name="first.nuendo.aaf")
    nested = tmp_path / "nested"
    nested.mkdir()
    second = nested / "second.AAF"
    second.write_bytes(first.read_bytes())

    found = discover_aaf_inputs(
        [tmp_path, first],
        recursive=True,
        exclude_marker=".nuendo",
    )

    assert found == [first.resolve(), second.resolve()]
