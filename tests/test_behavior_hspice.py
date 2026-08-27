from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from primesim_bridge import runner
from primesim_bridge.models import EXIT_CODES_HSPICE, ExecutionStatus
from primesim_bridge.runner import PrimeSimSimulator, RemoteSpec
from tests import fake_hspice


@pytest.fixture(scope="module")
def fake_hspice_path() -> Path:
    path = Path(__file__).parent / "fake_hspice.py"
    assert os.access(path, os.X_OK)
    return path


def deck(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "tb.sp"
    path.write_text(body)
    return path


def simulator(tmp_path: Path, fake_path: Path, **kwargs) -> PrimeSimSimulator:
    return PrimeSimSimulator(
        work_dir=tmp_path / "runs", binary=str(fake_path), **kwargs
    )


def run_hspice(tmp_path: Path, fake_path: Path, body: str, options=None):
    return simulator(tmp_path, fake_path).run_simulation(
        deck(tmp_path, body), {"engine": "hspice", **(options or {})}
    )


def test_fake_hspice_is_executable(fake_hspice_path):
    assert fake_hspice_path.stat().st_mode & stat.S_IXUSR


def test_fake_hspice_parser_orders_include_views(tmp_path):
    first = tmp_path / "first.sp"
    main = tmp_path / "main.sp"
    last = tmp_path / "last.sp"
    first.write_text("first\n")
    main.write_text("main\n")
    last.write_text("last\n")
    parsed = fake_hspice.parse_args(
        [
            "-case",
            "1",
            "-include_last",
            str(last),
            "-o",
            "out",
            "-i",
            str(main),
            "-include_first",
            str(first),
        ]
    )
    assert parsed == (str(main), "out", [str(first)], [str(last)])
    assert fake_hspice.deck_view(parsed[0], parsed[2], parsed[3]) == (
        "first\nmain\nlast\n"
    )


def test_success_measures_and_injected_safety(tmp_path, fake_hspice_path):
    result = run_hspice(
        tmp_path,
        fake_hspice_path,
        "* fake:measure=delay=1e-9\n* fake:measure=gain=12.5\n.end\n",
    )
    prefix = Path(result.metadata["prefix"])
    aux = prefix.parent / "psb_hspice_options.sp"
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data == {"delay": 1e-9, "gain": 12.5}
    assert result.metadata["engine"] == "hspice"
    assert result.metadata["transport"] == "local"
    assert result.metadata["output_files"]["measure"] == [str(prefix) + ".mt0.csv"]
    assert result.metadata["argv"][result.metadata["argv"].index("-include_first") + 1] == str(aux)
    assert ".option measform=3" in aux.read_text()
    assert ".option measfail=1" in aux.read_text()


def test_no_safety_uses_classic_measure_format(tmp_path, fake_hspice_path):
    result = run_hspice(
        tmp_path,
        fake_hspice_path,
        "* fake:measure=delay=1e-9\n* fake:measure=gain=12.5\n.end\n",
        {"no_safety": True},
    )
    prefix = Path(result.metadata["prefix"])
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data == {"delay": 1e-9, "gain": 12.5}
    assert "-include_first" not in result.metadata["argv"]
    assert result.metadata["output_files"]["measure"] == [str(prefix) + ".mt0"]


def test_exit_failure_keeps_log_and_exit_errors(tmp_path, fake_hspice_path):
    result = run_hspice(
        tmp_path,
        fake_hspice_path,
        "* fake:error=no convergence in operating point\n* fake:exit=1\n.end\n",
    )
    assert result.status is ExecutionStatus.FAILURE
    assert EXIT_CODES_HSPICE[1] in result.errors
    assert "**error** no convergence in operating point" in result.errors


def test_exit_zero_without_banner_is_partial(tmp_path, fake_hspice_path):
    result = run_hspice(tmp_path, fake_hspice_path, "* fake:no_banner\n.end\n")
    assert result.status is ExecutionStatus.PARTIAL
    assert result.errors == []
    assert result.warnings == ["exit 0 but no 'job concluded' banner in .lis"]


def test_license_exit_is_failure(tmp_path, fake_hspice_path):
    result = run_hspice(tmp_path, fake_hspice_path, "* fake:exit=2\n.end\n")
    assert result.status is ExecutionStatus.FAILURE
    assert result.errors == [EXIT_CODES_HSPICE[2]]


def test_hspice_dc_log_line_is_warning_only(tmp_path, fake_hspice_path):
    result = run_hspice(
        tmp_path, fake_hspice_path, "* fake:log=DC not converged\n.end\n"
    )
    assert result.status is ExecutionStatus.SUCCESS
    assert result.errors == []
    assert result.warnings == ["DC not converged"]


@pytest.mark.parametrize("no_safety", [False, True])
def test_failed_measure_is_none_in_both_formats(
    tmp_path, fake_hspice_path, no_safety
):
    result = run_hspice(
        tmp_path,
        fake_hspice_path,
        "* fake:measure=vout=3.0\n* fake:measure_failed=vout\n.end\n",
        {"no_safety": no_safety},
    )
    assert result.data["vout"] is None
    assert result.warnings == ["measure vout failed in row 1"]


@pytest.mark.parametrize("no_safety,suffix", [(False, ".csv"), (True, "")])
def test_alter_measures_are_aggregated_with_metadata(
    tmp_path, fake_hspice_path, no_safety, suffix
):
    result = run_hspice(
        tmp_path,
        fake_hspice_path,
        "* fake:measure=base=10\n* fake:alter_measures=2\n.end\n",
        {"no_safety": no_safety},
    )
    assert result.data == {"base": 10.0, "alter1": 1.0, "alter2": 2.0}
    assert result.metadata["alter_measures"] == {
        f"tb.mt1{suffix}": {"alter1": 1.0},
        f"tb.mt2{suffix}": {"alter2": 2.0},
    }


def test_include_last_staging_changes_fake_deck_view(tmp_path, fake_hspice_path):
    include = tmp_path / "models" / "options.inc"
    include.parent.mkdir()
    include.write_text("* fake:measure=from_include=7\n")
    result = run_hspice(
        tmp_path,
        fake_hspice_path,
        ".end\n",
        {"include_files": [include]},
    )
    staged = tmp_path / "options.inc"
    assert result.data == {"from_include": 7.0}
    assert result.metadata["argv"][-2:] == ["-include_last", str(staged)]
    assert staged.read_text() == include.read_text()


def test_waveview_generation_for_hspice_fsdb(tmp_path, fake_hspice_path):
    result = run_hspice(
        tmp_path,
        fake_hspice_path,
        "* fake:fsdb\n.probe tran v(out)\n.end\n",
        {"waveview_script": True},
    )
    script = Path(result.metadata["waveview"]["script"])
    assert result.status is ExecutionStatus.SUCCESS
    assert script.is_file()
    assert "sx_display {v(out)}" in script.read_text()


def test_sync_lsf_wrapper_around_fake_hspice(tmp_path, fake_hspice_path):
    wrapper = tmp_path / "bsub_sync.sh"
    wrapper.write_text(f'#!/bin/sh\nexec "{fake_hspice_path}" "$@"\n')
    wrapper.chmod(0o755)
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs", binary=str(wrapper)
    ).run_simulation(deck(tmp_path, "* fake:measure=vout=2.5\n.end\n"), {"engine": "hspice"})
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data == {"vout": 2.5}


