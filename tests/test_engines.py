from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from primesim_bridge import cli
from primesim_bridge.argv import build_primesim_argv, primesim_mode_args
from primesim_bridge.engines import EngineContext, get_profile
from primesim_bridge.models import (
    EXIT_CODES_HSPICE,
    ExecutionStatus,
    classify_exit_hspice,
)
from primesim_bridge.parsers import collect_outputs, parse_log_text


def context(**overrides):
    values = {
        "netlist": Path("tb.sp"),
        "prefix": Path("tb"),
        "binary": "simulator",
        "options": {"engine": "spice"},
        "extra_args": ("-case", "1"),
        "include_files": (Path("model.inc"),),
        "threads": 4,
        "waveform_format": "fsdb",
        "log_file": None,
        "safety": True,
    }
    values.update(overrides)
    return EngineContext(**values)


def test_engine_context_freezes_options_copy():
    options = {"engine": "spice"}
    ctx = context(options=options)
    options["engine"] = "pro"
    assert ctx.options["engine"] == "spice"
    with pytest.raises(TypeError):
        ctx.options["engine"] = "pro"


def test_profile_registry_aliases_and_unknown_value():
    assert get_profile("spice") is get_profile("pro")
    assert get_profile("spice").name == "primesim"
    assert get_profile("hspice").name == "hspice"
    with pytest.raises(ValueError, match="spice, pro, hspice"):
        get_profile("xa")


def test_primesim_argv_snapshot_and_pre_refactor_regression():
    ctx = context(log_file=Path("custom.log"))
    expected = build_primesim_argv(
        netlist=str(ctx.netlist),
        prefix=str(ctx.prefix),
        binary=ctx.binary,
        log_file=str(ctx.log_file),
        engine_args=primesim_mode_args("spice"),
        threads=ctx.threads,
        waveform_format=ctx.waveform_format,
        extra_args=[*ctx.extra_args, "-afile", "model.inc"],
    )
    assert get_profile("spice").build_argv(ctx) == expected
    assert expected == [
        "simulator",
        "-spice",
        "tb.sp",
        "-o",
        "tb",
        "-log",
        "custom.log",
        "-mt",
        "4",
        "-format",
        "fsdb",
        "-aopt",
        "primesim_exit_dc_fail=1",
        "-aopt",
        "primesim_measout=3",
        "-case",
        "1",
        "-afile",
        "model.inc",
    ]


def test_hspice_argv_snapshot():
    ctx = context(options={"engine": "hspice"})
    assert get_profile("hspice").build_argv(ctx) == [
        "simulator",
        "-i",
        "tb.sp",
        "-o",
        "tb",
        "-mt",
        "4",
        "-wavefmt",
        "fsdb",
        "-include_first",
        "psb_hspice_options.sp",
        "-include_last",
        "model.inc",
        "-case",
        "1",
    ]


def test_hspice_safety_can_be_suppressed_or_overridden():
    profile = get_profile("hspice")
    unsafe = context(options={"engine": "hspice"}, safety=False)
    assert "-include_first" not in profile.build_argv(unsafe)
    override = context(
        options={"engine": "hspice"},
        extra_args=("-include_first", "site.sp"),
    )
    argv = profile.build_argv(override)
    assert argv.count("-include_first") == 1
    assert argv[-2:] == ["-include_first", "site.sp"]
    assert profile.aux_files(override) == []


def test_hspice_aux_file_and_log_path_contract():
    profile = get_profile("hspice")
    ctx = context(options={"engine": "hspice"})
    assert profile.aux_files(ctx) == [
        (
            "psb_hspice_options.sp",
            "* injected by primesim-bridge\n"
            ".option measform=3\n.option measfail=1\n",
        )
    ]
    assert profile.log_path(ctx) == Path("tb.lis")


def test_hspice_exit_table_and_signal_normalization():
    assert set(EXIT_CODES_HSPICE) == {0, 1, 2, 3, 6, 8, 11, 15, 24, 28, 38, 99, 101}
    for code in (-11, 139):
        status, meaning = classify_exit_hspice(code)
        assert status is ExecutionStatus.FAILURE
        assert meaning == EXIT_CODES_HSPICE[11]


