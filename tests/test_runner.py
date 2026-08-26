import subprocess
from pathlib import Path

import pytest

from primesim_bridge.models import ExecutionStatus
from primesim_bridge.runner import PrimeSimSimulator, RemoteSpec
from primesim_bridge import runner


def completed(argv, returncode=0):
    return subprocess.CompletedProcess(argv, returncode, stdout="", stderr="")


def make_netlist(tmp_path):
    netlist = tmp_path / "source" / "tb.sp"
    netlist.parent.mkdir()
    netlist.write_text("* synthetic test deck\n.end\n")
    return netlist


def enable_local_binary(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda binary: f"/tools/{binary}")


def test_end_to_end_argv_and_measure_assembly(tmp_path, monkeypatch):
    netlist = make_netlist(tmp_path)
    include = tmp_path / "models" / "device.inc"
    include.parent.mkdir()
    include.write_text("* model\n")
    enable_local_binary(monkeypatch)
    calls = []

    def fake_exec(argv, *, timeout):
        calls.append((argv, timeout))
        prefix = tmp_path / "runs" / "tb" / "tb"
        Path(str(prefix) + ".mt0.csv").write_text("delay,gain\n1n,2\n2n,3\n")
        return completed(argv)

    monkeypatch.setattr(runner, "_exec", fake_exec)
    simulator = PrimeSimSimulator(work_dir=tmp_path / "runs", binary="prime-custom")
    result = simulator.run_simulation(
        netlist,
        {
            "engine": "spice",
            "runlvl": 5,
            "threads": 4,
            "waveform_format": "fsdb",
            "include_files": [include],
        },
    )
    argv = result.metadata["argv"]
    assert argv[:4] == ["prime-custom", "-spice", "-runlvl", "5"]
    assert argv[4:7] == [str(netlist), "-o", str(tmp_path / "runs" / "tb" / "tb")]
    assert ["-mt", "4"] == argv[argv.index("-mt") : argv.index("-mt") + 2]
    assert ["-format", "fsdb"] == argv[
        argv.index("-format") : argv.index("-format") + 2
    ]
    assert argv[-2:] == ["-afile", str(netlist.parent / "device.inc")]
    assert (netlist.parent / "device.inc").read_text() == "* model\n"
    assert calls[0][1] == 3600
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data == {"delay": [1e-9, 2e-9], "gain": [2.0, 3.0]}
    assert result.metadata["_rows"] == 2


@pytest.mark.parametrize(
    "returncode,expected_status",
    [(0, ExecutionStatus.SUCCESS), (13, ExecutionStatus.FAILURE), (27, ExecutionStatus.FAILURE)],
)
def test_exit_code_to_status_flow(tmp_path, monkeypatch, returncode, expected_status):
    netlist = make_netlist(tmp_path)
    enable_local_binary(monkeypatch)
    monkeypatch.setattr(
        runner, "_exec", lambda argv, *, timeout: completed(argv, returncode)
    )
    result = PrimeSimSimulator(work_dir=tmp_path / "runs").run_simulation(netlist)
    assert result.status is expected_status
    if returncode:
        assert result.errors


def test_exit_zero_with_log_error_is_partial(tmp_path, monkeypatch):
    netlist = make_netlist(tmp_path)
    enable_local_binary(monkeypatch)

    def fake_exec(argv, *, timeout):
        (tmp_path / "runs" / "tb" / "tb.log").write_text("ERROR: bad device\n")
        return completed(argv)

    monkeypatch.setattr(runner, "_exec", fake_exec)
    result = PrimeSimSimulator(work_dir=tmp_path / "runs").run_simulation(netlist)
    assert result.status is ExecutionStatus.PARTIAL
    assert result.errors == ["ERROR: bad device"]


def test_parallel_wait_with_log_error_is_failure(tmp_path, monkeypatch):
    netlist = make_netlist(tmp_path)
    enable_local_binary(monkeypatch)

    def fake_exec(argv, *, timeout):
        (tmp_path / "runs" / "tb" / "tb.log").write_text("ERROR: remote worker failed\n")
        return completed(argv)

    monkeypatch.setattr(runner, "_exec", fake_exec)
    result = PrimeSimSimulator(work_dir=tmp_path / "runs").run_simulation(
        netlist, {"is_parallel_wait": True}
    )
    assert result.status is ExecutionStatus.FAILURE


@pytest.mark.parametrize(
    "extra_args,expected_status,expected_warning",
    [([], ExecutionStatus.PARTIAL, False),
     (["-aopt", "primesim_exit_dc_fail=0"], ExecutionStatus.SUCCESS, True)],
)
def test_dc_signature_promotion_depends_on_safety_injection(
    tmp_path, monkeypatch, extra_args, expected_status, expected_warning
):
    netlist = make_netlist(tmp_path)
    enable_local_binary(monkeypatch)

    def fake_exec(argv, *, timeout):
        (tmp_path / "runs" / "tb" / "tb.log").write_text("DC not converged\n")
        return completed(argv)

    monkeypatch.setattr(runner, "_exec", fake_exec)
    result = PrimeSimSimulator(work_dir=tmp_path / "runs").run_simulation(
        netlist, {"extra_args": extra_args}
    )
    assert result.status is expected_status
    assert bool(result.warnings) is expected_warning
    assert bool(result.errors) is (not expected_warning)


def test_binary_missing_returns_failure_without_exec(tmp_path, monkeypatch):
    netlist = make_netlist(tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda binary: None)
    monkeypatch.setattr(
        runner,
        "_exec",
        lambda argv, *, timeout: pytest.fail("_exec should not be called"),
    )
    result = PrimeSimSimulator(work_dir=tmp_path / "runs", binary="missing").run_simulation(netlist)
    assert result.status is ExecutionStatus.FAILURE
    assert result.errors == ["primesim executable not found: missing"]
    assert result.metadata["argv"][0] == "missing"