def test_stdout_trap_without_output_prefix(tmp_path, fake_hspice_path):
    input_deck = deck(tmp_path, "* stdout trap marker\n.end\n")
    completed = subprocess.run(
        [sys.executable, str(fake_hspice_path), "-i", str(input_deck)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "stdout trap marker" in completed.stdout
    assert not (tmp_path / "tb.lis").exists()


def test_remote_hspice_uses_basenames_and_uploads_aux_last(
    tmp_path, fake_hspice_path, monkeypatch
):
    monkeypatch.setenv("PSB_NO_COMPANION", "1")
    runner._companion.reset_cache()
    input_deck = deck(tmp_path, ".end\n")
    include = tmp_path / "model.inc"
    include.write_text("* model\n")
    calls = []

    def fake_exec(argv, *, timeout):
        calls.append(argv)
        if argv[:2] == ["scp", "-r"]:
            output_prefix = tmp_path / "runs" / "tb" / "tb"
            Path(str(output_prefix) + ".lis").write_text(
                "***** job concluded ******\n"
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_exec", fake_exec)
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs",
        binary="hspice-custom",
        remote=RemoteSpec(host="compute"),
        run_id_factory=lambda: "hspice-run",
    ).run_simulation(
        input_deck, {"engine": "hspice", "include_files": [include]}
    )
    aux = tmp_path / "runs" / "tb" / "psb_hspice_options.sp"
    assert calls[1] == [
        "scp",
        str(input_deck),
        str(include),
        str(aux),
        "compute:.primesim_bridge/runs/hspice-run/",
    ]
    assert result.metadata["argv"] == [
        "hspice-custom",
        "-i",
        "tb.sp",
        "-o",
        "tb",
        "-include_first",
        "psb_hspice_options.sp",
        "-include_last",
        "model.inc",
    ]
    assert "cd .primesim_bridge/runs/hspice-run && hspice-custom -i tb.sp" in calls[2][2]
    assert result.status is ExecutionStatus.SUCCESS


def test_primesim_no_safety_keeps_dc_warning(tmp_path, fake_primesim_path):
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs", binary=str(fake_primesim_path)
    ).run_simulation(deck(tmp_path, "* fake:dc_fail\n.end\n"), {"no_safety": True})
    assert result.status is ExecutionStatus.SUCCESS
    assert result.errors == []
    assert result.warnings == ["DC not converged"]
    assert result.metadata["engine"] == "primesim"


@pytest.mark.parametrize(
    "engine,expected_error",
    [
        ("spice", "primesim executable not found: missing"),
        ("pro", "primesim executable not found: missing"),
        ("hspice", "hspice executable not found: missing"),
    ],
)
def test_binary_missing_metadata_has_engine(
    tmp_path, monkeypatch, engine, expected_error
):
    monkeypatch.setattr(runner.shutil, "which", lambda binary: None)
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs", binary="missing"
    ).run_simulation(deck(tmp_path, ".end\n"), {"engine": engine})
    assert result.status is ExecutionStatus.FAILURE
    assert result.errors == [expected_error]
    assert result.metadata["engine"] == (
        "hspice" if engine == "hspice" else "primesim"
    )


def test_from_env_resolves_binary_after_hspice_profile_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("PSB_REMOTE_HOST", "")
    monkeypatch.setenv("VB_PRIMESIM_BIN", "configured-primesim")
    monkeypatch.setenv("VB_HSPICE_BIN", "configured-hspice")
    monkeypatch.delenv("VB_SYNOPSYS_SETUP", raising=False)
    monkeypatch.setattr(runner.shutil, "which", lambda binary: None)
    sim = PrimeSimSimulator.from_env(work_dir=tmp_path / "runs")
    assert sim.binary == "configured-primesim"
    result = sim.run_simulation(deck(tmp_path, ".end\n"), {"engine": "hspice"})
    assert result.metadata["argv"][0] == "configured-hspice"
    assert result.errors == [
        "hspice executable not found: configured-hspice"
    ]
