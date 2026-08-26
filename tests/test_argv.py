import pytest

from primesim_bridge.argv import build_primesim_argv, primesim_mode_args


def test_spice_mode_args_and_runlvl():
    assert primesim_mode_args() == ["-spice"]
    assert primesim_mode_args("spice", runlvl=6) == ["-spice", "-runlvl", "6"]


def test_pro_mode_args():
    assert primesim_mode_args("pro") == []
    assert primesim_mode_args("pro", mode="prohd") == ["-mode", "prohd"]


@pytest.mark.parametrize(
    "engine,kwargs",
    [
        ("spice", {"mode": "prohd"}),
        ("pro", {"runlvl": 4}),
        ("spice", {"runlvl": 0}),
        ("spice", {"runlvl": 7}),
        ("pro", {"mode": "junk"}),
    ],
)
def test_mode_args_reject_invalid_combinations(engine, kwargs):
    with pytest.raises(ValueError):
        primesim_mode_args(engine, **kwargs)


def test_build_argv_snapshot_with_substituted_binary_log_and_threads():
    argv = build_primesim_argv(
        binary="custom-primesim",
        engine_args=["-spice", "-runlvl", "5"],
        netlist="tb.sp",
        prefix="runs/tb",
        log_file="runs/custom.log",
        threads=8,
    )
    assert argv == [
        "custom-primesim",
        "-spice",
        "-runlvl",
        "5",
        "tb.sp",
        "-o",
        "runs/tb",
        "-log",
        "runs/custom.log",
        "-mt",
        "8",
        "-aopt",
        "primesim_exit_dc_fail=1",
        "-aopt",
        "primesim_measout=3",
    ]


def test_safety_is_suppressed_by_matching_two_element_aopt():
    argv = build_primesim_argv(
        netlist="tb.sp",
        prefix="tb",
        extra_args=[
            "-aopt",
            "primesim_exit_dc_fail=0",
            "-aopt",
            "primesim_measout=4",
        ],
    )
    assert argv.count("primesim_exit_dc_fail=1") == 0
    assert argv.count("primesim_measout=3") == 0
    assert argv[-4:] == [
        "-aopt",
        "primesim_exit_dc_fail=0",
        "-aopt",
        "primesim_measout=4",
    ]


@pytest.mark.parametrize("waveform_format", ["fsdb", "WDF", "fsdb wdf"])
def test_waveform_format_validation_accepts_documented_values(waveform_format):
    argv = build_primesim_argv(
        netlist="tb.sp", prefix="tb", waveform_format=waveform_format
    )
    assert argv[argv.index("-format") + 1] == waveform_format.lower()


def test_waveform_format_validation_rejects_junk():
    with pytest.raises(ValueError):
        build_primesim_argv(netlist="tb.sp", prefix="tb", waveform_format="raw")