def test_env_setup_skips_which_and_wraps_exec(tmp_path, monkeypatch):
    netlist = make_netlist(tmp_path)
    monkeypatch.setattr(
        runner.shutil,
        "which",
        lambda binary: pytest.fail("which should not be called"),
    )
    calls = []

    def fake_exec(argv, *, timeout):
        calls.append(argv)
        return completed(argv)

    monkeypatch.setattr(runner, "_exec", fake_exec)
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs", env_setup="/tools/setup.sh"
    ).run_simulation(netlist)
    assert result.status is ExecutionStatus.SUCCESS
    assert calls[0][:2] == ["sh", "-c"]
    assert calls[0][2].startswith(". /tools/setup.sh && exec primesim -spice")


def test_csh_env_setup_wrapper_shape(tmp_path, monkeypatch):
    netlist = make_netlist(tmp_path)
    calls = []
    monkeypatch.setattr(
        runner,
        "_exec",
        lambda argv, *, timeout: calls.append(argv) or completed(argv),
    )
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs",
        env_setup="/tools/setup.csh",
        env_setup_shell="csh",
    ).run_simulation(netlist)
    assert result.status is ExecutionStatus.SUCCESS
    assert calls[0][:2] == ["csh", "-fc"]
    assert calls[0][2].startswith("source /tools/setup.csh; exec primesim -spice")


def test_from_env_reads_only_documented_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("VB_PRIMESIM_BIN", "configured-primesim")
    monkeypatch.setenv("VB_SYNOPSYS_SETUP", "/tools/setup.csh")
    monkeypatch.setenv("VB_SYNOPSYS_SETUP_SHELL", "csh")
    monkeypatch.setenv("VB_REMOTE_HOST", "compute")
    monkeypatch.setenv("VB_REMOTE_USER", "alice")
    simulator = PrimeSimSimulator.from_env(work_dir=tmp_path)
    assert simulator.binary == "configured-primesim"
    assert simulator.env_setup == "/tools/setup.csh"
    assert simulator.env_setup_shell == "csh"
    assert simulator.remote == RemoteSpec(host="compute", user="alice")


@pytest.mark.parametrize("with_artifact", [False, True])
def test_timeout_status_depends_on_artifacts(tmp_path, monkeypatch, with_artifact):
    netlist = make_netlist(tmp_path)
    enable_local_binary(monkeypatch)

    def fake_exec(argv, *, timeout):
        if with_artifact:
            (tmp_path / "runs" / "tb" / "tb.log").write_text("WARNING: incomplete\n")
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(runner, "_exec", fake_exec)
    result = PrimeSimSimulator(work_dir=tmp_path / "runs", timeout=7).run_simulation(netlist)
    assert result.status is (
        ExecutionStatus.PARTIAL if with_artifact else ExecutionStatus.FAILURE
    )
    assert "timeout after 7s" in result.errors


def test_default_prefix_uniquifies_directory_only(tmp_path, monkeypatch):
    netlist = make_netlist(tmp_path)
    enable_local_binary(monkeypatch)
    monkeypatch.setattr(runner, "_exec", lambda argv, *, timeout: completed(argv))
    simulator = PrimeSimSimulator(work_dir=tmp_path / "runs")
    first = simulator.run_simulation(netlist)
    second = simulator.run_simulation(netlist)
    assert first.metadata["prefix"].endswith("/tb/tb")
    assert second.metadata["prefix"].endswith("/tb_2/tb")


def test_remote_sequence_and_argv_shapes(tmp_path, monkeypatch):
    netlist = make_netlist(tmp_path)
    include = tmp_path / "model.inc"
    include.write_text("* model\n")
    calls = []

    def fake_exec(argv, *, timeout):
        calls.append(argv)
        if argv[0] == "scp" and argv[1] == "-r":
            (tmp_path / "runs" / "tb" / "tb.log").write_text("run complete\n")
        return completed(argv)

    monkeypatch.setattr(runner, "_exec", fake_exec)
    simulator = PrimeSimSimulator(
        work_dir=tmp_path / "runs",
        remote=RemoteSpec(host="compute", user="alice"),
        run_id_factory=lambda: "fixed-run",
    )
    result = simulator.run_simulation(netlist, {"include_files": [include]})
    assert [call[0] for call in calls] == ["scp", "ssh", "scp"]
    assert calls[0] == [
        "scp",
        str(netlist),
        str(include),
        "alice@compute:~/.primesim_bridge/runs/fixed-run/",
    ]
    assert calls[1][0:2] == ["ssh", "alice@compute"]
    assert calls[1][2].startswith("cd ~/.primesim_bridge/runs/fixed-run && primesim -spice tb.sp")
    assert calls[2] == [
        "scp",
        "-r",
        "alice@compute:~/.primesim_bridge/runs/fixed-run/.",
        str(tmp_path / "runs" / "tb"),
    ]
    assert result.metadata["argv"][-2:] == ["-afile", "model.inc"]
    assert result.metadata["remote_cmds"] == calls
    assert result.status is ExecutionStatus.SUCCESS


def test_remote_upload_failure_names_stage(tmp_path, monkeypatch):
    netlist = make_netlist(tmp_path)
    monkeypatch.setattr(
        runner, "_exec", lambda argv, *, timeout: completed(argv, returncode=1)
    )
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs", remote=RemoteSpec(host="compute")
    ).run_simulation(netlist)
    assert result.status is ExecutionStatus.FAILURE
    assert "remote upload stage failed" in result.errors
    assert len(result.metadata["remote_cmds"]) == 1
