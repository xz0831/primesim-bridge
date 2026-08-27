import importlib
import os
import subprocess
import sys
import types
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from primesim_bridge import _companion
from primesim_bridge.models import ExecutionStatus
from primesim_bridge.runner import PrimeSimSimulator, RemoteSpec
from primesim_bridge import runner


FIXTURES = Path(__file__).parent / "fixtures"


def _real_companion_enabled():
    if os.environ.get("RUN_COMPANION_TESTS") != "1":
        return False
    try:
        importlib.import_module("virtuoso_bridge")
    except Exception:
        return False
    return True


REAL_COMPANION = _real_companion_enabled()
LIVE_SSH = (
    REAL_COMPANION
    and os.environ.get("RUN_LIVE_SSH_TESTS") == "1"
    and bool(os.environ.get("PSB_TEST_SSH_HOST"))
)


@pytest.fixture(autouse=True)
def reset_companion(monkeypatch):
    monkeypatch.delenv("PSB_NO_COMPANION", raising=False)
    monkeypatch.delenv("PSB_COMPANION_FORCE", raising=False)
    _companion.reset_cache()
    yield
    _companion.reset_cache()


def module(name, *, package=False, **attributes):
    value = types.ModuleType(name)
    if package:
        value.__path__ = []
    for key, item in attributes.items():
        setattr(value, key, item)
    return value


