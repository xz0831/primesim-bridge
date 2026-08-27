from pathlib import Path

import pytest

from primesim_bridge.models import ExecutionStatus
from primesim_bridge.runner import PrimeSimSimulator


FIXTURES = Path(__file__).parent / "fixtures"


def copy_deck(tmp_path: Path, name: str) -> Path:
    source_dir = tmp_path / "source"
    source_dir.mkdir(exist_ok=True)
    destination = source_dir / name
    destination.write_text((FIXTURES / name).read_text())
    return destination


def simulator(tmp_path: Path, fake_primesim_path: Path, timeout: int = 3600):
    return PrimeSimSimulator(
        binary=str(fake_primesim_path),
        work_dir=tmp_path / "runs",
        timeout=timeout,
    )


def test_fake_primesim_is_executable(fake_primesim_path):
    assert fake_primesim_path.stat().st_mode & 0o111


def test_fake_success_parses_two_csv_measures(tmp_path, fake_primesim_path):
    deck = copy_deck(tmp_path, "fake_success.sp")
    result = simulator(tmp_path, fake_primesim_path).run_simulation(deck)
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data == {"delay": 1e-9, "gain": 2.0}
    assert result.metadata["transport"] == "local"
    assert "primesim_exit_dc_fail=1" in result.metadata["argv"]
    assert "primesim_measout=3" in result.metadata["argv"]


def test_fake_dc_failure_with_default_safety(tmp_path, fake_primesim_path):
    deck = copy_deck(tmp_path, "fake_dc_fail.sp")
    result = simulator(tmp_path, fake_primesim_path).run_simulation(deck)
    assert result.status is ExecutionStatus.FAILURE
    assert result.metadata["returncode"] == 34
    assert any("DC not converged" in error for error in result.errors)


def test_fake_dc_failure_suppression_changes_outcome(tmp_path, fake_primesim_path):
    deck = copy_deck(tmp_path, "fake_dc_fail.sp")
    result = simulator(tmp_path, fake_primesim_path).run_simulation(
        deck,
        {"extra_args": ["-aopt", "primesim_exit_dc_fail=0"]},
    )
    assert result.status is ExecutionStatus.SUCCESS
    assert result.metadata["returncode"] == 0
    assert any("DC not converged" in warning for warning in result.warnings)
    assert not result.errors


def test_fake_explicit_exit_uses_exit_table(tmp_path, fake_primesim_path):
    deck = copy_deck(tmp_path, "fake_exit.sp")
    result = simulator(tmp_path, fake_primesim_path).run_simulation(deck)
    assert result.status is ExecutionStatus.FAILURE
    assert "Unable to converge" in result.errors


def test_fake_classic_measure_matches_csv_data(tmp_path, fake_primesim_path):
    deck = copy_deck(tmp_path, "fake_success.sp")
    result = simulator(tmp_path, fake_primesim_path).run_simulation(
        deck,
        {"extra_args": ["-aopt", "primesim_measout=0"]},
    )
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data == {"delay": 1e-9, "gain": 2.0}
    assert Path(result.metadata["prefix"] + ".mt0").is_file()


def test_fake_timeout_before_artifacts_is_failure(tmp_path, fake_primesim_path):
    deck = copy_deck(tmp_path, "fake_sleep_first.sp")
    result = simulator(tmp_path, fake_primesim_path, timeout=1).run_simulation(deck)
    assert result.status is ExecutionStatus.FAILURE
    assert "timeout after 1s" in result.errors
    assert all(not paths for paths in result.metadata["output_files"].values())


def test_fake_timeout_after_artifacts_is_partial(tmp_path, fake_primesim_path):
    deck = copy_deck(tmp_path, "fake_sleep.sp")
    result = simulator(tmp_path, fake_primesim_path, timeout=1).run_simulation(deck)
    assert result.status is ExecutionStatus.PARTIAL
    assert "timeout after 1s" in result.errors
    assert result.metadata["output_files"]["log"]


def test_fake_include_directive_is_staged_and_applied(tmp_path, fake_primesim_path):
    deck = copy_deck(tmp_path, "fake_include.sp")
    include = tmp_path / "includes" / "measure.inc"
    include.parent.mkdir()
    include.write_text("* fake:measure=vout=1.0\n")
    result = simulator(tmp_path, fake_primesim_path).run_simulation(
        deck, {"include_files": [include]}
    )
    assert result.status is ExecutionStatus.SUCCESS
    assert result.data["vout"] == pytest.approx(1.0)
    assert result.metadata["argv"][-2:] == [
        "-afile",
        str(deck.parent / include.name),
    ]


def test_fake_multirow_measure_shape(tmp_path, fake_primesim_path):
    deck = copy_deck(tmp_path, "fake_rows.sp")
    result = simulator(tmp_path, fake_primesim_path).run_simulation(deck)
    assert result.data == {"vout": [1.0, 2.0, 3.0]}
    assert result.metadata["_rows"] == 3


def test_fake_unique_directory_rerun(tmp_path, fake_primesim_path):
    deck = copy_deck(tmp_path, "fake_success.sp")
    runner = simulator(tmp_path, fake_primesim_path)
    first = runner.run_simulation(deck)
    second = runner.run_simulation(deck)
    assert first.metadata["prefix"].endswith("/fake_success/fake_success")
    assert second.metadata["prefix"].endswith("/fake_success_2/fake_success")
