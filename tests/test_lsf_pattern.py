"""LSF / shared-filesystem site patterns (no SSH to compute nodes).

Models the company topology where PrimeSim is launched through a synchronous
LSF submission wrapper (`bsub -I`-style) on a shared filesystem: the wrapper
IS the `binary`, artifacts land on paths both sides see, and SSH remote mode
is never involved. Wrappers are generated per-test exactly the way a site
adapter would write them.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from primesim_bridge.models import ExecutionStatus
from primesim_bridge.runner import PrimeSimSimulator


def _write_wrapper(tmp_path: Path, fake_path: Path, *, mask_exit: bool) -> Path:
    """A site submission wrapper: transparent sync (bsub -I) or exit-masking (-wait)."""
    lines = ["#!/bin/sh"]
    if mask_exit:
        # Network-parallel `-wait` submissions always return 0 (guide p.575).
        lines += [f'"{fake_path}" "$@"', "exit 0"]
    else:
        lines += [f'exec "{fake_path}" "$@"']
    wrapper = tmp_path / ("bsub_wait.sh" if mask_exit else "bsub_sync.sh")
    wrapper.write_text("\n".join(lines) + "\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return wrapper


def _deck(tmp_path: Path, body: str) -> Path:
    deck = tmp_path / "tb.sp"
    deck.write_text(body)
    return deck


def test_sync_wrapper_binary_behaves_like_local(tmp_path, fake_primesim_path):
    wrapper = _write_wrapper(tmp_path, fake_primesim_path, mask_exit=False)
    deck = _deck(tmp_path, "* fake:measure=vout=1.0\n.end\n")
    result = PrimeSimSimulator(
        binary=str(wrapper), work_dir=tmp_path / "runs"
    ).run_simulation(deck)
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data == {"vout": 1.0}
    assert result.metadata["transport"] == "local"


def test_wait_wrapper_masks_failure_without_hint(tmp_path, fake_primesim_path):
    # dc_fail + injected safety → the fake exits 34, the wrapper masks it to 0.
    # Exit-code-first classification sees 0 + a promoted DC error → PARTIAL,
    # which is the honest-but-wrong-mode outcome the hint exists to fix.
    wrapper = _write_wrapper(tmp_path, fake_primesim_path, mask_exit=True)
    deck = _deck(tmp_path, "* fake:dc_fail\n.end\n")
    result = PrimeSimSimulator(
        binary=str(wrapper), work_dir=tmp_path / "runs"
    ).run_simulation(deck)
    assert result.status is ExecutionStatus.PARTIAL
    assert any("DC not converged" in e for e in result.errors)


def test_wait_wrapper_with_hint_classifies_from_log(tmp_path, fake_primesim_path):
    wrapper = _write_wrapper(tmp_path, fake_primesim_path, mask_exit=True)
    deck = _deck(tmp_path, "* fake:dc_fail\n.end\n")
    result = PrimeSimSimulator(
        binary=str(wrapper), work_dir=tmp_path / "runs"
    ).run_simulation(deck, {"is_parallel_wait": True})
    assert result.status is ExecutionStatus.FAILURE
    assert any("DC not converged" in e for e in result.errors)


def test_wait_wrapper_clean_run_with_hint_is_success(tmp_path, fake_primesim_path):
    wrapper = _write_wrapper(tmp_path, fake_primesim_path, mask_exit=True)
    deck = _deck(tmp_path, "* fake:measure=vout=2.5\n.end\n")
    result = PrimeSimSimulator(
        binary=str(wrapper), work_dir=tmp_path / "runs"
    ).run_simulation(deck, {"is_parallel_wait": True})
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data == {"vout": 2.5}


def test_psb_remote_host_wins_over_vb(monkeypatch, tmp_path):
    monkeypatch.setenv("VB_REMOTE_HOST", "eda-server")
    monkeypatch.setenv("PSB_REMOTE_HOST", "primesim-host")
    monkeypatch.setenv("PSB_REMOTE_USER", "psb-user")
    sim = PrimeSimSimulator.from_env(work_dir=tmp_path)
    assert sim.remote is not None
    assert sim.remote.host == "primesim-host"
    assert sim.remote.user == "psb-user"


def test_empty_psb_remote_host_forces_local_despite_vb(monkeypatch, tmp_path):
    # The company shield: vbl's .env exports VB_REMOTE_HOST for its own direct-TCP
    # setup files, but PrimeSim must stay local on the shared filesystem.
    monkeypatch.setenv("VB_REMOTE_HOST", "eda-server")
    monkeypatch.setenv("PSB_REMOTE_HOST", "")
    sim = PrimeSimSimulator.from_env(work_dir=tmp_path)
    assert sim.remote is None


def test_vb_remote_host_fallback_unchanged(monkeypatch, tmp_path):
    monkeypatch.delenv("PSB_REMOTE_HOST", raising=False)
    monkeypatch.delenv("PSB_REMOTE_USER", raising=False)
    monkeypatch.setenv("VB_REMOTE_HOST", "eda-server")
    monkeypatch.setenv("VB_REMOTE_USER", "vb-user")
    sim = PrimeSimSimulator.from_env(work_dir=tmp_path)
    assert sim.remote is not None
    assert sim.remote.host == "eda-server"
    assert sim.remote.user == "vb-user"
