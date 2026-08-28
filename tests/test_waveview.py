"""WaveView ACE-script generation (human waveform handoff).

Generation is fully offline-verified here; whether a real licensed WaveView
opens the script is the documented on-site check (one command).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from primesim_bridge import cli
from primesim_bridge.models import ExecutionStatus
from primesim_bridge.runner import PrimeSimSimulator
from primesim_bridge.waveview import (
    emit_ace_script,
    parse_probe_signals,
    write_waveview_script,
)


def test_parse_probe_signals_variants():
    deck = "\n".join(
        [
            "* comment",
            ".probe tran v(out) i(vdd) x1.v(net5)",
            ".PRINT AC vdb(out)",
            ".probe tran v(out)",  # duplicate — kept once
            ".probe dc alias=v(inp)",  # assignment keeps rhs
            ".probe tran $skip",  # $-token dropped
            ".tran 1n 1u",
            ".end",
        ]
    )
    assert parse_probe_signals(deck) == [
        "v(out)",
        "i(vdd)",
        "x1.v(net5)",
        "vdb(out)",
        "v(inp)",
    ]


def test_emit_ace_script_snapshot(tmp_path):
    text = emit_ace_script(
        [tmp_path / "runs" / "tb.fsdb"],
        ["v(out)", "i(vdd)"],
        tmp_path / "runs" / "tb.sx",
    )
    assert f"sx_open_sim_file_read {{{tmp_path}/runs/tb.fsdb}}" in text
    assert "sx_signal_add_mode single" in text
    assert "sx_display {v(out)} -auto_new_wv" in text
    assert "sx_display {i(vdd)} -auto_new_wv" in text
    assert f"sx_save_session {{{tmp_path}/runs/tb.sx}} -relpath" in text
    # session line must come after displays; script is plain lines
    assert text.index("sx_display") < text.index("sx_save_session")


def test_emit_ace_script_rejects_brace_in_literal(tmp_path):
    with pytest.raises(ValueError):
        emit_ace_script([tmp_path / "tb.fsdb"], ["v({bad})"], None)


def test_emit_ace_script_path_with_space(tmp_path):
    spaced = tmp_path / "run dir" / "tb.fsdb"
    text = emit_ace_script([spaced], [], None)
    assert f"sx_open_sim_file_read {{{spaced}}}" in text


def test_write_waveview_script_no_fsdb(tmp_path):
    prefix = tmp_path / "tb"
    tmp_path.mkdir(exist_ok=True)
    outcome = write_waveview_script(prefix, deck_text="")
    assert outcome["script"] is None
    assert outcome["warnings"] == ["no .fsdb waveform files found for this prefix"]


def test_write_waveview_script_full(tmp_path):
    prefix = tmp_path / "tb"
    (tmp_path / "tb.fsdb").write_text("")
    deck = ".probe tran v(out)\n.end\n"
    outcome = write_waveview_script(prefix, deck_text=deck)
    script = outcome["script"]
    assert script is not None and script.name == "tb_waves.tcl"
    body = script.read_text()
    assert "sx_display {v(out)}" in body
    assert outcome["session"] == tmp_path / "tb.sx"
    assert any(cmd.startswith("wv -k -ace_gui ") for cmd in outcome["launch"])
    assert any(" -y " in cmd for cmd in outcome["launch"])


def test_write_waveview_script_no_signals_warns(tmp_path):
    prefix = tmp_path / "tb"
    (tmp_path / "tb.fsdb").write_text("")
    outcome = write_waveview_script(prefix, deck_text=".end\n", save_session=False)
    assert outcome["session"] is None
    assert any("no signals selected" in w for w in outcome["warnings"])
    assert "sx_display" not in outcome["script"].read_text()


def test_cli_waveview(tmp_path, capsys):
    (tmp_path / "tb.fsdb").write_text("")
    deck = tmp_path / "tb.sp"
    deck.write_text(".probe tran v(out)\n.end\n")
    rc = cli.main(
        ["waveview", str(tmp_path / "tb"), "--deck", str(deck)]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["signals"] == ["v(out)"]
    assert payload["script"].endswith("tb_waves.tcl")


def test_cli_waveview_no_fsdb_exit_1(tmp_path, capsys):
    rc = cli.main(["waveview", str(tmp_path / "tb")])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["script"] is None


def test_run_option_generates_script_via_fake(tmp_path, fake_primesim_path):
    deck = tmp_path / "tb.sp"
    deck.write_text(
        "* fake:fsdb\n* fake:measure=vout=1.0\n.probe tran v(out)\n.end\n"
    )
    result = PrimeSimSimulator(
        binary=str(fake_primesim_path), work_dir=tmp_path / "runs"
    ).run_simulation(deck, {"waveview_script": True})
    assert result.status is ExecutionStatus.SUCCESS
    info = result.metadata["waveview"]
    assert info["script"] and Path(info["script"]).is_file()
    assert "sx_display {v(out)}" in Path(info["script"]).read_text()
    assert info["session"] and info["launch"]


def test_run_option_without_fsdb_warns(tmp_path, fake_primesim_path):
    deck = tmp_path / "tb.sp"
    deck.write_text("* fake:measure=vout=1.0\n.end\n")
    result = PrimeSimSimulator(
        binary=str(fake_primesim_path), work_dir=tmp_path / "runs"
    ).run_simulation(deck, {"waveview_script": True})
    assert result.metadata["waveview"]["script"] is None
    assert "no .fsdb waveform files found for this prefix" in result.warnings


def test_launch_hints_honor_wv_wrapper_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PSB_WV_BIN", "sx_sub")
    (tmp_path / "tb.fsdb").write_text("")
    outcome = write_waveview_script(tmp_path / "tb", deck_text=".probe tran v(out)\n.end\n")
    assert all(cmd.startswith("sx_sub ") for cmd in outcome["launch"])


def test_launch_hints_default_to_wv(tmp_path, monkeypatch):
    monkeypatch.delenv("PSB_WV_BIN", raising=False)
    (tmp_path / "tb.fsdb").write_text("")
    outcome = write_waveview_script(tmp_path / "tb", deck_text="")
    assert outcome["launch"][0].startswith("wv ")
