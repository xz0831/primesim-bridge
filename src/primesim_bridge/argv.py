from __future__ import annotations


PRO_MODES = {"prohd", "promd", "proxd", "spicehd", "spicemd", "spicexd"}
WAVEFORM_FORMATS = {
    "fsdb",
    "wdf",
    "psf",
    "psfxl",
    "tr0",
    "psfascii",
    "none",
    "out",
    "fsdb wdf",
}
SAFETY_OPTIONS = ("primesim_exit_dc_fail=1", "primesim_measout=3")


def primesim_mode_args(
    engine: str = "spice", *, runlvl: int | None = None, mode: str | None = None
) -> list[str]:
    normalized_engine = engine.lower()
    if normalized_engine == "spice":
        if mode is not None:
            raise ValueError("mode is valid only with engine='pro'; use runlvl with spice")
        argv = ["-spice"]
        if runlvl is not None:
            if not 1 <= runlvl <= 6:
                raise ValueError("runlvl must be between 1 and 6 for engine='spice'")
            argv.extend(["-runlvl", str(runlvl)])
        return argv
    if normalized_engine == "pro":
        if runlvl is not None:
            raise ValueError("runlvl is valid only with engine='spice'; use mode with pro")
        argv = []
        if mode is not None:
            normalized_mode = mode.lower()
            if normalized_mode not in PRO_MODES:
                valid = ", ".join(sorted(PRO_MODES))
                raise ValueError(f"mode must be one of: {valid}")
            argv.extend(["-mode", normalized_mode])
        return argv
    raise ValueError("engine must be 'spice' or 'pro'")


def _extra_has_aopt(extra_args: list[str], option_name: str) -> bool:
    for index in range(len(extra_args) - 1):
        if extra_args[index] != "-aopt":
            continue
        if extra_args[index + 1].split("=", 1)[0] == option_name:
            return True
    return False


def build_primesim_argv(
    *,
    netlist: str,
    prefix: str,
    binary: str = "primesim",
    log_file: str | None = None,
    engine_args: list[str] | None = None,
    threads: int | None = None,
    waveform_format: str | None = None,
    extra_args: list[str] | None = None,
    inject_safety: bool = True,
) -> list[str]:
    selected_engine_args = list(engine_args or [])
    selected_extra_args = list(extra_args or [])
    argv = [binary, *selected_engine_args, netlist, "-o", prefix]
    if log_file is not None:
        argv.extend(["-log", log_file])
    if threads is not None:
        argv.extend(["-mt", str(threads)])
    if waveform_format is not None:
        normalized_format = waveform_format.lower()
        if normalized_format not in WAVEFORM_FORMATS:
            valid = ", ".join(sorted(WAVEFORM_FORMATS))
            raise ValueError(f"waveform_format must be one of: {valid}")
        argv.extend(["-format", normalized_format])
    if inject_safety:
        for safety_option in SAFETY_OPTIONS:
            option_name = safety_option.split("=", 1)[0]
            if not _extra_has_aopt(selected_extra_args, option_name):
                argv.extend(["-aopt", safety_option])
    argv.extend(selected_extra_args)
    return argv
