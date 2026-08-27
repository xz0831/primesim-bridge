from __future__ import annotations

import re
from pathlib import Path

import pytest

from primesim_bridge.engines import EngineContext, get_profile
from primesim_bridge.models import ExecutionStatus


def context(**overrides):
    values = {
        "netlist": Path("tb.sp"),
        "prefix": Path("tb"),
        "binary": "xa-custom",
        "options": {"engine": "xa", "dry_run": True},
        "extra_args": (),
        "include_files": (),
        "threads": None,
        "waveform_format": None,
        "log_file": None,
        "safety": True,
    }
    values.update(overrides)
    return EngineContext(**values)


def test_xa_profile_registry_and_contract():
    profile = get_profile("xa")
    ctx = context()
    assert profile.name == "xa"
    assert profile.default_binary == "xa"
    assert profile.env_binary_var == "VB_XA_BIN"
    assert profile.log_signatures == ("Total Wall Time =",)
    assert profile.log_path(ctx) == Path("tb.log")


def test_xa_default_argv_snapshot_has_positional_netlist_before_output():
    argv = get_profile("xa").build_argv(context())
    assert argv == ["xa-custom", "tb.sp", "-o", "tb", "-c", "psb_xa.cmd"]
    assert argv.index("tb.sp") < argv.index("-o")
    assert "-hspice" not in argv
    assert "-format" not in argv


@pytest.mark.parametrize("dialect", [None, "hspice"])
def test_xa_default_and_explicit_hspice_dialect_emit_no_flag(dialect):
    argv = get_profile("xa").build_argv(
        context(options={"engine": "xa", "dry_run": True, "dialect": dialect})
    )
    assert argv[:2] == ["xa-custom", "tb.sp"]
    assert "-hspice" not in argv


def test_xa_spectre_argv_threads_waveform_and_extra_args():
    ctx = context(
        options={"engine": "xa", "dry_run": True, "dialect": "spectre"},
        threads=8,
        waveform_format="PSF",
        extra_args=("-gz",),
    )
    assert get_profile("xa").build_argv(ctx) == [
        "xa-custom",
        "-spectre",
        "tb.sp",
        "-o",
        "tb",
        "-mt",
        "8",
        "-wavefmt",
        "psf",
        "-c",
        "psb_xa.cmd",
        "-gz",
    ]


def test_xa_safety_can_be_disabled_or_overridden_by_caller_command_file():
    profile = get_profile("xa")
    unsafe = context(safety=False)
    assert "-c" not in profile.build_argv(unsafe)
    assert profile.aux_files(unsafe) == []

    override = context(extra_args=("-c", "site.cmd"))
    argv = profile.build_argv(override)
    assert argv.count("-c") == 1
    assert argv[-2:] == ["-c", "site.cmd"]
    assert profile.aux_files(override) == []


def test_xa_aux_file_contract_and_non_dry_reference(tmp_path):
    profile = get_profile("xa")
    ctx = context(
        prefix=tmp_path / "run" / "tb",
        options={"engine": "xa"},
    )
    assert profile.aux_files(ctx) == [
        (
            "psb_xa.cmd",
            "# injected by primesim-bridge\n"
            "set_meas_option -format hspice\n",
        )
    ]
    argv = profile.build_argv(ctx)
    assert argv[argv.index("-c") + 1] == str(tmp_path / "run" / "psb_xa.cmd")


@pytest.mark.parametrize("waveform_format", ["none", "vpd", "fsdb wdf"])
def test_xa_rejects_unsupported_waveform_formats(waveform_format):
    with pytest.raises(ValueError, match="waveform_format must be one of"):
        get_profile("xa").build_argv(context(waveform_format=waveform_format))


def test_xa_rejects_invalid_dialect_without_emitting_format_alias():
    with pytest.raises(
        ValueError, match="dialect must be one of: hspice, spectre, eldo"
    ):
        get_profile("xa").build_argv(
            context(options={"engine": "xa", "dialect": "format"})
        )


@pytest.mark.parametrize("option", [{"runlvl": 3}, {"mode": "prohd"}])
def test_xa_rejects_runlvl_and_mode_with_exact_message(option):
    with pytest.raises(
        ValueError,
        match=re.escape(
            "xa accuracy is set with set_sim_level / -sim_mode, not runlvl/mode"
        ),
    ):
        get_profile("xa").build_argv(
            context(options={"engine": "xa", **option})
        )


def test_xa_rejects_log_file_with_exact_message():
    with pytest.raises(ValueError, match="^xa log is always <prefix>\\.log$"):
        get_profile("xa").build_argv(context(log_file=Path("custom.log")))


def test_xa_rejects_include_files_directly_with_exact_message():
    message = (
        "xa has no CLI include-append mechanism (-I only adds a search path) — "
        "use .include/.lib inside the netlist"
    )
    with pytest.raises(ValueError, match=re.escape(message)):
        get_profile("xa").build_argv(
            context(include_files=(Path("model.inc"),))
        )


def test_xa_rejects_existing_directory_prefix_with_exact_message(tmp_path):
    prefix = tmp_path / "existing"
    prefix.mkdir()
    message = (
        "xa -o treats an existing directory as the output directory; choose a "
        "prefix that is not a directory"
    )
    with pytest.raises(ValueError, match=re.escape(message)):
        get_profile("xa").build_argv(context(prefix=prefix))


@pytest.mark.parametrize("engine", ["spice", "hspice"])
def test_dialect_is_rejected_on_non_xa_engines_with_exact_message(engine):
    with pytest.raises(ValueError, match="^dialect is only valid for engine xa$"):
        get_profile(engine).build_argv(
            context(options={"engine": engine, "dialect": "spectre"})
        )


@pytest.mark.parametrize("has_errors", [False, True])
@pytest.mark.parametrize("has_marker", [False, True])
def test_xa_exit_zero_classification_matrix(has_errors, has_marker):
    log = {
        "errors": ["Error: bad deck"] if has_errors else [],
        "warnings": [],
        "signatures": ["Total Wall Time ="] if has_marker else [],
    }
    status, errors, warnings = get_profile("xa").classify(
        0, log, True, context(threads=2)
    )
    assert status is (
        ExecutionStatus.PARTIAL if has_errors else ExecutionStatus.SUCCESS
    )
    assert errors == []
    assert warnings == (
        []
        if has_marker
        else [
            "exit 0 but no 'Total Wall Time' end-of-log marker "
            "(undocumented success proxy)"
        ]
    )


def test_xa_single_core_exit_zero_without_marker_has_no_proxy_warning():
    status, errors, warnings = get_profile("xa").classify(
        0,
        {"errors": [], "warnings": [], "signatures": []},
        False,
        context(threads=None),
    )
    assert status is ExecutionStatus.SUCCESS
    assert errors == []
    assert warnings == []


@pytest.mark.parametrize(
    "returncode,expected_error",
    [
        (2, "exit code 2"),
        (-9, "exit code -9"),
        (None, "no exit code (process did not complete)"),
    ],
)
def test_xa_undocumented_exit_codes_are_plain_failures(returncode, expected_error):
    status, errors, warnings = get_profile("xa").classify(
        returncode,
        {"errors": [], "warnings": [], "signatures": []},
        False,
        context(),
    )
    assert status is ExecutionStatus.FAILURE
    assert errors == [expected_error]
    assert warnings == []
