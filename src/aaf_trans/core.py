from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unicodedata
from collections.abc import Iterable
from dataclasses import replace
from itertools import chain
from pathlib import Path
from typing import Any

import aaf2
from aaf2.auid import AUID
from aaf2.components import Sequence, SourceClip, Transition
from aaf2.essence import PCMDescriptor

from .edit_protocol import (
    PRIMARY_TIMECODE_MISSING,
    SOUND_PHYSICAL_TRACK_NUMBER_MISSING,
    SOUND_TRACK_NAME_MISSING,
    TOP_LEVEL_USAGE_MISSING,
    USAGE_TOP_LEVEL_AUID,
    VALID_TIMECODE_FPS,
    add_primary_timecode,
    analyze_edit_protocol,
    repair_sound_track_metadata,
    usage_auid,
)
from .models import ConversionResult, Diagnostic, FileReport

_CHUNK_SIZE = 1024 * 1024
_SEQUENCE_LENGTH_MISMATCH = "SEQUENCE_LENGTH_MISMATCH"
_PCM_CONTAINER_FORMAT_MISSING = "PCM_CONTAINER_FORMAT_MISSING"
_PCM_CONTAINER_FORMAT_AMBIGUOUS = "PCM_CONTAINER_FORMAT_AMBIGUOUS"
_PCM_CODEC_DEFINITION_MISSING = "PCM_CODEC_DEFINITION_MISSING"
_PCM_CODEC_AUID = AUID("90ac17c8-e3e2-4596-9e9e-a6dd1c70c892")


class AAFTransError(Exception):
    """Base class for expected converter failures."""


class InputValidationError(AAFTransError):
    """The requested input or output path is invalid."""


class AAFReadError(AAFTransError):
    """The AAF container could not be read."""


class UnfixableAAFError(AAFTransError):
    """The AAF has errors outside this converter's safe repair scope."""


class OutputExistsError(AAFTransError):
    """The destination exists and overwrite was not requested."""


