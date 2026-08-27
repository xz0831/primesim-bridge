import json
from pathlib import Path

import pytest

from primesim_bridge import cli
from primesim_bridge.models import ExecutionStatus, SimulationResult


FIXTURES = Path(__file__).parent / "fixtures"


def test_dry_run_is_one_line_and_touches_no_filesystem(tmp_path, monkeypatch, capsys):
    nonexistent = tmp_path / "does-not-exist" / "tb.sp"
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: (_ for _ in ()).throw(AssertionError("exists called")),
    )
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(AssertionError("mkdir called")),
    )
    assert cli.main(["run", str(nonexistent), "--dry-run"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    line = lines[0]
    assert "-spice" in line
    assert "-o" in line
    assert "-aopt primesim_exit_dc_fail=1" in line
    assert "-aopt primesim_measout=3" in line


def test_parse_subcommand_on_fixture_artifacts(tmp_path, capsys):
    prefix = tmp_path / "result"
    (tmp_path / "result.mt0.csv").write_text(
        (FIXTURES / "measure_single.mt0.csv").read_text()
    )
    (tmp_path / "result.log").write_text("WARNING: synthetic warning\n")
    assert cli.main(["parse", str(prefix)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["data"]["delay"] == pytest.approx(1.5e-9)
    assert output["data"]["gain"] == 12.5
    assert output["warnings"] == ["WARNING: synthetic warning"]
    assert output["metadata"]["output_files"]["measure"] == [
        str(tmp_path / "result.mt0.csv")
    ]


def test_status_never_raises_without_tools(monkeypatch, capsys):
    monkeypatch.setenv("PSB_NO_COMPANION", "1")
    cli._companion.reset_cache()
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli.main(["status"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["primesim"] is None
    assert output["license_tokens"] == []
    assert output["companion"] == {
        "available": False,
        "version": "unknown",
        "verified": False,
        "capabilities": [],
        "env_file": None,
    }


def test_run_waveforms_flag_requests_postprocessing(tmp_path, monkeypatch, capsys):
    captured = {}

    class StubSimulator:
        def run_simulation(self, netlist, options):
            captured.update(options)
            return SimulationResult(
                status=ExecutionStatus.SUCCESS,
                data={},
                errors=[],
                warnings=[],
                metadata={},
            )

    monkeypatch.setattr(
        cli.PrimeSimSimulator,
        "from_env",
        lambda **overrides: StubSimulator(),
    )
    assert cli.main(["run", str(tmp_path / "tb.sp"), "--waveforms"]) == 0
    assert captured["parse_waveforms"] is True
    assert json.loads(capsys.readouterr().out)["status"] == "SUCCESS"
