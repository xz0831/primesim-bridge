from pathlib import Path

import pytest

from primesim_bridge.parsers import collect_outputs


@pytest.mark.parametrize(
    "suffix",
    [
        ".mt",
        ".mt.csv",
        ".mt.gz",
        ".mt.csv.gzip",
        ".a0.t0.mt",
        ".a1.t2.mt.csv",
    ],
)
def test_xa_digitless_measure_suffixes_are_bucketed_as_measure(tmp_path, suffix):
    prefix = tmp_path / "xa"
    artifact = tmp_path / f"xa{suffix}"
    artifact.write_bytes(b"")
    assert collect_outputs(prefix)["measure"] == [artifact]


@pytest.mark.parametrize("suffix", [".out", ".psf"])
def test_xa_out_and_psf_are_bucketed_as_waveforms(tmp_path, suffix):
    prefix = tmp_path / "xa"
    artifact = tmp_path / f"xa{suffix}"
    artifact.write_bytes(b"")
    assert collect_outputs(prefix)["waveform"] == [artifact]


@pytest.mark.parametrize(
    "suffix",
    [
        ".mc",
        ".valog",
        ".errt",
        ".errz",
        ".hotspot",
        ".power",
        ".rcxt",
        ".err",
        ".hiz",
    ],
)
def test_xa_non_measure_artifacts_remain_other(tmp_path, suffix):
    prefix = tmp_path / "xa"
    artifact = tmp_path / f"xa{suffix}"
    artifact.write_bytes(b"")
    outputs = collect_outputs(prefix)
    assert outputs["measure"] == []
    assert outputs["other"] == [artifact]
