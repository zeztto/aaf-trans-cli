from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    message: str
    fixable: bool
    mob_name: str | None = None
    slot_id: int | None = None
    slot_name: str | None = None
    media_kind: str | None = None
    stored_length: int | None = None
    calculated_length: int | None = None
    component_count: int | None = None
    transition_count: int | None = None
    affected_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FileReport:
    path: Path
    source_sha256: str
    file_size: int
    producer_name: str | None
    producer_version: str | None
    producer_platform: str | None
    composition_mobs: int
    composition_slots: int
    master_mobs: int
    source_mobs: int
    essence_data: int
    sequence_count: int
    top_level_compositions: int
    top_level_timecode_tracks: int
    primary_timecode_tracks: int
    sound_tracks: int
    sound_track_names_missing: int
    sound_physical_track_numbers_missing: int
    pcm_descriptors: int
    embedded_pcm_descriptors: int
    pcm_codec_definition_present: bool
    timeline_fingerprint: str
    timeline_structure_fingerprint: str
    media_timeline_structure_fingerprint: str
    essence_fingerprint: str | None
    diagnostics: tuple[Diagnostic, ...]

    @property
    def compatible(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)

    @property
    def fixable_diagnostics(self) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.fixable)

    @property
    def unfixable_errors(self) -> tuple[Diagnostic, ...]:
        return tuple(
            item for item in self.diagnostics if item.severity == "error" and not item.fixable
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "source_sha256": self.source_sha256,
            "file_size": self.file_size,
            "producer": {
                "name": self.producer_name,
                "version": self.producer_version,
                "platform": self.producer_platform,
            },
            "counts": {
                "composition_mobs": self.composition_mobs,
                "composition_slots": self.composition_slots,
                "master_mobs": self.master_mobs,
                "source_mobs": self.source_mobs,
                "essence_data": self.essence_data,
                "sequences": self.sequence_count,
                "top_level_compositions": self.top_level_compositions,
                "top_level_timecode_tracks": self.top_level_timecode_tracks,
                "primary_timecode_tracks": self.primary_timecode_tracks,
                "sound_tracks": self.sound_tracks,
                "sound_track_names_missing": self.sound_track_names_missing,
                "sound_physical_track_numbers_missing": (
                    self.sound_physical_track_numbers_missing
                ),
                "pcm_descriptors": self.pcm_descriptors,
                "embedded_pcm_descriptors": self.embedded_pcm_descriptors,
            },
            "pcm_codec_definition_present": self.pcm_codec_definition_present,
            "timeline_fingerprint": self.timeline_fingerprint,
            "timeline_structure_fingerprint": self.timeline_structure_fingerprint,
            "media_timeline_structure_fingerprint": (
                self.media_timeline_structure_fingerprint
            ),
            "essence_fingerprint": self.essence_fingerprint,
            "compatible": self.compatible,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class ConversionResult:
    status: str
    source: Path
    output: Path | None
    sequences_fixed: int
    composition_usage_codes_fixed: int
    primary_timecode_tracks_added: int
    sound_track_names_fixed: int
    sound_physical_track_numbers_fixed: int
    pcm_container_formats_fixed: int
    pcm_codec_definitions_fixed: int
    codec_definitions_added: int
    source_report: FileReport
    output_report: FileReport | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source": str(self.source),
            "output": str(self.output) if self.output else None,
            "sequences_fixed": self.sequences_fixed,
            "composition_usage_codes_fixed": self.composition_usage_codes_fixed,
            "primary_timecode_tracks_added": self.primary_timecode_tracks_added,
            "sound_track_names_fixed": self.sound_track_names_fixed,
            "sound_physical_track_numbers_fixed": (
                self.sound_physical_track_numbers_fixed
            ),
            "pcm_container_formats_fixed": self.pcm_container_formats_fixed,
            "pcm_codec_definitions_fixed": self.pcm_codec_definitions_fixed,
            "codec_definitions_added": self.codec_definitions_added,
            "source_report": self.source_report.to_dict(),
            "output_report": self.output_report.to_dict() if self.output_report else None,
        }
