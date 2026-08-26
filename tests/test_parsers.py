import gzip
import re
import shutil
from pathlib import Path

import pytest

from primesim_bridge.parsers import (
    collect_outputs,
    parse_hspice_number,
    parse_log_text,
    parse_measure_ascii,
    parse_measure_csv,
    parse_op_ascii,
)


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "token,expected",
    [
        ("1.25e-3", 1.25e-3),
        ("2MEG", 2e6),
        ("2m", 2e-3),
        ("3f", 3e-15),
        ("4T", 4e12),
        ("1.5ns", 1.5e-9),
        ("10pF", 1e-11),
        ("2x", 2e6),
        ("junk", "junk"),
    ],
)
def test_parse_hspice_number(token, expected):
    parsed = parse_hspice_number(token)
    if isinstance(expected, float):
        assert parsed == pytest.approx(expected)
    else:
        assert parsed == expected


def test_parse_measure_csv_single_row():
    parsed = parse_measure_csv(FIXTURES / "measure_single.mt0.csv")
    assert parsed["delay"] == pytest.approx(1.5e-9)
    assert parsed["gain"] == 12.5


def test_parse_measure_csv_multi_row():
    parsed = parse_measure_csv(FIXTURES / "measure_sweep.mt0.csv")
    assert parsed["delay"] == pytest.approx([1e-9, 2e-9, 3e-9])
    assert parsed["gain"] == [10.0, 20.0, 30.0]
    assert parsed["_rows"] == 3


def test_parse_measure_csv_failed_cell():
    parsed = parse_measure_csv(FIXTURES / "measure_failed.mt0.csv")
    assert parsed["delay"] is None
    assert parsed["gain"] == 4.5
    assert parsed["_warnings"] == ["measure delay failed in row 1"]


@pytest.mark.parametrize("compression_suffix", [".gz", ".gzip"])
def test_parse_gzip_compressed_csv(tmp_path, compression_suffix):
    destination = tmp_path / ("result.mt0.csv" + compression_suffix)
    with (FIXTURES / "measure_single.mt0.csv").open("rb") as source:
        with gzip.open(destination, "wb") as compressed:
            shutil.copyfileobj(source, compressed)
    assert parse_measure_csv(destination)["delay"] == pytest.approx(1.5e-9)


def test_parse_documented_classic_measure_shape():
    assert parse_measure_ascii(FIXTURES / "classic.mt0") == {
        "delay": 1.25e-9,
        "gain": 2.5,
    }


def test_malformed_classic_measure_falls_back_without_raise():
    parsed = parse_measure_ascii(FIXTURES / "malformed.mt0")
    assert parsed["parse_confidence"] == "low"
    assert parsed["raw_lines"] == ["delay gain", "1.25n 2.5"]


def test_parse_op_ascii_best_effort():
    parsed = parse_op_ascii(FIXTURES / "operating_point.op0")
    assert parsed == {"v(out)": 1.25, "i(vdd)": -0.0025}


def test_log_signature_and_generic_rules():
    parsed = parse_log_text((FIXTURES / "simulation.log").read_text())
    assert parsed["signatures"] == [
        "DC not converged",
        "ERROR! time step too small (diverged)",
    ]
    assert "DC not converged" in parsed["warnings"]
    assert "ERROR! time step too small (diverged)" in parsed["errors"]
    assert "0 errors" not in parsed["errors"]
    assert "0 warnings" not in parsed["warnings"]


def test_collect_outputs_classifies_conventions_suffixes_and_gzip(tmp_path):
    prefix = tmp_path / "run"
    paths = [
        tmp_path / "run.log",
        tmp_path / "run.mt0.csv.gz",
        tmp_path / "run_a1_s0.mt0",
        tmp_path / "run.pt0",
        tmp_path / "run.op0.gzip",
        tmp_path / "run.fsdb",
        tmp_path / "run.ic",
        tmp_path / "run.unknown",
        tmp_path / "runner.log",
    ]
    for path in paths:
        path.write_bytes(b"")
    convention_one = tmp_path / "run_wdf"
    convention_one.mkdir()
    nested_waveform = convention_one / "tran" / "waveform.data"
    nested_waveform.parent.mkdir()
    nested_waveform.write_bytes(b"")
    outputs = collect_outputs(prefix)
    assert set(outputs["measure"]) == {
        tmp_path / "run.mt0.csv.gz",
        tmp_path / "run_a1_s0.mt0",
    }
    assert outputs["print"] == [tmp_path / "run.pt0"]
    assert outputs["op"] == [tmp_path / "run.op0.gzip"]
    assert outputs["log"] == [tmp_path / "run.log"]
    assert set(outputs["waveform"]) == {tmp_path / "run.fsdb", nested_waveform}
    assert set(outputs["other"]) == {tmp_path / "run.ic", tmp_path / "run.unknown"}


def test_fixture_manifest_is_exact():
    manifest_path = FIXTURES / "MANIFEST.md"
    manifest = manifest_path.read_text()
    actual = {path.name for path in FIXTURES.iterdir() if path.name != "MANIFEST.md"}
    listed = set(re.findall(r"^\| `([^`]+)` \|", manifest, flags=re.MULTILINE))
    assert listed == actual
    for name in listed:
        row = next(line for line in manifest.splitlines() if line.startswith(f"| `{name}` |"))
        assert "SYNTHETIC-FROM-DOC (assumed layout)" in row or "PrimeSim User Guide p" in row
