from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from primesim_bridge import cli, runner
from primesim_bridge.models import ExecutionStatus, SimulationResult
from primesim_bridge.runner import PrimeSimSimulator, RemoteSpec
from tests import fake_xa


@pytest.fixture(scope="module")
def fake_xa_path() -> Path:
    path = Path(__file__).parent / "fake_xa.py"
    os.chmod(path, 0o755)
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


def run_xa(tmp_path: Path, fake_path: Path, body: str, options=None):
    return simulator(tmp_path, fake_path).run_simulation(
        deck(tmp_path, body), {"engine": "xa", **(options or {})}
    )


def test_fake_xa_is_executable(fake_xa_path):
    assert fake_xa_path.stat().st_mode & stat.S_IXUSR


def test_fake_xa_parser_uses_first_positional_and_repeatable_commands(tmp_path):
    first = tmp_path / "first.cmd"
    second = tmp_path / "second.cmd"
    first.write_text("first\n")
    second.write_text("second\n")
    parsed = fake_xa.parse_args(
        [
            "-spectre",
            "tb.sp",
            "ignored.sp",
            "-c",
            str(first),
            "-mt",
            "2",
            "-c",
            str(second),
            "-o",
            "out",
        ]
    )
    assert parsed == ("tb.sp", "out", [str(first), str(second)])
    assert fake_xa.deck_view(None, parsed[2]) == "first\nsecond\n"


def test_success_measures_and_injected_safety(tmp_path, fake_xa_path):
    result = run_xa(
        tmp_path,
        fake_xa_path,
        "* fake:measure=delay=1e-9\n* fake:measure=gain=12.5\n.end\n",
    )
    prefix = Path(result.metadata["prefix"])
    aux = prefix.parent / "psb_xa.cmd"
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data == {"delay": 1e-9, "gain": 12.5}
    assert result.metadata["engine"] == "xa"
    assert result.metadata["transport"] == "local"
    assert result.metadata["output_files"]["measure"] == [str(prefix) + ".mt"]
    argv = result.metadata["argv"]
    assert argv[argv.index("-c") + 1] == str(aux)
    assert aux.read_text() == (
        "# injected by primesim-bridge\n"
        "set_meas_option -format hspice\n"
    )


def test_no_safety_keeps_xa_measure_as_unstructured_raw_lines(
    tmp_path, fake_xa_path
):
    result = run_xa(
        tmp_path,
        fake_xa_path,
        "* fake:measure=vout=2.5\n.end\n",
        {"no_safety": True},
    )
    prefix = Path(result.metadata["prefix"])
    assert result.status is ExecutionStatus.SUCCESS
    assert "-c" not in result.metadata["argv"]
    assert result.metadata["output_files"]["measure"] == [str(prefix) + ".meas"]
    assert result.data.get("parse_confidence") == "low"
    assert "vout" not in result.data
    assert any("vout" in line for line in result.data["raw_lines"])


def test_nonzero_exit_keeps_xa_log_error_and_plain_exit_error(
    tmp_path, fake_xa_path
):
    result = run_xa(
        tmp_path,
        fake_xa_path,
        "* fake:error=simulation aborted\n* fake:exit=2\n.end\n",
    )
    assert result.status is ExecutionStatus.FAILURE
    assert "exit code 2" in result.errors
    assert "Error: simulation aborted" in result.errors
    assert result.metadata["engine"] == "xa"


def test_walltime_proxy_warning_is_multicore_only(tmp_path, fake_xa_path):
    multi_dir = tmp_path / "multi"
    multi_dir.mkdir()
    multi = run_xa(
        multi_dir,
        fake_xa_path,
        "* fake:no_walltime\n.end\n",
        {"threads": 2},
    )
    assert multi.status is ExecutionStatus.SUCCESS
    assert multi.warnings == [
        "exit 0 but no 'Total Wall Time' end-of-log marker "
        "(undocumented success proxy)"
    ]

    single_dir = tmp_path / "single"
    single_dir.mkdir()
    single = run_xa(
        single_dir,
        fake_xa_path,
        "* fake:no_walltime\n.end\n",
    )
    assert single.status is ExecutionStatus.SUCCESS
    assert single.warnings == []


def test_exit_zero_with_xa_error_is_partial(tmp_path, fake_xa_path):
    result = run_xa(
        tmp_path,
        fake_xa_path,
        "* fake:error=bad model\n.end\n",
    )
    assert result.status is ExecutionStatus.PARTIAL
    assert result.errors == ["Error: bad model"]


def test_waveview_generation_for_xa_fsdb(tmp_path, fake_xa_path):
    result = run_xa(
        tmp_path,
        fake_xa_path,
        "* fake:fsdb\n.probe tran v(out)\n.end\n",
        {"waveview_script": True},
    )
    script = Path(result.metadata["waveview"]["script"])
    assert result.status is ExecutionStatus.SUCCESS
    assert script.is_file()
    assert "sx_display {v(out)}" in script.read_text()


