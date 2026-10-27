from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import aaf_trans.cli as cli
from aaf_trans.core import sha256_file


def test_inspect_json_reports_mismatch(make_aaf, capsys) -> None:
    source = make_aaf()

    exit_code = cli.main(["inspect", str(source), "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["compatible"] is False
    assert payload[0]["diagnostics"][0]["code"] == "SEQUENCE_LENGTH_MISMATCH"


def test_inspect_human_reports_compatible_file(make_aaf, capsys) -> None:
    source = make_aaf(compatible=True)

    exit_code = cli.main(["inspect", str(source)])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "[진단 통과]" in stdout
    assert "composition=1 slots=2" in stdout


def test_convert_batch_writes_each_output(make_aaf, tmp_path: Path, capsys) -> None:
    first = make_aaf(name="first.aaf")
    second = make_aaf(name="second.aaf")
    output_dir = tmp_path / "converted"

    exit_code = cli.main(
        [
            "convert",
            str(first),
            str(second),
            "--output-dir",
            str(output_dir),
            "--skip-essence-hash",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "first.nuendo.aaf").exists()
    assert (output_dir / "second.nuendo.aaf").exists()
    stdout = capsys.readouterr().out
    assert stdout.count("[변환 완료]") == 2


def test_convert_json_is_machine_readable(make_aaf, tmp_path: Path, capsys) -> None:
    source = make_aaf()
    output_dir = tmp_path / "converted"

    exit_code = cli.main(
        [
            "convert",
            str(source),
            "-o",
            str(output_dir),
            "--skip-essence-hash",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["status"] == "converted"
    assert payload[0]["sequences_fixed"] == 1
    assert payload[0]["primary_timecode_tracks_added"] == 0


def test_convert_cli_applies_requested_primary_timecode_fps(
    make_aaf,
    tmp_path: Path,
    capsys,
) -> None:
    import aaf2

    source = make_aaf(
        compatible=True,
        missing_primary_timecode=True,
    )
    output_dir = tmp_path / "converted"

    exit_code = cli.main(
        [
            "convert",
            str(source),
            "--output-dir",
            str(output_dir),
            "--timecode-fps",
            "30",
            "--skip-essence-hash",
        ]
    )

    assert exit_code == 0
    assert "PrimaryTimecode=1" in capsys.readouterr().out
    output = output_dir / "fixture.nuendo.aaf"
    with aaf2.open(str(output), "r") as handle:
        composition = next(iter(handle.content.compositionmobs()))
        slot = next(
            slot for slot in composition.slots if slot.media_kind == "Timecode"
        )
        assert str(slot.edit_rate) == "30"
        assert slot.segment.fps == 30


def test_convert_human_reports_dry_run_and_already_compatible(
    make_aaf,
    tmp_path: Path,
    capsys,
) -> None:
    broken = make_aaf(name="broken.aaf")
    compatible = make_aaf(name="compatible.aaf", compatible=True)
    output_dir = tmp_path / "converted"

    dry_run_exit = cli.main(
        [
            "convert",
            str(broken),
            "--output-dir",
            str(output_dir),
            "--dry-run",
            "--skip-essence-hash",
        ]
    )
    compatible_exit = cli.main(
        [
            "convert",
            str(compatible),
            "--output-dir",
            str(output_dir),
            "--skip-essence-hash",
        ]
    )

    assert dry_run_exit == 0
    assert compatible_exit == 0
    stdout = capsys.readouterr().out
    assert "[dry-run]" in stdout
    assert "[이미 호환]" in stdout
    assert not output_dir.exists()


def test_inspect_missing_path_returns_json_error(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing.aaf"

    exit_code = cli.main(["inspect", str(missing), "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["status"] == "failed"
    assert payload[0]["error"]["type"] == "InputValidationError"


def test_convert_batch_isolates_unexpected_per_file_error(
    make_aaf,
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    first = make_aaf(name="first.aaf")
    second = make_aaf(name="second.aaf")
    output_dir = tmp_path / "converted"
    real_convert = cli.convert_aaf

    def flaky_convert(source, output, **kwargs):
        if Path(source).name == "first.aaf":
            raise OSError("simulated filesystem failure")
        return real_convert(source, output, **kwargs)

    monkeypatch.setattr(cli, "convert_aaf", flaky_convert)

    exit_code = cli.main(
        [
            "convert",
            str(first),
            str(second),
            "--output-dir",
            str(output_dir),
            "--skip-essence-hash",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "simulated filesystem failure" in captured.err
    assert not (output_dir / "first.nuendo.aaf").exists()
    assert (output_dir / "second.nuendo.aaf").exists()


def test_convert_batch_rejects_another_input_as_output_before_writing(
    make_aaf,
    capsys,
) -> None:
    first = make_aaf(name="first.aaf")
    second = make_aaf(name="first.nuendo.aaf")
    before = {path: sha256_file(path) for path in [first, second]}

    exit_code = cli.main(["convert", str(first), str(second), "--overwrite", "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["status"] == "failed"
    assert payload[0]["path"] is None
    assert {path: sha256_file(path) for path in before} == before
    assert not second.with_name("first.nuendo.nuendo.aaf").exists()


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_convert_batch_rejects_input_alias_before_writing(
    make_aaf,
    alias: str,
    monkeypatch,
    capsys,
) -> None:
    first = make_aaf(name="first.aaf")
    second = make_aaf(name="second.aaf")
    output = first.with_name("first.nuendo.aaf")
    try:
        if alias == "symlink":
            output.symlink_to(second)
        else:
            os.link(second, output)
    except OSError as error:
        pytest.skip(f"filesystem does not support {alias}: {error}")
    before = {path: sha256_file(path) for path in [first, second, output]}
    calls: list[Path] = []
    real_convert = cli.convert_aaf

    def tracked_convert(source, destination, **kwargs):
        calls.append(source)
        return real_convert(source, destination, **kwargs)

    monkeypatch.setattr(cli, "convert_aaf", tracked_convert)
    exit_code = cli.main(["convert", str(first), str(second), "--overwrite"])

    assert exit_code == 1
    assert not calls
    assert {path: sha256_file(path) for path in before} == before
    assert output.samefile(second)
    assert not second.with_name("second.nuendo.aaf").exists()
    assert "[실패]" in capsys.readouterr().err


@pytest.mark.parametrize("dry_run", [False, True])
def test_convert_batch_rejects_case_only_output_collisions(
    make_aaf,
    tmp_path: Path,
    dry_run: bool,
    capsys,
) -> None:
    left_dir = tmp_path / "left"
    right_dir = tmp_path / "right"
    left_dir.mkdir()
    right_dir.mkdir()
    first = make_aaf(name="Case.aaf")
    # Separate input directories work on both case-sensitive and insensitive volumes.
    first.rename(left_dir / "Case.aaf")
    first = left_dir / "Case.aaf"
    second = make_aaf(name="case.aaf")
    second.rename(right_dir / "case.aaf")
    second = right_dir / "case.aaf"
    output_dir = tmp_path / "output"
    before = {path: sha256_file(path) for path in [first, second]}
    arguments = ["convert", str(first), str(second), "-o", str(output_dir), "--overwrite"]
    if dry_run:
        arguments.append("--dry-run")

    exit_code = cli.main(arguments)

    assert exit_code == 1
    assert not output_dir.exists()
    assert {path: sha256_file(path) for path in before} == before
    assert "[실패]" in capsys.readouterr().err


@pytest.mark.parametrize("dry_run", [False, True])
def test_convert_batch_rejects_unicode_normalization_output_collisions(
    make_aaf,
    tmp_path: Path,
    dry_run: bool,
    capsys,
) -> None:
    left_dir = tmp_path / "left"
    right_dir = tmp_path / "right"
    left_dir.mkdir()
    right_dir.mkdir()
    composed = make_aaf(name="Caf\u00e9.aaf")
    composed.rename(left_dir / composed.name)
    first = left_dir / composed.name
    decomposed = make_aaf(name="Cafe\u0301.aaf")
    decomposed.rename(right_dir / decomposed.name)
    second = right_dir / decomposed.name
    output_dir = tmp_path / "output"
    before = {path: sha256_file(path) for path in [first, second]}
    arguments = ["convert", str(first), str(second), "-o", str(output_dir), "--overwrite"]
    if dry_run:
        arguments.append("--dry-run")

    exit_code = cli.main(arguments)

    assert exit_code == 1
    assert not output_dir.exists()
    assert {path: sha256_file(path) for path in before} == before
    assert "[실패]" in capsys.readouterr().err


def test_convert_batch_rejects_existing_output_identity_collision(
    make_aaf,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    first = make_aaf(name="first.aaf")
    second = make_aaf(name="second.aaf")
    first_output = make_aaf(name="first.nuendo.aaf", compatible=True)
    second_output = tmp_path / "second.nuendo.aaf"
    try:
        os.link(first_output, second_output)
    except OSError as error:
        pytest.skip(f"filesystem does not support hard links: {error}")
    before = {path: sha256_file(path) for path in [first, second, first_output, second_output]}
    calls = []
    monkeypatch.setattr(cli, "convert_aaf", lambda *args, **kwargs: calls.append(args))

    exit_code = cli.main(["convert", str(first), str(second), "--overwrite", "--json"])

    assert exit_code == 1
    assert not calls
    assert {path: sha256_file(path) for path in before} == before
    assert json.loads(capsys.readouterr().out)[0]["status"] == "failed"


@pytest.mark.parametrize(
    "marker",
    [
        "", ".", "..", "../escape", "\\escape", "a/b", "a\\b", ".aaf:stream",
        "x?y", "x|y", "x\x00y", "x\ny", "x\x7fy",
    ],
)
def test_convert_invalid_marker_is_rejected_before_writing(
    make_aaf,
    marker: str,
    capsys,
) -> None:
    source = make_aaf()
    before = sha256_file(source)

    exit_code = cli.main(["convert", str(source), "--marker", marker, "--overwrite"])

    assert exit_code == 1
    assert sha256_file(source) == before
    assert sorted(path.name for path in source.parent.iterdir()) == [source.name]
    assert "[실패]" in capsys.readouterr().err