def install_stub_modules(
    monkeypatch, *, ssh_runner=None, parser=None, resolver=None
):
    # Purge every cached real companion module first: with the real package
    # installed, a prior real-tier test leaves e.g. virtuoso_bridge.transport.ssh
    # in sys.modules, and the probe's import_module would return that cached real
    # submodule THROUGH the stubbed parent — granting capabilities the stub
    # deliberately withholds (caught live 2026-08-27, companion tier).
    for name in [k for k in sys.modules if k == "virtuoso_bridge" or k.startswith("virtuoso_bridge.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    modules = {
        "virtuoso_bridge": module("virtuoso_bridge", package=True),
        "virtuoso_bridge.transport": module(
            "virtuoso_bridge.transport", package=True
        ),
        "virtuoso_bridge.spectre": module(
            "virtuoso_bridge.spectre", package=True
        ),
    }
    if ssh_runner is not None:
        modules["virtuoso_bridge.transport.ssh"] = module(
            "virtuoso_bridge.transport.ssh", SSHRunner=ssh_runner
        )
    if parser is not None:
        modules["virtuoso_bridge.spectre.parsers"] = module(
            "virtuoso_bridge.spectre.parsers",
            parse_psf_ascii_directory=parser,
        )
    if resolver is not None:
        modules["virtuoso_bridge.env"] = module(
            "virtuoso_bridge.env", resolve_env_path=resolver
        )
    for name, value in modules.items():
        monkeypatch.setitem(sys.modules, name, value)
    monkeypatch.setattr(_companion.importlib.metadata, "version", lambda name: "0.8.0")
    _companion.reset_cache()


def make_netlist(tmp_path):
    netlist = tmp_path / "source" / "tb.sp"
    netlist.parent.mkdir()
    netlist.write_text("* synthetic test deck\n.end\n")
    return netlist


def completed(argv, returncode=0):
    return subprocess.CompletedProcess(argv, returncode, stdout="", stderr="")


def test_absent_probe_forced_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", None)
    info = _companion.companion_info()
    assert info.available is False
    assert info.version == "unknown"
    assert info.capabilities == frozenset()


def test_absent_runner_falls_back_to_subprocess(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", None)
    calls = []
    monkeypatch.setattr(
        runner,
        "_exec",
        lambda argv, *, timeout: calls.append(argv) or completed(argv),
    )
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs", remote=RemoteSpec(host="compute")
    ).run_simulation(make_netlist(tmp_path))
    assert result.metadata["transport"] == "openssh-subprocess"
    assert [call[0] for call in calls] == ["ssh", "scp", "ssh", "scp"]


def test_absent_tier_b_request_is_warning(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", None)
    monkeypatch.setattr(runner.shutil, "which", lambda binary: "/tools/primesim")
    monkeypatch.setattr(runner, "_exec", lambda argv, *, timeout: completed(argv))
    result = PrimeSimSimulator(work_dir=tmp_path / "runs").run_simulation(
        make_netlist(tmp_path), {"parse_waveforms": True}
    )
    assert result.status is ExecutionStatus.SUCCESS
    assert result.errors == []
    assert result.warnings == [
        "waveform parsing requested but companion package not available"
    ]


def test_tier_b_empty_envelope_warning(tmp_path, monkeypatch):
    install_stub_modules(monkeypatch, parser=lambda path: {})
    monkeypatch.setattr(runner.shutil, "which", lambda binary: "/tools/primesim")

    def fake_exec(argv, *, timeout):
        output_dir = tmp_path / "runs" / "tb" / "tb_tran"
        output_dir.mkdir()
        return completed(argv)

    monkeypatch.setattr(runner, "_exec", fake_exec)
    result = PrimeSimSimulator(work_dir=tmp_path / "runs").run_simulation(
        make_netlist(tmp_path), {"parse_waveforms": True}
    )
    envelope = result.metadata["waveforms"][
        str(tmp_path / "runs" / "tb" / "tb_tran")
    ]
    assert envelope == {
        "parser": "virtuoso-bridge-psfascii",
        "dialect_verified": False,
        "empty": True,
        "data": {},
    }
    assert result.warnings == [
        "waveform parsing produced no signals "
        "(PSF dialect mismatch is unverified — G2/R1)"
    ]


def test_present_broken_without_force_has_no_capabilities(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "virtuoso_bridge", module("virtuoso_bridge", package=True)
    )
    monkeypatch.setattr(
        _companion.importlib.metadata,
        "version",
        lambda name: (_ for _ in ()).throw(
            _companion.importlib.metadata.PackageNotFoundError(name)
        ),
    )
    info = _companion.companion_info()
    assert info.available is True
    assert info.version == "unknown"
    assert info.verified is False
    assert info.capabilities == frozenset()


def test_present_broken_force_grants_only_callable_capabilities(monkeypatch):
    monkeypatch.setenv("PSB_COMPANION_FORCE", "1")
    install_stub_modules(
        monkeypatch,
        parser=lambda path: {},
        resolver=lambda: Path("/tmp/companion.env"),
    )
    monkeypatch.setattr(
        _companion.importlib.metadata,
        "version",
        lambda name: (_ for _ in ()).throw(
            _companion.importlib.metadata.PackageNotFoundError(name)
        ),
    )
    _companion.reset_cache()
    info = _companion.companion_info()
    assert info.available is True
    assert info.verified is False
    assert info.capabilities == frozenset({"env", "psf_ascii"})


def test_kill_switch_wins_over_present_stub(monkeypatch):
    install_stub_modules(monkeypatch, parser=lambda path: {"vout": [1.0]})
    monkeypatch.setenv("PSB_NO_COMPANION", "1")
    _companion.reset_cache()
    assert _companion.companion_info().available is False


def test_flags_are_truthy_only_for_literal_one(monkeypatch):
    install_stub_modules(monkeypatch, parser=lambda path: {})
    monkeypatch.setenv("PSB_NO_COMPANION", "true")
    _companion.reset_cache()
    assert _companion.companion_info().available is True


def test_env_file_uses_resolver_without_loading(monkeypatch):
    calls = []
    install_stub_modules(
        monkeypatch,
        resolver=lambda: calls.append("resolve") or Path("/tmp/companion.env"),
    )
    assert _companion.env_file() == Path("/tmp/companion.env")
    assert calls == ["resolve"]


def test_transport_normalizes_results_and_merges_download(tmp_path, monkeypatch):
    class StubSSHRunner:
        def __init__(self, host, user=None, timeout=0):
            assert (host, user, timeout) == ("compute", "alice", 9)

        def test_connection(self):
            return True

        def run_command(self, command, timeout=None):
            return SimpleNamespace(returncode=7, stdout="out", stderr="err")

        def upload_batch(self, files, timeout=None):
            return SimpleNamespace(returncode=3, stdout="", stderr="")

        def download(self, remote_path, local_path, recursive=False, timeout=None):
            assert recursive is True
            local_path.mkdir()
            (local_path / "remote.txt").write_text("remote")
            nested = local_path / "nested"
            nested.mkdir()
            (nested / "new.txt").write_text("new")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    install_stub_modules(monkeypatch, ssh_runner=StubSSHRunner)
    transport = _companion.CompanionTransport("compute", "alice", 9)
    assert transport.check() is True
    assert transport.run("command", 4) == (7, "out", "err")
    assert transport.put_batch([], 4) == 3
    target = tmp_path / "target"
    (target / "nested").mkdir(parents=True)
    (target / "keep.txt").write_text("keep")
    (target / "nested" / "old.txt").write_text("old")
    assert transport.get_dir("remote", target, 4) == 0
    assert (target / "keep.txt").read_text() == "keep"
    assert (target / "nested" / "old.txt").read_text() == "old"
    assert (target / "nested" / "new.txt").read_text() == "new"
    assert not (tmp_path / "target.dl").exists()


def test_companion_runner_success_records_synthetic_argv(tmp_path, monkeypatch):
    class FakeTransport:
        def __init__(self, host, user, timeout):
            pass

        def run(self, command, timeout):
            return 0, "", ""

        def put_batch(self, files, timeout):
            return 0

        def get_dir(self, remote_path, local_path, timeout):
            (local_path / "tb.log").write_text("analysis complete\n")
            return 0

    install_stub_modules(monkeypatch, ssh_runner=lambda *args, **kwargs: None)
    monkeypatch.setattr(_companion, "CompanionTransport", FakeTransport)
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs",
        remote=RemoteSpec(host="compute", user="alice"),
        run_id_factory=lambda: "fixed",
    ).run_simulation(make_netlist(tmp_path))
    assert result.status is ExecutionStatus.SUCCESS
    assert result.metadata["transport"] == "companion-sshrunner"
    assert result.metadata["remote_dir"] == ".primesim_bridge/runs/fixed"
    assert [entry[1] for entry in result.metadata["remote_cmds"]] == [
        "mkdir",
        "put_batch",
        "run",
        "get_dir",
    ]
    assert all(isinstance(entry, list) for entry in result.metadata["remote_cmds"])


def test_companion_constructor_exception_is_failure(tmp_path, monkeypatch):
    install_stub_modules(monkeypatch, ssh_runner=lambda *args, **kwargs: None)
    monkeypatch.setattr(
        _companion,
        "CompanionTransport",
        lambda *args: (_ for _ in ()).throw(ValueError("bad backend")),
    )
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs", remote=RemoteSpec(host="compute")
    ).run_simulation(make_netlist(tmp_path))
    assert result.status is ExecutionStatus.FAILURE
    assert result.errors == ["remote upload stage failed", "bad backend"]


