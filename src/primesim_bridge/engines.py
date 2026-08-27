from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol

from .argv import build_primesim_argv, primesim_mode_args
from .models import (
    ExecutionStatus,
    classify_exit,
    classify_exit_hspice,
)


HSPICE_WAVEFORM_FORMATS = {"fsdb", "wdf", "psf", "tr0"}
HSPICE_AUX_NAME = "psb_hspice_options.sp"
HSPICE_AUX_CONTENT = (
    "* injected by primesim-bridge\n"
    ".option measform=3\n"
    ".option measfail=1\n"
)


@dataclass(frozen=True)
class EngineContext:
    netlist: Path
    prefix: Path
    binary: str
    options: Mapping[str, Any]
    extra_args: tuple[str, ...]
    include_files: tuple[Path, ...]
    threads: Optional[int]
    waveform_format: Optional[str]
    log_file: Optional[Path]
    safety: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


class EngineProfile(Protocol):
    name: str
    default_binary: str
    env_binary_var: str
    log_signatures: tuple[str, ...]

    def build_argv(self, ctx: EngineContext) -> list[str]: ...

    def aux_files(self, ctx: EngineContext) -> list[tuple[str, str]]: ...

    def log_path(self, ctx: EngineContext) -> Path: ...

    def classify(
        self,
        returncode: Optional[int],
        log: Mapping[str, list],
        has_artifacts: bool,
        ctx: EngineContext,
    ) -> tuple[ExecutionStatus, list[str], list[str]]: ...


def _has_option(arguments: tuple[str, ...], option: str) -> bool:
    return option in arguments


@dataclass(frozen=True)
class PrimeSimProfile:
    name: str = "primesim"
    default_binary: str = "primesim"
    env_binary_var: str = "VB_PRIMESIM_BIN"
    log_signatures: tuple[str, ...] = ()

    def build_argv(self, ctx: EngineContext) -> list[str]:
        engine_args = primesim_mode_args(
            str(ctx.options.get("engine", "spice")),
            runlvl=ctx.options.get("runlvl"),
            mode=ctx.options.get("mode"),
        )
        extra_args = list(ctx.extra_args)
        for include_file in ctx.include_files:
            extra_args.extend(["-afile", str(include_file)])
        return build_primesim_argv(
            netlist=str(ctx.netlist),
            prefix=str(ctx.prefix),
            binary=ctx.binary,
            log_file=str(ctx.log_file) if ctx.log_file is not None else None,
            engine_args=engine_args,
            threads=ctx.threads,
            waveform_format=ctx.waveform_format,
            extra_args=extra_args,
            inject_safety=ctx.safety,
        )

    def aux_files(self, ctx: EngineContext) -> list[tuple[str, str]]:
        return []

    def log_path(self, ctx: EngineContext) -> Path:
        if ctx.log_file is not None:
            return ctx.log_file
        return Path(str(ctx.prefix) + ".log")

    def classify(
        self,
        returncode: Optional[int],
        log: Mapping[str, list],
        has_artifacts: bool,
        ctx: EngineContext,
    ) -> tuple[ExecutionStatus, list[str], list[str]]:
        del has_artifacts, ctx
        extra_errors: list[str] = []
        effective_returncode = returncode if returncode is not None else 1
        exit_status, exit_error = classify_exit(effective_returncode)
        if exit_status is ExecutionStatus.FAILURE:
            if exit_error is not None:
                extra_errors.append(exit_error)
            status = ExecutionStatus.FAILURE
        elif log["errors"]:
            status = ExecutionStatus.PARTIAL
        else:
            status = ExecutionStatus.SUCCESS
        return status, extra_errors, []


@dataclass(frozen=True)
class HspiceProfile:
    name: str = "hspice"
    default_binary: str = "hspice"
    env_binary_var: str = "VB_HSPICE_BIN"
    log_signatures: tuple[str, ...] = ("***** job concluded",)

    @staticmethod
    def _validate(ctx: EngineContext) -> None:
        if ctx.options.get("runlvl") is not None or ctx.options.get("mode") is not None:
            raise ValueError(
                "accuracy is netlist-only for hspice: use .option runlvl"
            )
        if ctx.log_file is not None:
            raise ValueError(
                "hspice has no log-name flag — the listing is <prefix>.lis"
            )
        if "." in ctx.prefix.name:
            raise ValueError(
                "hspice truncates the output root at the last period — "
                "choose a dot-free prefix"
            )

    def build_argv(self, ctx: EngineContext) -> list[str]:
        self._validate(ctx)
        argv = [ctx.binary, "-i", str(ctx.netlist), "-o", str(ctx.prefix)]
        if ctx.threads is not None:
            argv.extend(["-mt", str(ctx.threads)])
        if ctx.waveform_format is not None:
            normalized_format = ctx.waveform_format.lower()
            if normalized_format not in HSPICE_WAVEFORM_FORMATS:
                valid = ", ".join(sorted(HSPICE_WAVEFORM_FORMATS))
                raise ValueError(f"waveform_format must be one of: {valid}")
            argv.extend(["-wavefmt", normalized_format])
        if ctx.safety and not _has_option(ctx.extra_args, "-include_first"):
            aux_ref = (
                Path(HSPICE_AUX_NAME)
                if ctx.options.get("dry_run") is True
                else ctx.prefix.parent / HSPICE_AUX_NAME
            )
            argv.extend(["-include_first", str(aux_ref)])
        for include_file in ctx.include_files:
            argv.extend(["-include_last", str(include_file)])
        argv.extend(ctx.extra_args)
        return argv

    def aux_files(self, ctx: EngineContext) -> list[tuple[str, str]]:
        if not ctx.safety or _has_option(ctx.extra_args, "-include_first"):
            return []
        return [(HSPICE_AUX_NAME, HSPICE_AUX_CONTENT)]

    def log_path(self, ctx: EngineContext) -> Path:
        return Path(str(ctx.prefix) + ".lis")

    def classify(
        self,
        returncode: Optional[int],
        log: Mapping[str, list],
        has_artifacts: bool,
        ctx: EngineContext,
    ) -> tuple[ExecutionStatus, list[str], list[str]]:
        del has_artifacts, ctx
        effective_returncode = returncode if returncode is not None else 1
        exit_status, exit_error = classify_exit_hspice(effective_returncode)
        if exit_status is ExecutionStatus.FAILURE:
            return (
                ExecutionStatus.FAILURE,
                [exit_error] if exit_error is not None else [],
                [],
            )
        if self.log_signatures[0] not in log["signatures"]:
            return (
                ExecutionStatus.PARTIAL,
                [],
                ["exit 0 but no 'job concluded' banner in .lis"],
            )
        if log["errors"]:
            return ExecutionStatus.PARTIAL, [], []
        return ExecutionStatus.SUCCESS, [], []


PRIMESIM_PROFILE = PrimeSimProfile()
HSPICE_PROFILE = HspiceProfile()
ENGINE_PROFILES: Mapping[str, EngineProfile] = MappingProxyType(
    {
        "spice": PRIMESIM_PROFILE,
        "pro": PRIMESIM_PROFILE,
        "hspice": HSPICE_PROFILE,
    }
)


def get_profile(engine_option: str) -> EngineProfile:
    normalized = engine_option.lower()
    try:
        return ENGINE_PROFILES[normalized]
    except KeyError as exc:
        raise ValueError("engine must be one of: spice, pro, hspice") from exc