def test_sync_lsf_wrapper_around_fake_xa(tmp_path, fake_xa_path):
    wrapper = tmp_path / "bsub_sync.sh"
    wrapper.write_text(f'#!/bin/sh\nexec "{fake_xa_path}" "$@"\n')
    wrapper.chmod(0o755)
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs", binary=str(wrapper)
    ).run_simulation(
        deck(tmp_path, "* fake:measure=vout=2.5\n.end\n"),
        {"engine": "xa"},
    )
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data == {"vout": 2.5}
    assert result.metadata["engine"] == "xa"


def test_remote_xa_uses_basenames_and_uploads_aux_after_netlist(
    tmp_path, fake_xa_path, monkeypatch
):
    del fake_xa_path
    monkeypatch.setenv("PSB_NO_COMPANION", "1")
    runner._companion.reset_cache()
    input_deck = deck(tmp_path, ".end\n")
    calls = []

    def fake_exec(argv, *, timeout):
        del timeout
        calls.append(argv)
        if argv[:2] == ["scp", "-r"]:
            output_prefix = tmp_path / "runs" / "tb" / "tb"
            Path(str(output_prefix) + ".log").write_text(
                "Total Wall Time = 1 sec (0hr 0min 1sec)\n"
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_exec", fake_exec)
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs",
        binary="xa-custom",
        remote=RemoteSpec(host="compute"),
        run_id_factory=lambda: "xa-run",
    ).run_simulation(input_deck, {"engine": "xa"})
    aux = tmp_path / "runs" / "tb" / "psb_xa.cmd"
    assert calls[1] == [
        "scp",
        str(input_deck),
        str(aux),
        "compute:.primesim_bridge/runs/xa-run/",
    ]
    assert result.metadata["argv"] == [
        "xa-custom",
        "tb.sp",
        "-o",
        "tb",
        "-c",
        "psb_xa.cmd",
    ]
    assert result.status is ExecutionStatus.SUCCESS
    assert result.metadata["engine"] == "xa"


def test_no_output_option_writes_literal_xa_prefix_in_process_cwd(
    tmp_path, fake_xa_path
):
    input_deck = deck(tmp_path, ".end\n")
    completed = subprocess.run(
        [sys.executable, str(fake_xa_path), str(input_deck)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert (tmp_path / "xa.log").is_file()


def test_xa_cli_dry_run_dialect_and_default_argv(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(
        ["run", "tb.sp", "--engine", "xa", "--dialect", "spectre", "--dry-run"]
    ) == 0
    assert shlex.split(capsys.readouterr().out) == [
        "xa",
        "-spectre",
        "tb.sp",
        "-o",
        "tb",
        "-c",
        "psb_xa.cmd",
    ]

    assert cli.main(["run", "tb.sp", "--engine", "xa", "--dry-run"]) == 0
    default_argv = shlex.split(capsys.readouterr().out)
    assert default_argv == ["xa", "tb.sp", "-o", "tb", "-c", "psb_xa.cmd"]
    assert "-format" not in default_argv
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "option,message",
    [
        (
            ["--runlvl", "3"],
            "xa accuracy is set with set_sim_level / -sim_mode, not runlvl/mode",
        ),
        (["--log", "custom.log"], "xa log is always <prefix>.log"),
    ],
)
def test_xa_cli_validation_exits_two(option, message, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["run", "tb.sp", "--engine", "xa", "--dry-run", *option])
    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def test_xa_cli_plumbs_dialect_into_non_dry_options(monkeypatch, capsys):
    captured = {}

    class StubSimulator:
        def run_simulation(self, netlist, options):
            del netlist
            captured.update(options)
            return SimulationResult(
                status=ExecutionStatus.SUCCESS,
                data={},
                errors=[],
                warnings=[],
                metadata={"engine": "xa"},
            )

    monkeypatch.setattr(
        cli.PrimeSimSimulator,
        "from_env",
        lambda **overrides: StubSimulator(),
    )
    assert cli.main(
        ["run", "tb.sp", "--engine", "xa", "--dialect", "eldo"]
    ) == 0
    assert captured["engine"] == "xa"
    assert captured["dialect"] == "eldo"
    assert json.loads(capsys.readouterr().out)["status"] == "SUCCESS"


def test_xa_alter_measure_limitation_merges_last_without_metadata(
    tmp_path, fake_xa_path
):
    prefix = tmp_path / "xa"
    (tmp_path / "xa.a0.mt").write_text(
        "$DATA1 SOURCE='fake.sp' VERSION='FAKE'\n"
        ".TITLE 'alter zero'\n"
        "vout\n"
        "1\n"
    )
    (tmp_path / "xa.a1.mt").write_text(
        "$DATA1 SOURCE='fake.sp' VERSION='FAKE'\n"
        ".TITLE 'alter one'\n"
        "vout\n"
        "2\n"
    )
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs", binary=str(fake_xa_path)
    ).run_simulation(
        deck(tmp_path, ".end\n"),
        {
            "engine": "xa",
            "prefix": prefix,
            "no_safety": True,
        },
    )
    # E2 limitation: .a# carries no E1-recognizable measure index; E3 fixes it.
    assert result.data["vout"] == 2.0
    assert not result.metadata.get("alter_measures")