class VerificationError(AAFTransError):
    """The converted file failed a post-write invariant."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def calculate_sequence_length(sequence: Sequence) -> int:
    """Calculate AAF Sequence duration, subtracting Transition overlap."""
    total = 0
    for index, component in enumerate(sequence.components):
        length = getattr(component, "length", None)
        if isinstance(length, bool) or not isinstance(length, int) or length < 0:
            raise ValueError(
                f"component {index} ({type(component).__name__}) has invalid length {length!r}"
            )
        total += -length if isinstance(component, Transition) else length
        if total < 0:
            raise ValueError(f"component {index} makes the Sequence duration negative")
    return total


def _validate_aaf_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise InputValidationError(f"입력 경로가 없습니다: {resolved}")
    if not resolved.is_file():
        raise InputValidationError(f"AAF 파일이 아닙니다: {resolved}")
    if resolved.suffix.lower() != ".aaf":
        raise InputValidationError(f".aaf 확장자만 지원합니다: {resolved}")
    return resolved


def discover_aaf_inputs(
    paths: Iterable[str | os.PathLike[str]],
    *,
    recursive: bool = False,
    exclude_marker: str | None = None,
) -> list[Path]:
    """Resolve explicit files and directories into a stable, deduplicated AAF list."""
    discovered: list[Path] = []
    seen: set[Path] = set()

    for raw_path in paths:
        candidate = Path(raw_path).expanduser().resolve()
        if not candidate.exists():
            raise InputValidationError(f"입력 경로가 없습니다: {candidate}")

        if candidate.is_file():
            files = [candidate]
        elif candidate.is_dir():
            iterator = candidate.rglob("*") if recursive else candidate.glob("*")
            files = sorted(
                (
                    item
                    for item in iterator
                    if item.is_file()
                    and item.suffix.lower() == ".aaf"
                    and not (
                        exclude_marker
                        and item.stem.lower().endswith(exclude_marker.lower())
                    )
                ),
                key=lambda item: str(item).casefold(),
            )
        else:
            raise InputValidationError(f"파일 또는 directory가 아닙니다: {candidate}")

        for file_path in files:
            if file_path.suffix.lower() != ".aaf":
                raise InputValidationError(f".aaf 확장자만 지원합니다: {file_path}")
            resolved = file_path.resolve()
            if resolved not in seen:
                discovered.append(resolved)
                seen.add(resolved)

    if not discovered:
        raise InputValidationError("변환할 .aaf 파일을 찾지 못했습니다.")
    return discovered


def build_output_path(source: Path, output_dir: Path | None, marker: str) -> Path:
    if not marker or marker in {".", ".."}:
        raise InputValidationError("output marker는 비어 있을 수 없습니다.")
    if any(
        character in '/\\:*?"<>|' or ord(character) < 32 or ord(character) == 127
        for character in marker
    ):
        raise InputValidationError(
            "output marker에는 경로 구분자·예약 문자·제어 문자를 사용할 수 없습니다."
        )
    normalized_marker = marker if marker.startswith(".") else f".{marker}"
    parent = output_dir.expanduser().resolve() if output_dir else source.parent
    return parent / f"{source.stem}{normalized_marker}.aaf"


def _normalize_output_path(path: Path) -> Path:
    """Resolve the parent but never follow the final output symlink."""
    expanded = path.expanduser()
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    return absolute.parent.resolve() / absolute.name


def _batch_path_key(path: Path) -> str:
    decomposed = unicodedata.normalize("NFD", str(path))
    return unicodedata.normalize("NFC", decomposed.casefold())


def validate_batch_paths(inputs: list[Path], outputs: list[Path]) -> None:
    """Reject ambiguous targets before any conversion in the batch starts."""
    if len(inputs) != len(outputs):
        raise InputValidationError("입력과 출력 경로 수가 일치하지 않습니다.")

    sources = [source.expanduser().resolve() for source in inputs]
    source_keys = {_batch_path_key(source) for source in sources}
    source_stats = [source.stat() for source in sources]
    source_ids = {(stat.st_dev, stat.st_ino) for stat in source_stats}
    target_keys: set[str] = set()
    target_ids: set[tuple[int, int]] = set()

    for raw_output in outputs:
        output = _normalize_output_path(raw_output)
        # Reject equivalent Unicode/case names even on case-sensitive volumes.
        key = _batch_path_key(output)
        try:
            stat = output.stat()
            identity = (stat.st_dev, stat.st_ino)
        except FileNotFoundError:
            identity = None

        if key in source_keys or (identity is not None and identity in source_ids):
            raise InputValidationError(
                f"출력이 batch 입력 파일 또는 그 별칭과 겹칩니다: {output}"
            )
        if key in target_keys or (identity is not None and identity in target_ids):
            raise InputValidationError(
                "출력 이름 또는 파일 별칭이 충돌합니다. "
                "서로 다른 output directory 또는 marker를 사용하세요."
            )
        target_keys.add(key)
        if identity is not None:
            target_ids.add(identity)


def _producer_metadata(handle: Any) -> tuple[str | None, str | None, str | None]:
    try:
        identifications = list(handle.header["IdentificationList"].value)
    except (KeyError, TypeError, ValueError):
        return None, None, None
    if not identifications:
        return None, None, None

    identification = identifications[-1]

    def value(name: str) -> str | None:
        try:
            raw = identification[name].value
        except (KeyError, TypeError, ValueError):
            return None
        return str(raw) if raw is not None else None

    return value("ProductName"), value("ProductVersionString"), value("Platform")


def _component_record(component: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": type(component).__name__,
        "media_kind": getattr(component, "media_kind", None),
        "length": getattr(component, "length", None),
    }
    if isinstance(component, SourceClip):
        record.update(
            {
                "source_id": str(component.mob_id),
                "source_slot_id": component.slot_id,
                "start": component.start,
            }
        )
    elif isinstance(component, Transition):
        record["cutpoint"] = component.cutpoint
    elif isinstance(component, Sequence):
        record["components"] = [_component_record(item) for item in component.components]
        record.pop("length", None)
    return record


def _timeline_fingerprint(
    composition_mobs: list[Any],
    *,
    include_track_names: bool = True,
    exclude_media_kinds: Iterable[str] = (),
) -> str:
    excluded = frozenset(exclude_media_kinds)
    records: list[dict[str, Any]] = []
    for mob in composition_mobs:
        mob_record: dict[str, Any] = {
            "mob_id": str(mob.mob_id),
            "name": mob.name,
            "slots": [],
        }
        for slot in mob.slots:
            segment = slot.segment
            if getattr(segment, "media_kind", None) in excluded:
                continue
            mob_record["slots"].append(
                {
                    "slot_id": slot.slot_id,
                    "edit_rate": str(getattr(slot, "edit_rate", None)),
                    "origin": getattr(slot, "origin", None),
                    "segment": _component_record(segment) if segment is not None else None,
                }
            )
            if include_track_names:
                mob_record["slots"][-1]["slot_name"] = slot.name
        records.append(mob_record)

    payload = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _essence_fingerprint(essence_data: list[Any]) -> str:
    digest = hashlib.sha256()
    for essence in sorted(essence_data, key=lambda item: str(item.mob_id)):
        digest.update(str(essence.mob_id).encode("ascii"))
        stream = essence.open("r")
        try:
            while True:
                chunk = stream.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        finally:
            stream.close()
    return digest.hexdigest()


def _weak_reference_target(property_value: Any) -> Any | None:
    if property_value.data is None:
        return None
    try:
        return property_value.value
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _pcm_descriptor_state(
    source_mobs: list[Any],
    essence_data: list[Any],
) -> tuple[int, int, int, int, int]:
    embedded_mob_ids = {str(item.mob_id) for item in essence_data}
    pcm_descriptors = 0
    embedded_pcm_descriptors = 0
    missing_embedded_containers = 0
    missing_ambiguous_containers = 0
    missing_codecs = 0

    for mob in source_mobs:
        descriptor = mob.descriptor
        if not isinstance(descriptor, PCMDescriptor):
            continue
        pcm_descriptors += 1
        embedded = str(mob.mob_id) in embedded_mob_ids
        embedded_pcm_descriptors += int(embedded)

        if _weak_reference_target(descriptor["ContainerFormat"]) is None:
            if embedded:
                missing_embedded_containers += 1
            else:
                missing_ambiguous_containers += 1
        if _weak_reference_target(descriptor["CodecDefinition"]) is None:
            missing_codecs += 1

    return (
        pcm_descriptors,
        embedded_pcm_descriptors,
        missing_embedded_containers,
        missing_ambiguous_containers,
        missing_codecs,
    )


def _ensure_pcm_codec_definition(handle: Any) -> tuple[Any, bool]:
    dictionary = handle.dictionary
    codec = dictionary.lookup_codecdef(_PCM_CODEC_AUID)
    if codec is not None:
        return codec, False

    codec = handle.create.CodecDef(
        dictionary,
        _PCM_CODEC_AUID,
        "PCM Codec",
        "Handles RIFF PCM data.",
        "PCMDescriptor",
        ("DataDef_Sound", "DataDef_LegacySound"),
    )
    dictionary.register_def(codec)
    return codec, True


def analyze_aaf(
    path: str | os.PathLike[str],
    *,
    include_essence_fingerprint: bool = False,
) -> FileReport:
    source = _validate_aaf_path(Path(path))
    source_hash = sha256_file(source)
    diagnostics: list[Diagnostic] = []

    try:
        with aaf2.open(str(source), "r") as handle:
            composition_mobs = list(handle.content.compositionmobs())
            master_mobs = list(handle.content.mastermobs())
            source_mobs = list(handle.content.sourcemobs())
            essence_data = list(handle.content.essencedata)
            producer_name, producer_version, producer_platform = _producer_metadata(handle)
            edit_protocol = analyze_edit_protocol(
                composition_mobs,
                master_mobs,
                source_mobs,
            )
            (
                pcm_descriptors,
                embedded_pcm_descriptors,
                missing_embedded_containers,
                missing_ambiguous_containers,
                missing_codecs,
            ) = _pcm_descriptor_state(source_mobs, essence_data)
            pcm_codec_definition_present = (
                handle.dictionary.lookup_codecdef(_PCM_CODEC_AUID) is not None
            )

            if not composition_mobs:
                diagnostics.append(
                    Diagnostic(
                        code="NO_COMPOSITION_MOB",
                        severity="error",
                        message="CompositionMob이 없어 Nuendo timeline을 만들 수 없습니다.",
                        fixable=False,
                    )
                )

            composition_slots = 0
            sequence_count = 0
            for mob in composition_mobs:
                for slot in mob.slots:
                    composition_slots += 1
                    segment = slot.segment
                    if segment is None:
                        diagnostics.append(
                            Diagnostic(
                                code="MISSING_SLOT_SEGMENT",
                                severity="error",
                                message="Composition slot의 Segment가 없습니다.",
                                fixable=False,
                                mob_name=mob.name,
                                slot_id=slot.slot_id,
                                slot_name=slot.name,
                            )
                        )
                        continue
                    if not isinstance(segment, Sequence):
                        continue

                    sequence_count += 1
                    try:
                        calculated_length = calculate_sequence_length(segment)
                    except ValueError as error:
                        diagnostics.append(
                            Diagnostic(
                                code="INVALID_COMPONENT_LENGTH",
                                severity="error",
                                message=str(error),
                                fixable=False,
                                mob_name=mob.name,
                                slot_id=slot.slot_id,
                                slot_name=slot.name,
                                media_kind=segment.media_kind,
                                stored_length=segment.length,
                                component_count=len(segment.components),
                            )
                        )
                        continue

                    if segment.length != calculated_length:
                        diagnostics.append(
                            Diagnostic(
                                code=_SEQUENCE_LENGTH_MISMATCH,
                                severity="error",
                                message=(
                                    "Sequence.Length가 component duration 합계와 일치하지 않습니다."
                                ),
                                fixable=True,
                                mob_name=mob.name,
                                slot_id=slot.slot_id,
                                slot_name=slot.name,
                                media_kind=segment.media_kind,
                                stored_length=segment.length,
                                calculated_length=calculated_length,
                                component_count=len(segment.components),
                                transition_count=sum(
                                    isinstance(item, Transition) for item in segment.components
                                ),
                            )
                        )

            if composition_mobs and composition_slots == 0:
                diagnostics.append(
                    Diagnostic(
                        code="NO_COMPOSITION_SLOTS",
                        severity="error",
                        message="CompositionMob에 import할 track slot이 없습니다.",
                        fixable=False,
                    )
                )

            diagnostics.extend(edit_protocol.diagnostics)

            if missing_embedded_containers:
                diagnostics.append(
                    Diagnostic(
                        code=_PCM_CONTAINER_FORMAT_MISSING,
                        severity="error",
                        message=(
                            "Embedded PCMDescriptor의 ContainerFormat이 없습니다. "
                            "Nuendo importer가 embedded essence container를 "
                            "해석하지 못할 수 있습니다."
                        ),
                        fixable=True,
                        media_kind="sound",
                        affected_count=missing_embedded_containers,
                    )
                )

            if missing_ambiguous_containers:
                diagnostics.append(
                    Diagnostic(
                        code=_PCM_CONTAINER_FORMAT_AMBIGUOUS,
                        severity="error",
                        message=(
                            "외부 또는 offline PCMDescriptor의 ContainerFormat이 없어 "
                            "안전하게 container type을 추론할 수 없습니다."
                        ),
                        fixable=False,
                        media_kind="sound",
                        affected_count=missing_ambiguous_containers,
                    )
                )

            if missing_codecs:
                diagnostics.append(
                    Diagnostic(
                        code=_PCM_CODEC_DEFINITION_MISSING,
                        severity="error",
                        message=(
                            "PCMDescriptor의 CodecDefinition이 없거나 Dictionary에서 "
                            "해결되지 않습니다."
                        ),
                        fixable=True,
                        media_kind="sound",
                        affected_count=missing_codecs,
                    )
                )

            timeline_fingerprint = _timeline_fingerprint(composition_mobs)
            timeline_structure_fingerprint = _timeline_fingerprint(
                composition_mobs,
                include_track_names=False,
            )
            media_timeline_structure_fingerprint = _timeline_fingerprint(
                composition_mobs,
                include_track_names=False,
                exclude_media_kinds=("Timecode",),
            )
            essence_fingerprint = (
                _essence_fingerprint(essence_data) if include_essence_fingerprint else None
            )
    except AAFTransError:
        raise
    except Exception as error:
        raise AAFReadError(
            f"AAF container를 읽지 못했습니다: {source} ({type(error).__name__}: {error})"
        ) from error

    return FileReport(
        path=source,
        source_sha256=source_hash,
        file_size=source.stat().st_size,
        producer_name=producer_name,
        producer_version=producer_version,
        producer_platform=producer_platform,
        composition_mobs=len(composition_mobs),
        composition_slots=composition_slots,
        master_mobs=len(master_mobs),
        source_mobs=len(source_mobs),
        essence_data=len(essence_data),
        sequence_count=sequence_count,
        top_level_compositions=len(edit_protocol.top_level_compositions),
        top_level_timecode_tracks=edit_protocol.top_level_timecode_tracks,
        primary_timecode_tracks=edit_protocol.primary_timecode_tracks,
        sound_tracks=edit_protocol.sound_tracks,
        sound_track_names_missing=edit_protocol.sound_track_names_missing,
        sound_physical_track_numbers_missing=(
            edit_protocol.sound_physical_track_numbers_missing
        ),
        pcm_descriptors=pcm_descriptors,
        embedded_pcm_descriptors=embedded_pcm_descriptors,
        pcm_codec_definition_present=pcm_codec_definition_present,
        timeline_fingerprint=timeline_fingerprint,
        timeline_structure_fingerprint=timeline_structure_fingerprint,
        media_timeline_structure_fingerprint=(
            media_timeline_structure_fingerprint
        ),
        essence_fingerprint=essence_fingerprint,
        diagnostics=tuple(diagnostics),
    )


def _assert_output_target(source: Path, output: Path, overwrite: bool) -> None:
    if output.suffix.lower() != ".aaf":
        raise InputValidationError(f"출력 파일은 .aaf 확장자여야 합니다: {output}")
    if source == output:
        raise InputValidationError("원본 파일을 직접 덮어쓸 수 없습니다.")
    if output.exists() or output.is_symlink():
        try:
            if source.samefile(output):
                raise InputValidationError("원본과 출력이 같은 파일입니다.")
        except OSError:
            pass
        if not overwrite:
            raise OutputExistsError(f"출력 파일이 이미 있습니다: {output}")


def _validate_timecode_fps(timecode_fps: int) -> int:
    if (
        isinstance(timecode_fps, bool)
        or not isinstance(timecode_fps, int)
        or timecode_fps not in VALID_TIMECODE_FPS
    ):
        allowed = ", ".join(str(value) for value in VALID_TIMECODE_FPS)
        raise InputValidationError(
            f"timecode FPS는 다음 값만 지원합니다: {allowed}"
        )
    return timecode_fps


def _assert_reports_preserve_structure(
    before: FileReport,
    after: FileReport,
    *,
    primary_timecode_tracks_added: int,
) -> None:
    count_fields = (
        "composition_mobs",
        "master_mobs",
        "source_mobs",
        "essence_data",
        "sequence_count",
    )
    changed_counts = [
        name for name in count_fields if getattr(before, name) != getattr(after, name)
    ]
    if changed_counts:
        raise VerificationError(
            "변환 중 AAF object count가 바뀌었습니다: " + ", ".join(changed_counts)
        )
    expected_composition_slots = (
        before.composition_slots + primary_timecode_tracks_added
    )
    if after.composition_slots != expected_composition_slots:
        raise VerificationError(
            "변환 중 Composition slot count가 예상과 다르게 바뀌었습니다: "
            f"expected={expected_composition_slots}, actual={after.composition_slots}"
        )
    if (
        before.media_timeline_structure_fingerprint
        != after.media_timeline_structure_fingerprint
    ):
        raise VerificationError("변환 중 media timeline component 구조가 바뀌었습니다.")
    if (
        primary_timecode_tracks_added == 0
        and before.timeline_structure_fingerprint
        != after.timeline_structure_fingerprint
    ):
        raise VerificationError("변환 중 timeline component 구조가 바뀌었습니다.")
    if (
        after.primary_timecode_tracks
        != before.primary_timecode_tracks + primary_timecode_tracks_added
    ):
        raise VerificationError(
            "변환 후 Primary Timecode track count가 예상과 다릅니다."
        )
    if (
        after.top_level_timecode_tracks
        != before.top_level_timecode_tracks + primary_timecode_tracks_added
    ):
        raise VerificationError(
            "변환 후 top-level Timecode track count가 예상과 다릅니다."
        )
    if (
        before.essence_fingerprint is not None
        and before.essence_fingerprint != after.essence_fingerprint
    ):
        raise VerificationError("변환 중 embedded essence payload가 바뀌었습니다.")


def _materialize_output(temp_path: Path, output: Path, overwrite: bool) -> None:
    if overwrite:
        os.replace(temp_path, output)
        return
    try:
        os.link(temp_path, output)
    except FileExistsError as error:
        raise OutputExistsError(f"출력 파일이 이미 있습니다: {output}") from error
    except OSError as error:
        message = f"검증된 출력 파일을 생성하지 못했습니다: {output} ({error})"
        raise AAFTransError(message) from error
    temp_path.unlink()


def _affected_count(report: FileReport, code: str) -> int:
    return sum(
        item.affected_count or 1
        for item in report.fixable_diagnostics
        if item.code == code
    )


def convert_aaf(
    source_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
    dry_run: bool = False,
    verify_essence: bool = True,
    timecode_fps: int = 25,
) -> ConversionResult:
    source = _validate_aaf_path(Path(source_path))
    output = _normalize_output_path(Path(output_path))
    timecode_fps = _validate_timecode_fps(timecode_fps)
    _assert_output_target(source, output, overwrite)

    before = analyze_aaf(source, include_essence_fingerprint=verify_essence)
    if before.unfixable_errors:
        codes = ", ".join(item.code for item in before.unfixable_errors)
        raise UnfixableAAFError(f"자동 수정 범위 밖의 AAF 오류가 있습니다: {codes}")

    sequences_expected = _affected_count(before, _SEQUENCE_LENGTH_MISMATCH)
    usage_codes_expected = _affected_count(before, TOP_LEVEL_USAGE_MISSING)
    timecode_tracks_expected = _affected_count(before, PRIMARY_TIMECODE_MISSING)
    track_names_expected = _affected_count(before, SOUND_TRACK_NAME_MISSING)
    physical_numbers_expected = _affected_count(
        before,
        SOUND_PHYSICAL_TRACK_NUMBER_MISSING,
    )
    containers_expected = _affected_count(before, _PCM_CONTAINER_FORMAT_MISSING)
    codecs_expected = _affected_count(before, _PCM_CODEC_DEFINITION_MISSING)
    fixes_expected = sum(
        (
            sequences_expected,
            usage_codes_expected,
            timecode_tracks_expected,
            track_names_expected,
            physical_numbers_expected,
            containers_expected,
            codecs_expected,
        )
    )
    if fixes_expected == 0:
        return ConversionResult(
            status="already-compatible",
            source=source,
            output=None,
            sequences_fixed=0,
            composition_usage_codes_fixed=0,
            primary_timecode_tracks_added=0,
            sound_track_names_fixed=0,
            sound_physical_track_numbers_fixed=0,
            pcm_container_formats_fixed=0,
            pcm_codec_definitions_fixed=0,
            codec_definitions_added=0,
            source_report=before,
            output_report=None,
        )
    if dry_run:
        return ConversionResult(
            status="dry-run",
            source=source,
            output=output,
            sequences_fixed=sequences_expected,
            composition_usage_codes_fixed=usage_codes_expected,
            primary_timecode_tracks_added=timecode_tracks_expected,
            sound_track_names_fixed=track_names_expected,
            sound_physical_track_numbers_fixed=physical_numbers_expected,
            pcm_container_formats_fixed=containers_expected,
            pcm_codec_definitions_fixed=codecs_expected,
            codec_definitions_added=int(
                codecs_expected > 0 and not before.pcm_codec_definition_present
            ),
            source_report=before,
            output_report=None,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".aaf",
        dir=output.parent,
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)

    try:
        shutil.copy2(source, temporary)
        sequences_changed = 0
        usage_codes_changed = 0
        timecode_tracks_changed = 0
        track_names_changed = 0
        physical_numbers_changed = 0
        containers_changed = 0
        codecs_changed = 0
        codec_definitions_added = 0
        with aaf2.open(str(temporary), "rw") as handle:
            composition_mobs = list(handle.content.compositionmobs())
            master_mobs = list(handle.content.mastermobs())
            source_mobs = list(handle.content.sourcemobs())

            for mob in composition_mobs:
                for slot in mob.slots:
                    sequence = slot.segment
                    if not isinstance(sequence, Sequence):
                        continue
                    calculated_length = calculate_sequence_length(sequence)
                    if sequence.length != calculated_length:
                        sequence.length = calculated_length
                        sequences_changed += 1

            if (
                len(composition_mobs) == 1
                and usage_auid(composition_mobs[0]) is None
            ):
                composition_mobs[0].usage = USAGE_TOP_LEVEL_AUID
                usage_codes_changed += 1

            (
                track_names_changed,
                physical_numbers_changed,
            ) = repair_sound_track_metadata(
                chain(composition_mobs, master_mobs, source_mobs)
            )

            embedded_mob_ids = {
                str(item.mob_id) for item in handle.content.essencedata
            }
            pcm_codec = None
            container = handle.dictionary.lookup_containerdef("AAF")
            for mob in source_mobs:
                descriptor = mob.descriptor
                if not isinstance(descriptor, PCMDescriptor):
                    continue

                if (
                    str(mob.mob_id) in embedded_mob_ids
                    and _weak_reference_target(descriptor["ContainerFormat"]) is None
                ):
                    descriptor["ContainerFormat"].value = container
                    containers_changed += 1

                if _weak_reference_target(descriptor["CodecDefinition"]) is None:
                    if pcm_codec is None:
                        pcm_codec, added = _ensure_pcm_codec_definition(handle)
                        codec_definitions_added += int(added)
                    descriptor["CodecDefinition"].value = pcm_codec
                    codecs_changed += 1

        if timecode_tracks_expected:
            with aaf2.open(str(temporary), "rw") as handle:
                top_level_compositions = [
                    mob
                    for mob in handle.content.compositionmobs()
                    if usage_auid(mob) == USAGE_TOP_LEVEL_AUID
                ]
                if len(top_level_compositions) != 1:
                    raise VerificationError(
                        "Primary Timecode를 추가할 top-level CompositionMob이 "
                        "하나가 아닙니다."
                    )
                try:
                    add_primary_timecode(
                        handle,
                        top_level_compositions[0],
                        timecode_fps,
                    )
                except ValueError as error:
                    raise VerificationError(
                        f"Primary Timecode를 추가하지 못했습니다: {error}"
                    ) from error
                timecode_tracks_changed += 1

        actual_counts = (
            sequences_changed,
            usage_codes_changed,
            timecode_tracks_changed,
            track_names_changed,
            physical_numbers_changed,
            containers_changed,
            codecs_changed,
        )
        expected_counts = (
            sequences_expected,
            usage_codes_expected,
            timecode_tracks_expected,
            track_names_expected,
            physical_numbers_expected,
            containers_expected,
            codecs_expected,
        )
        if actual_counts != expected_counts:
            raise VerificationError(
                "수정 대상 count가 달라졌습니다: "
                f"expected={expected_counts}, changed={actual_counts}"
            )

        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())

        after = analyze_aaf(temporary, include_essence_fingerprint=verify_essence)
        remaining_errors = [item.code for item in after.diagnostics if item.severity == "error"]
        if remaining_errors:
            raise VerificationError(
                "변환 후 AAF 오류가 남았습니다: " + ", ".join(remaining_errors)
            )
        _assert_reports_preserve_structure(
            before,
            after,
            primary_timecode_tracks_added=timecode_tracks_changed,
        )
        if sha256_file(source) != before.source_sha256:
            raise VerificationError("변환 중 원본 파일이 변경되었습니다.")

        _materialize_output(temporary, output, overwrite)
        output_report = replace(after, path=output)
        return ConversionResult(
            status="converted",
            source=source,
            output=output,
            sequences_fixed=sequences_changed,
            composition_usage_codes_fixed=usage_codes_changed,
            primary_timecode_tracks_added=timecode_tracks_changed,
            sound_track_names_fixed=track_names_changed,
            sound_physical_track_numbers_fixed=physical_numbers_changed,
            pcm_container_formats_fixed=containers_changed,
            pcm_codec_definitions_fixed=codecs_changed,
            codec_definitions_added=codec_definitions_added,
            source_report=before,
            output_report=output_report,
        )
    finally:
        if temporary.exists():
            temporary.unlink()