@pytest.mark.parametrize(
    "log,expected_status,expected_warning",
    [
        (
            {"errors": [], "warnings": [], "signatures": ["***** job concluded"]},
            ExecutionStatus.SUCCESS,
            [],
        ),
        (
            {
                "errors": ["**error** bad deck"],
                "warnings": [],
                "signatures": ["***** job concluded"],
            },
            ExecutionStatus.PARTIAL,
            [],
        ),
        (
            {"errors": [], "warnings": [], "signatures": []},
            ExecutionStatus.PARTIAL,
            ["exit 0 but no 'job concluded' banner in .lis"],
        ),
    ],
)
def test_hspice_banner_classification(log, expected_status, expected_warning):
    status, errors, warnings = get_profile("hspice").classify(
        0, log, True, context(options={"engine": "hspice"})
    )
    assert status is expected_status
    assert errors == []
    assert warnings == expected_warning


def test_hspice_none_returncode_is_failure():
    status, errors, warnings = get_profile("hspice").classify(
        None,
        {"errors": [], "warnings": [], "signatures": []},
        False,
        context(options={"engine": "hspice"}),
    )
    assert status is ExecutionStatus.FAILURE
    assert errors == [EXIT_CODES_HSPICE[1]]
    assert warnings == []


def test_profile_classification_never_promotes_dc_signature():
    log = {
        "errors": [],
        "warnings": ["DC not converged"],
        "signatures": ["DC not converged"],
    }
    status, errors, warnings = get_profile("spice").classify(
        0, log, True, context()
    )
    assert status is ExecutionStatus.SUCCESS
    assert errors == []
    assert warnings == []
    assert log["warnings"] == ["DC not converged"]


@pytest.mark.parametrize("waveform_format", ["none", "psfxl", "fsdb wdf"])
def test_hspice_rejects_unsupported_waveform_formats(waveform_format):
    with pytest.raises(ValueError, match="waveform_format must be one of"):
        get_profile("hspice").build_argv(
            context(options={"engine": "hspice"}, waveform_format=waveform_format)
        )


def test_hspice_rejects_prefix_with_dot():
    with pytest.raises(ValueError, match="truncates the output root"):
        get_profile("hspice").build_argv(
            context(options={"engine": "hspice"}, prefix=Path("tb.out"))
        )


def test_hspice_cli_dry_run_is_typed_and_does_not_write(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["run", "tb.sp", "--engine", "hspice", "--dry-run"]) == 0
    assert shlex.split(capsys.readouterr().out) == [
        "hspice",
        "-i",
        "tb.sp",
        "-o",
        "tb",
        "-include_first",
        "psb_hspice_options.sp",
    ]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "option,message",
    [
        (["--runlvl", "3"], "accuracy is netlist-only"),
        (["--mode", "prohd"], "accuracy is netlist-only"),
        (["--log", "custom.lis"], "hspice has no log-name flag"),
    ],
)
def test_hspice_cli_validation_exits_two(option, message, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["run", "tb.sp", "--engine", "hspice", "--dry-run", *option])
    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def test_cli_parse_aggregates_alter_measure_metadata(tmp_path, capsys):
    prefix = tmp_path / "tb"
    (tmp_path / "tb.mt0.csv").write_text("base\n1\n")
    (tmp_path / "tb.mt1.csv").write_text("alter\n2\n")
    assert cli.main(["parse", str(prefix)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"] == {"alter": 2.0, "base": 1.0}
    assert payload["metadata"]["alter_measures"] == {
        "tb.mt1.csv": {"alter": 2.0}
    }


def test_parser_extra_signature_is_case_insensitive_and_not_a_diagnostic():
    parsed = parse_log_text(
        ">info: ***** JOB CONCLUDED ******\n",
        extra_signatures=("***** job concluded",),
    )
    assert parsed == {
        "errors": [],
        "warnings": [],
        "signatures": ["***** job concluded"],
    }


def test_parser_classifies_hspice_artifact_families(tmp_path):
    prefix = tmp_path / "tb"
    for suffix in (
        ".lis",
        ".st0",
        ".printtr0",
        ".pa0",
        ".mpp0",
        ".ava.report",
        ".ms0",
        ".mt1.csv",
        ".csv",
    ):
        (tmp_path / f"tb{suffix}").write_text("")
    outputs = collect_outputs(prefix)
    names = {
        bucket: [path.name for path in paths] for bucket, paths in outputs.items()
    }
    assert names["log"] == ["tb.lis"]
    assert names["print"] == ["tb.pa0", "tb.printtr0"]
    assert names["measure"] == ["tb.csv", "tb.ms0", "tb.mt1.csv"]
    assert names["other"] == ["tb.ava.report", "tb.mpp0", "tb.st0"]