@pytest.mark.parametrize(
    "failure_call,expected_stage",
    [
        ("mkdir", "remote upload stage failed"),
        ("put", "remote upload stage failed"),
        ("run", "remote ssh stage failed"),
        ("get", "remote download stage failed"),
    ],
)
def test_companion_interaction_exceptions_never_propagate(
    tmp_path, monkeypatch, failure_call, expected_stage
):
    class FailingTransport:
        def __init__(self, host, user, timeout):
            self.run_count = 0

        def run(self, command, timeout):
            self.run_count += 1
            stage = "mkdir" if self.run_count == 1 else "run"
            if stage == failure_call:
                raise RuntimeError(stage + " exploded")
            return 0, "", ""

        def put_batch(self, files, timeout):
            if failure_call == "put":
                raise RuntimeError("put exploded")
            return 0

        def get_dir(self, remote_path, local_path, timeout):
            if failure_call == "get":
                raise RuntimeError("get exploded")
            return 0

    install_stub_modules(monkeypatch, ssh_runner=lambda *args, **kwargs: None)
    monkeypatch.setattr(_companion, "CompanionTransport", FailingTransport)
    result = PrimeSimSimulator(
        work_dir=tmp_path / "runs", remote=RemoteSpec(host="compute")
    ).run_simulation(make_netlist(tmp_path))
    assert result.status is ExecutionStatus.FAILURE
    assert expected_stage in result.errors
    assert any("exploded" in error for error in result.errors)


