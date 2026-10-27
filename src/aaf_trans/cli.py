from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .core import (
    analyze_aaf,
    build_output_path,
    convert_aaf,
    discover_aaf_inputs,
    validate_batch_paths,
)
from .edit_protocol import VALID_TIMECODE_FPS
from .models import ConversionResult, FileReport


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aaf-trans",
        description="AAF timeline과 내장 PCM metadata를 검사·정규화합니다.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="AAF compatibility를 진단합니다.")
    inspect_parser.add_argument("paths", nargs="+", help="AAF 파일 또는 directory")
    inspect_parser.add_argument(
        "-r", "--recursive", action="store_true", help="하위 directory 포함"
    )
    inspect_parser.add_argument(
        "--essence-hash",
        action="store_true",
        help="embedded essence payload fingerprint도 계산",
    )
    inspect_parser.add_argument("--json", action="store_true", help="JSON으로 출력")

    convert_parser = subparsers.add_parser(
        "convert", help="지원 범위의 AAF metadata를 정규화합니다."
    )
    convert_parser.add_argument("paths", nargs="+", help="AAF 파일 또는 directory")
    convert_parser.add_argument(
        "-r", "--recursive", action="store_true", help="하위 directory 포함"
    )
    convert_parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="출력 directory; 생략하면 원본 옆에 생성",
    )
    convert_parser.add_argument(
        "--marker",
        default=".nuendo",
        help="출력 이름 marker (기본: .nuendo)",
    )
    convert_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="기존 출력 파일 교체",
    )
    convert_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="진단만 하고 파일을 쓰지 않음",
    )
    convert_parser.add_argument(
        "--skip-essence-hash",
        action="store_true",
        help="embedded essence byte fingerprint 검증 생략 (빠르지만 권장하지 않음)",
    )
    convert_parser.add_argument(
        "--timecode-fps",
        type=int,
        choices=VALID_TIMECODE_FPS,
        default=25,
        help="누락된 Primary Timecode FPS (기본: 25)",
    )
    convert_parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    return parser


def _report_status(report: FileReport) -> str:
    return "진단 통과" if report.compatible else "수정 필요"


def _print_report(report: FileReport) -> None:
    print(f"[{_report_status(report)}] {report.path}")
    print(
        "  "
        f"producer={report.producer_name or 'unknown'} "
        f"{report.producer_version or ''} "
        f"platform={report.producer_platform or 'unknown'}"
    )
    print(
        "  "
        f"composition={report.composition_mobs} slots={report.composition_slots} "
        f"master={report.master_mobs} source={report.source_mobs} "
        f"essence={report.essence_data}"
    )
    print(f"  sha256={report.source_sha256}")
    for item in report.diagnostics:
        location = ""
        if item.mob_name is not None:
            location = f" mob={item.mob_name!r} slot={item.slot_id}"
        lengths = ""
        if item.stored_length is not None:
            lengths = (
                f" stored={item.stored_length} calculated={item.calculated_length} "
                f"components={item.component_count}"
            )
        affected = (
            f" affected={item.affected_count}" if item.affected_count is not None else ""
        )
        print(f"  - {item.code}:{location}{lengths}{affected} — {item.message}")


def _print_conversion(result: ConversionResult) -> None:
    if result.status == "converted":
        print(
            f"[변환 완료] {result.source} -> {result.output} "
            f"(Sequence={result.sequences_fixed}, "
            f"UsageCode={result.composition_usage_codes_fixed}, "
            f"PrimaryTimecode={result.primary_timecode_tracks_added}, "
            f"SlotName={result.sound_track_names_fixed}, "
            f"PhysicalTrackNumber={result.sound_physical_track_numbers_fixed}, "
            f"PCM ContainerFormat={result.pcm_container_formats_fixed}, "
            f"PCM CodecDefinition={result.pcm_codec_definitions_fixed}, "
            f"CodecDef 추가={result.codec_definitions_added})"
        )
    elif result.status == "dry-run":
        print(
            f"[dry-run] {result.source} -> {result.output} "
            f"(Sequence={result.sequences_fixed}, "
            f"UsageCode={result.composition_usage_codes_fixed}, "
            f"PrimaryTimecode={result.primary_timecode_tracks_added}, "
            f"SlotName={result.sound_track_names_fixed}, "
            f"PhysicalTrackNumber={result.sound_physical_track_numbers_fixed}, "
            f"PCM ContainerFormat={result.pcm_container_formats_fixed}, "
            f"PCM CodecDefinition={result.pcm_codec_definitions_fixed} 수정 예정)"
        )
    else:
        print(f"[이미 호환] {result.source}")


def _error_payload(path: str | Path | None, error: Exception) -> dict[str, Any]:
    return {
        "status": "failed",
        "path": str(path) if path is not None else None,
        "error": {
            "type": type(error).__name__,
            "message": str(error),
        },
    }


def _run_inspect(args: argparse.Namespace) -> int:
    try:
        inputs = discover_aaf_inputs(args.paths, recursive=args.recursive)
    except Exception as error:
        if args.json:
            print(json.dumps([_error_payload(None, error)], ensure_ascii=False, indent=2))
        else:
            print(f"[실패] {error}", file=sys.stderr)
        return 1

    payload: list[dict[str, Any]] = []
    failed = False
    for source in inputs:
        try:
            report = analyze_aaf(source, include_essence_fingerprint=args.essence_hash)
            payload.append(report.to_dict())
            failed = failed or not report.compatible
            if not args.json:
                _print_report(report)
        except Exception as error:
            failed = True
            payload.append(_error_payload(source, error))
            if not args.json:
                print(f"[실패] {source}: {error}", file=sys.stderr)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def _run_convert(args: argparse.Namespace) -> int:
    try:
        inputs = discover_aaf_inputs(
            args.paths,
            recursive=args.recursive,
            exclude_marker=args.marker,
        )
        outputs = [
            build_output_path(source, args.output_dir, args.marker) for source in inputs
        ]
        validate_batch_paths(inputs, outputs)
    except Exception as error:
        if args.json:
            print(json.dumps([_error_payload(None, error)], ensure_ascii=False, indent=2))
        else:
            print(f"[실패] {error}", file=sys.stderr)
        return 1

    payload: list[dict[str, Any]] = []
    failed = False
    for source, output in zip(inputs, outputs, strict=True):
        try:
            result = convert_aaf(
                source,
                output,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                verify_essence=not args.skip_essence_hash,
                timecode_fps=args.timecode_fps,
            )
            payload.append(result.to_dict())
            if not args.json:
                _print_conversion(result)
        except Exception as error:
            failed = True
            payload.append(_error_payload(source, error))
            if not args.json:
                print(f"[실패] {source}: {error}", file=sys.stderr)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "inspect":
        return _run_inspect(args)
    if args.command == "convert":
        return _run_convert(args)
    parser.error(f"알 수 없는 command: {args.command}")
    return 2
