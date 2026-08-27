import os
import subprocess
import uuid

import pytest

from primesim_bridge import _companion
from primesim_bridge.models import ExecutionStatus
from primesim_bridge.runner import PrimeSimSimulator, RemoteSpec


LIVE_SSH = (
    os.environ.get("RUN_LIVE_SSH_TESTS") == "1"
    and bool(os.environ.get("PSB_TEST_SSH_HOST"))
)


@pytest.mark.skipif(not LIVE_SSH, reason="live-SSH tier disabled")
def test_openssh_live_fake_e2e(tmp_path, fake_primesim_path, monkeypatch):
    host = os.environ["PSB_TEST_SSH_HOST"]
    user = os.environ.get("PSB_TEST_SSH_USER")
    target = f"{user}@{host}" if user else host
    run_id = "live-" + uuid.uuid4().hex
    remote_dir = f".primesim_bridge/runs/{run_id}"
    subprocess.run(
        ["ssh", target, f"mkdir -p {remote_dir}"], check=True, timeout=30
    )
    subprocess.run(
        ["scp", str(fake_primesim_path), f"{target}:{remote_dir}/fake_primesim.py"],
        check=True,
        timeout=30,
    )
    subprocess.run(
        ["ssh", target, f"chmod +x {remote_dir}/fake_primesim.py"],
        check=True,
        timeout=30,
    )
    deck = tmp_path / "live.sp"
    deck.write_text("* fake:measure=vout=1.0\n.end\n")
    monkeypatch.setenv("PSB_NO_COMPANION", "1")
    _companion.reset_cache()
    result = PrimeSimSimulator(
        binary="./fake_primesim.py",
        work_dir=tmp_path / "runs",
        remote=RemoteSpec(host=host, user=user),
        timeout=30,
        run_id_factory=lambda: run_id,
    ).run_simulation(deck)
    assert result.metadata["transport"] == "openssh-subprocess"
    assert result.metadata["remote_dir"] == remote_dir
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data["vout"] == 1.0