@pytest.mark.skipif(not REAL_COMPANION, reason="real companion tier disabled")
def test_real_companion_pin_and_capabilities():
    info = _companion.companion_info()
    assert info.available is True
    assert info.version == "0.8.0"
    assert info.capabilities >= {"transport", "psf_ascii", "env"}


@pytest.mark.skipif(not REAL_COMPANION, reason="real companion tier disabled")
def test_real_companion_parses_spectre_shaped_psf_ascii(tmp_path):
    output = tmp_path / "psf"
    output.mkdir()
    (output / "tran.tran").write_text(
        "HEADER\n"
        '"PSFversion" "1.00"\n'
        '"type" "raw"\n'
        "TYPE\n"
        '"sweep" FLOAT DOUBLE\n'
        '"voltage" FLOAT DOUBLE\n'
        "TRACE\n"
        '"V(out)" "voltage"\n'
        "VALUE\n"
        '"sweep" 0.0\n'
        '"V(out)" 1.0\n'
        "END\n"
    )
    envelope = _companion.parse_psf_dir(output)
    assert envelope["empty"] is False
    assert len(envelope["data"]) >= 1


@pytest.mark.skipif(not LIVE_SSH, reason="companion live-SSH tier disabled")
def test_companion_live_round_trip_and_fake_e2e(tmp_path, fake_primesim_path):
    host = os.environ["PSB_TEST_SSH_HOST"]
    user = os.environ.get("PSB_TEST_SSH_USER")
    transport = _companion.CompanionTransport(host, user, 30)
    if not transport.check():
        pytest.skip("companion connection check failed")
    remote_dir = f".primesim_bridge/tests/{uuid.uuid4().hex}"
    assert transport.run(f"mkdir -p {remote_dir}", 30)[0] == 0
    source = tmp_path / "roundtrip.txt"
    source.write_text("roundtrip")
    assert transport.put_batch([(source, f"{remote_dir}/roundtrip.txt")], 30) == 0
    local = tmp_path / "download"
    local.mkdir()
    (local / "keep.txt").write_text("keep")
    assert transport.get_dir(remote_dir, local, 30) == 0
    assert (local / "keep.txt").read_text() == "keep"
    assert (local / "roundtrip.txt").read_text() == "roundtrip"
    fake_remote = f"{remote_dir}/fake_primesim.py"
    assert transport.put_batch([(fake_primesim_path, fake_remote)], 30) == 0
    assert transport.run(f"chmod +x {fake_remote}", 30)[0] == 0
    # The runner executes `cd <run_dir> && <binary> ...`, so a home-relative
    # binary path breaks after the cd (observed live: exit 127). Resolve the
    # remote HOME and pass an absolute path.
    rc, home_out, _ = transport.run("pwd", 30)
    assert rc == 0 and home_out.strip()
    fake_remote = f"{home_out.strip()}/{fake_remote}"
    if transport.run("command -v python3", 30)[0] != 0:
        pytest.skip("python3 is unavailable on remote host")
    deck = tmp_path / "live.sp"
    deck.write_text("* fake:measure=vout=1.0\n.end\n")
    result = PrimeSimSimulator(
        binary=fake_remote,
        work_dir=tmp_path / "runs",
        remote=RemoteSpec(host=host, user=user),
    ).run_simulation(deck)
    assert result.metadata["transport"] == "companion-sshrunner"
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data["vout"] == 1.0
