from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel

from . import _companion
from .engines import EngineContext, EngineProfile, get_profile
from .models import ExecutionStatus, SimulationResult
from .parsers import (
    collect_outputs,
    parse_log,
    parse_measure_ascii,
    parse_measure_csv,
    parse_op_ascii,
)


class RemoteSpec(BaseModel):
    host: str
    user: Optional[str] = None


def _exec(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _wrap_env_setup(
    argv: list[str], env_setup: str | None, env_setup_shell: str
) -> list[str]:
    if env_setup is None:
        return argv
    if env_setup_shell == "sh":
        command = ". " + shlex.quote(env_setup) + " && exec " + shlex.join(argv)
        return ["sh", "-c", command]
    command = "source " + shlex.quote(env_setup) + "; exec " + shlex.join(argv)
    return ["csh", "-fc", command]


def _merge_unique(target: list[str], additions: list[str]) -> None:
    for addition in additions:
        if addition not in target:
            target.append(addition)


def _has_aopt(extra_args: tuple[str, ...], option_name: str) -> bool:
    for index in range(len(extra_args) - 1):
        if extra_args[index] == "-aopt":
            if extra_args[index + 1].split("=", 1)[0] == option_name:
                return True
    return False


class PrimeSimSimulator:
    def __init__(
        self,
        *,
        work_dir: Path,
        binary: Optional[str] = None,
        env_setup: str | None = None,
        env_setup_shell: str = "sh",
        timeout: int = 3600,
        remote: RemoteSpec | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if env_setup_shell not in {"sh", "csh"}:
            raise ValueError("env_setup_shell must be 'sh' or 'csh'")
        self.binary = binary
        self.work_dir = Path(work_dir)
        self.env_setup = env_setup
        self.env_setup_shell = env_setup_shell
        self.timeout = timeout
        self.remote = remote
        self._run_counter = 0
        self.run_id_factory = run_id_factory or self._next_run_id
        self._binary_from_primesim_env = False

    def _next_run_id(self) -> str:
        self._run_counter += 1
        return f"run{self._run_counter}"

    @classmethod
    def from_env(cls, **overrides: Any) -> "PrimeSimSimulator":
        values: Dict[str, Any] = {}
        binary = os.environ.get("VB_PRIMESIM_BIN")
        setup = os.environ.get("VB_SYNOPSYS_SETUP")
        setup_shell = os.environ.get("VB_SYNOPSYS_SETUP_SHELL")
        # PSB_REMOTE_HOST shields against VB_REMOTE_HOST leaking from a shared
        # virtuoso-bridge .env (LSF sites use direct-TCP Virtuoso bridging with no
        # SSH to compute nodes — an exported VB_REMOTE_HOST there would wrongly
        # push PrimeSim runs into SSH remote mode). Presence wins over value: an
        # EMPTY PSB_REMOTE_HOST explicitly forces local mode.
        if "PSB_REMOTE_HOST" in os.environ:
            remote_host = os.environ["PSB_REMOTE_HOST"]
        else:
            remote_host = os.environ.get("VB_REMOTE_HOST", "")
        remote_user = os.environ.get("PSB_REMOTE_USER") or os.environ.get("VB_REMOTE_USER")
        if binary:
            values["binary"] = binary
        if setup:
            values["env_setup"] = setup
        if setup_shell:
            values["env_setup_shell"] = setup_shell
        if remote_host and remote_host.lower() != "localhost":
            values["remote"] = RemoteSpec(host=remote_host, user=remote_user or None)
        binary_from_primesim_env = bool(binary) and "binary" not in overrides
        values.update(overrides)
        simulator = cls(**values)
        simulator._binary_from_primesim_env = binary_from_primesim_env
        return simulator

    def _default_prefix(self, netlist: Path) -> Path:
        stem = netlist.stem
        run_dir = self.work_dir / stem
        if run_dir.exists():
            suffix = 2
            while (self.work_dir / f"{stem}_{suffix}").exists():
                suffix += 1
            run_dir = self.work_dir / f"{stem}_{suffix}"
        return run_dir / stem

    def _parse_artifacts(
        self,
        prefix: Path,
        explicit_log: Path | None = None,
        extra_signatures: tuple[str, ...] = (),
    ) -> tuple[
        dict[str, Any],
        list[str],
        list[str],
        list[str],
        dict[str, list[Path]],
        dict[str, Any],
    ]:
        outputs = collect_outputs(prefix)
        if explicit_log is not None and explicit_log.is_file():
            if explicit_log not in outputs["log"]:
                outputs["log"].append(explicit_log)
                outputs["log"].sort()
        data: dict[str, Any] = {}
        errors: list[str] = []
        warnings: list[str] = []
        signatures: list[str] = []
        alter_metadata: dict[str, Any] = {}
        indexed_measures: dict[Path, int] = {}
        for measure_path in outputs["measure"]:
            matches = list(
                re.finditer(
                    r"\.(?:mt|ma|ms|md|mc)(\d+)", measure_path.name.lower()
                )
            )
            if matches:
                indexed_measures[measure_path] = int(matches[-1].group(1))
        minimum_index = min(indexed_measures.values(), default=None)
        for measure_path in outputs["measure"]:
            uncompressed_name = measure_path.name.lower()
            if uncompressed_name.endswith(".gzip"):
                uncompressed_name = uncompressed_name[:-5]
            elif uncompressed_name.endswith(".gz"):
                uncompressed_name = uncompressed_name[:-3]
            parsed = (
                parse_measure_csv(measure_path)
                if uncompressed_name.endswith(".csv")
                else parse_measure_ascii(measure_path)
            )
            _merge_unique(warnings, parsed.pop("_warnings", []))
            alter_rows = parsed.pop("_rows", None)
            measure_index = indexed_measures.get(measure_path)
            if (
                minimum_index is not None
                and measure_index is not None
                and measure_index > minimum_index
            ):
                alter_metadata.setdefault("alter_measures", {})[
                    measure_path.name
                ] = dict(parsed)
                if alter_rows is not None:
                    alter_metadata.setdefault("alter_rows", {})[
                        measure_path.name
                    ] = alter_rows
            if alter_rows is not None:
                row_count = alter_rows
                data["_rows"] = max(int(data.get("_rows", 0)), int(row_count))
            data.update(parsed)
        for op_path in outputs["op"]:
            data.update(parse_op_ascii(op_path))
        for log_path in outputs["log"]:
            parsed_log = parse_log(log_path, extra_signatures=extra_signatures)
            _merge_unique(errors, parsed_log["errors"])
            _merge_unique(warnings, parsed_log["warnings"])
            _merge_unique(signatures, parsed_log["signatures"])
        return data, errors, warnings, signatures, outputs, alter_metadata

    @staticmethod
    def _serialized_outputs(outputs: dict[str, list[Path]]) -> dict[str, list[str]]:
        return {
            bucket: [str(path) for path in paths] for bucket, paths in outputs.items()
        }

    def _finish_result(
        self,
        *,
        prefix: Path,
        argv: list[str],
        returncode: int | None,
        is_parallel_wait: bool,
        engine: str,
        profile: EngineProfile,
        engine_ctx: EngineContext,
        explicit_log: Path | None = None,
        remote_cmds: list[list[str]] | None = None,
        remote_dir: str | None = None,
        transport: str,
        timeout_error: str | None = None,
        forced_error: str | None = None,
        exception_error: str | None = None,
    ) -> SimulationResult:
        data, errors, warnings, signatures, outputs, alter_metadata = self._parse_artifacts(
            prefix, explicit_log, profile.log_signatures
        )
        metadata: dict[str, Any] = {
            "argv": argv,
            "returncode": returncode,
            "output_files": self._serialized_outputs(outputs),
            "prefix": str(prefix),
            "log_signatures": signatures,
            "transport": transport,
            "engine": engine,
        }
        if remote_cmds is not None:
            metadata["remote_cmds"] = remote_cmds
        if remote_dir is not None:
            metadata["remote_dir"] = remote_dir
        metadata.update(alter_metadata)
        if "_rows" in data:
            metadata["_rows"] = data.pop("_rows")

        dc_signature = "DC not converged"
        dc_safety_injected = engine_ctx.safety and not _has_aopt(
            engine_ctx.extra_args, "primesim_exit_dc_fail"
        )
        if (
            profile.name == "primesim"
            and dc_safety_injected
            and dc_signature in signatures
        ):
            promoted = [
                item for item in warnings if dc_signature.lower() in item.lower()
            ]
            warnings = [
                item for item in warnings if dc_signature.lower() not in item.lower()
            ]
            _merge_unique(errors, promoted or [dc_signature])

        artifact_count = sum(len(paths) for paths in outputs.values())
        if timeout_error is not None:
            _merge_unique(errors, [timeout_error])
            status = ExecutionStatus.PARTIAL if artifact_count else ExecutionStatus.FAILURE
        elif forced_error is not None:
            _merge_unique(errors, [forced_error])
            if exception_error is not None:
                _merge_unique(errors, [exception_error])
            status = ExecutionStatus.FAILURE
        elif is_parallel_wait:
            status = ExecutionStatus.FAILURE if errors else ExecutionStatus.SUCCESS
        else:
            log = {
                "errors": errors,
                "warnings": warnings,
                "signatures": signatures,
            }
            status, extra_errors, extra_warnings = profile.classify(
                returncode, log, bool(artifact_count), engine_ctx
            )
            _merge_unique(errors, extra_errors)
            _merge_unique(warnings, extra_warnings)
        return SimulationResult(
            status=status,
            data=data,
            errors=errors,
            warnings=warnings,
            metadata=metadata,
        )

    @staticmethod
    def _postprocess_waveforms(
        result: SimulationResult, prefix: Path, requested: bool
    ) -> SimulationResult:
        if not requested:
            return result
        try:
            info = _companion.companion_info()
            if "psf_ascii" not in info.capabilities:
                _merge_unique(
                    result.warnings,
                    ["waveform parsing requested but companion package not available"],
                )
                return result
            candidates = sorted(
                directory
                for directory in prefix.parent.iterdir()
                if directory.is_dir()
                and directory.name.startswith(prefix.name + "_")
            )
            waveforms: dict[str, Any] = {}
            for directory in candidates:
                envelope = _companion.parse_psf_dir(directory)
                waveforms[str(directory)] = envelope
                if envelope["empty"]:
                    _merge_unique(
                        result.warnings,
                        [
                            "waveform parsing produced no signals "
                            "(PSF dialect mismatch is unverified — G2/R1)"
                        ],
                    )
            result.metadata["waveforms"] = waveforms
        except Exception as exc:
            _merge_unique(result.warnings, [f"waveform parsing failed: {exc}"])
        return result

    @staticmethod
    def _postprocess_waveview(
        result: SimulationResult, prefix: Path, netlist: Path, requested: bool
    ) -> SimulationResult:
        """Generate the WaveView ACE handoff script (human waveform handoff)."""
        if not requested:
            return result
        try:
            from primesim_bridge.waveview import write_waveview_script

            try:
                deck_text = netlist.read_text(errors="replace")
            except OSError:
                deck_text = ""
            outcome = write_waveview_script(prefix, deck_text=deck_text)
            _merge_unique(result.warnings, outcome["warnings"])
            result.metadata["waveview"] = {
                "script": str(outcome["script"]) if outcome["script"] else None,
                "session": str(outcome["session"]) if outcome["session"] else None,
                "signals": outcome["signals"],
                "launch": outcome["launch"],
            }
        except Exception as exc:
            _merge_unique(result.warnings, [f"waveview script generation failed: {exc}"])
        return result

    def run_simulation(
        self, netlist: Path, options: dict[str, Any] | None = None
    ) -> SimulationResult:
        selected = dict(options or {})
        netlist = Path(netlist)
        supplied_prefix = selected.get("prefix")
        prefix = Path(supplied_prefix) if supplied_prefix is not None else self._default_prefix(netlist)
        run_dir = prefix.parent
        run_dir.mkdir(parents=True, exist_ok=True)

        profile = get_profile(str(selected.get("engine", "spice")))
        binary = (
            self.binary
            if self.binary is not None
            and not (
                self._binary_from_primesim_env and profile.name != "primesim"
            )
            else os.environ.get(profile.env_binary_var, profile.default_binary)
        )
        extra_args = list(selected.get("extra_args") or [])
        include_files = [Path(path) for path in selected.get("include_files") or []]
        is_parallel_wait = bool(selected.get("is_parallel_wait", False))
        log_value = selected.get("log_file")
        waveform_format = selected.get("waveform_format")
        parse_waveforms = selected.get("parse_waveforms") is True
        waveview_script = selected.get("waveview_script") is True
        safety = not selected.get("no_safety", False)

        local_netlist = netlist.absolute()
        local_prefix = prefix.absolute()

        def context(
            *,
            context_netlist: Path,
            context_prefix: Path,
            context_includes: list[Path],
            context_log: Path | None,
        ) -> EngineContext:
            return EngineContext(
                netlist=context_netlist,
                prefix=context_prefix,
                binary=binary,
                options=selected,
                extra_args=tuple(extra_args),
                include_files=tuple(context_includes),
                threads=selected.get("threads"),
                waveform_format=waveform_format,
                log_file=context_log,
                safety=safety,
            )

        def finish_result(**kwargs: Any) -> SimulationResult:
            result = self._finish_result(**kwargs)
            result = self._postprocess_waveforms(result, local_prefix, parse_waveforms)
            return self._postprocess_waveview(
                result, local_prefix, local_netlist, waveview_script
            )

        if self.remote is None:
            include_destinations: list[Path] = []
            for include_file in include_files:
                source = include_file.absolute()
                destination = local_netlist.parent / include_file.name
                if include_file.resolve() != destination.resolve():
                    shutil.copy2(source, destination)
                include_destinations.append(destination)
            local_log = (
                Path(log_value).absolute() if log_value is not None else None
            )
            local_ctx = context(
                context_netlist=local_netlist,
                context_prefix=local_prefix,
                context_includes=include_destinations,
                context_log=local_log,
            )
            argv = profile.build_argv(local_ctx)
            aux_paths: list[Path] = []
            for name, content in profile.aux_files(local_ctx):
                aux_path = local_prefix.parent / name
                aux_path.write_text(content)
                aux_paths.append(aux_path)
            explicit_log = profile.log_path(local_ctx)
            if self.env_setup is None and shutil.which(binary) is None:
                return finish_result(
                    prefix=local_prefix,
                    argv=argv,
                    returncode=None,
                    is_parallel_wait=is_parallel_wait,
                    engine=profile.name,
                    profile=profile,
                    engine_ctx=local_ctx,
                    explicit_log=explicit_log,
                    transport="local",
                    forced_error=f"{profile.name} executable not found: {binary}",
                )
            command = _wrap_env_setup(argv, self.env_setup, self.env_setup_shell)
            try:
                completed = _exec(command, timeout=self.timeout)
            except FileNotFoundError:
                return finish_result(
                    prefix=local_prefix,
                    argv=argv,
                    returncode=None,
                    is_parallel_wait=is_parallel_wait,
                    engine=profile.name,
                    profile=profile,
                    engine_ctx=local_ctx,
                    explicit_log=explicit_log,
                    transport="local",
                    forced_error=f"{profile.name} executable not found: {binary}",
                )
            except subprocess.TimeoutExpired:
                return finish_result(
                    prefix=local_prefix,
                    argv=argv,
                    returncode=None,
                    is_parallel_wait=is_parallel_wait,
                    engine=profile.name,
                    profile=profile,
                    engine_ctx=local_ctx,
                    explicit_log=explicit_log,
                    transport="local",
                    timeout_error=f"timeout after {self.timeout}s",
                )
            return finish_result(
                prefix=local_prefix,
                argv=argv,
                returncode=completed.returncode,
                is_parallel_wait=is_parallel_wait,
                engine=profile.name,
                profile=profile,
                engine_ctx=local_ctx,
                explicit_log=explicit_log,
                transport="local",
            )

        run_id = self.run_id_factory()
        remote_dir = f".primesim_bridge/runs/{run_id}"
        target = (
            f"{self.remote.user}@{self.remote.host}"
            if self.remote.user
            else self.remote.host
        )
        remote_log = Path(log_value).name if log_value is not None else None
        local_log = local_prefix.parent / remote_log if remote_log else None
        local_ctx = context(
            context_netlist=local_netlist,
            context_prefix=local_prefix,
            context_includes=[path.absolute() for path in include_files],
            context_log=local_log,
        )
        argv_ctx = context(
            context_netlist=Path(netlist.name),
            context_prefix=Path(prefix.name),
            context_includes=[Path(path.name) for path in include_files],
            context_log=Path(remote_log) if remote_log else None,
        )
        argv = profile.build_argv(argv_ctx)
        aux_paths: list[Path] = []
        for name, content in profile.aux_files(local_ctx):
            aux_path = local_prefix.parent / name
            aux_path.write_text(content)
            aux_paths.append(aux_path)
        remote_cmds: list[list[str]] = []
        use_companion = (
            "transport" in _companion.companion_info().capabilities
        )
        transport_name = (
            "companion-sshrunner" if use_companion else "openssh-subprocess"
        )
        explicit_remote_log = profile.log_path(local_ctx)

        def remote_result(
            returncode: int | None,
            *,
            timeout_error: str | None = None,
            forced_error: str | None = None,
            exception_error: str | None = None,
        ) -> SimulationResult:
            return finish_result(
                prefix=local_prefix,
                argv=argv,
                returncode=returncode,
                is_parallel_wait=is_parallel_wait,
                engine=profile.name,
                profile=profile,
                engine_ctx=local_ctx,
                explicit_log=explicit_remote_log,
                remote_cmds=remote_cmds,
                remote_dir=remote_dir,
                transport=transport_name,
                timeout_error=timeout_error,
                forced_error=forced_error,
                exception_error=exception_error,
            )

        wrapped = _wrap_env_setup(argv, self.env_setup, self.env_setup_shell)
        remote_command = f"cd {remote_dir} && {shlex.join(wrapped)}"

        if use_companion:
            try:
                companion_transport = _companion.CompanionTransport(
                    self.remote.host,
                    self.remote.user,
                    self.timeout,
                )
            except subprocess.TimeoutExpired:
                return remote_result(
                    None, timeout_error=f"timeout after {self.timeout}s"
                )
            except Exception as exc:
                return remote_result(
                    None,
                    forced_error="remote upload stage failed",
                    exception_error=str(exc),
                )

            mkdir_record = ["companion", "mkdir", remote_dir]
            remote_cmds.append(mkdir_record)
            try:
                mkdir_returncode, _, _ = companion_transport.run(
                    f"mkdir -p {remote_dir}", self.timeout
                )
            except subprocess.TimeoutExpired:
                return remote_result(
                    None, timeout_error=f"timeout after {self.timeout}s"
                )
            except Exception as exc:
                return remote_result(
                    None,
                    forced_error="remote upload stage failed",
                    exception_error=str(exc),
                )
            if mkdir_returncode != 0:
                return remote_result(
                    mkdir_returncode, forced_error="remote upload stage failed"
                )

            upload_files = [netlist, *include_files, *aux_paths]
            remote_targets = [f"{remote_dir}/{path.name}" for path in upload_files]
            remote_cmds.append(
                ["companion", "put_batch", *remote_targets]
            )
            try:
                upload_returncode = companion_transport.put_batch(
                    list(zip(upload_files, remote_targets)), self.timeout
                )
            except subprocess.TimeoutExpired:
                return remote_result(
                    None, timeout_error=f"timeout after {self.timeout}s"
                )
            except Exception as exc:
                return remote_result(
                    None,
                    forced_error="remote upload stage failed",
                    exception_error=str(exc),
                )
            if upload_returncode != 0:
                return remote_result(
                    upload_returncode, forced_error="remote upload stage failed"
                )

            remote_cmds.append(["companion", "run", remote_command])
            simulation_returncode: int | None = None
            simulation_timeout = False
            ssh_stage_failure = False
            ssh_exception: str | None = None
            try:
                simulation_returncode, _, _ = companion_transport.run(
                    remote_command, self.timeout
                )
                ssh_stage_failure = simulation_returncode == 255
            except subprocess.TimeoutExpired:
                simulation_timeout = True
            except Exception as exc:
                ssh_stage_failure = True
                ssh_exception = str(exc)

            remote_cmds.append(
                ["companion", "get_dir", remote_dir, str(run_dir)]
            )
            try:
                download_returncode = companion_transport.get_dir(
                    remote_dir, run_dir, self.timeout
                )
            except subprocess.TimeoutExpired:
                return remote_result(
                    simulation_returncode,
                    timeout_error=f"timeout after {self.timeout}s",
                )
            except Exception as exc:
                return remote_result(
                    simulation_returncode,
                    forced_error="remote download stage failed",
                    exception_error=str(exc),
                )
            if download_returncode != 0:
                return remote_result(
                    simulation_returncode,
                    forced_error="remote download stage failed",
                )
            if simulation_timeout:
                return remote_result(
                    None, timeout_error=f"timeout after {self.timeout}s"
                )
            if ssh_stage_failure:
                return remote_result(
                    simulation_returncode,
                    forced_error="remote ssh stage failed",
                    exception_error=ssh_exception,
                )
            return remote_result(simulation_returncode)

        mkdir = ["ssh", target, f"mkdir -p {remote_dir}"]
        remote_cmds.append(mkdir)
        try:
            mkdir_result = _exec(mkdir, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return remote_result(None, timeout_error=f"timeout after {self.timeout}s")
        except FileNotFoundError:
            return remote_result(None, forced_error="remote upload stage failed")
        if mkdir_result.returncode != 0:
            return remote_result(
                mkdir_result.returncode, forced_error="remote upload stage failed"
            )

        upload = [
            "scp",
            str(netlist),
            *(str(path) for path in include_files),
            *(str(path) for path in aux_paths),
            f"{target}:{remote_dir}/",
        ]
        remote_cmds.append(upload)
        try:
            upload_result = _exec(upload, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return remote_result(None, timeout_error=f"timeout after {self.timeout}s")
        except FileNotFoundError:
            return remote_result(None, forced_error="remote upload stage failed")
        if upload_result.returncode != 0:
            return remote_result(
                upload_result.returncode, forced_error="remote upload stage failed"
            )

        ssh = ["ssh", target, remote_command]
        remote_cmds.append(ssh)
        simulation_returncode: int | None = None
        simulation_timeout = False
        ssh_stage_failure = False
        try:
            ssh_result = _exec(ssh, timeout=self.timeout)
            simulation_returncode = ssh_result.returncode
            ssh_stage_failure = ssh_result.returncode == 255
        except subprocess.TimeoutExpired:
            simulation_timeout = True
        except FileNotFoundError:
            ssh_stage_failure = True

        download = ["scp", "-r", f"{target}:{remote_dir}/.", str(run_dir)]
        remote_cmds.append(download)
        try:
            download_result = _exec(download, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return remote_result(
                simulation_returncode, timeout_error=f"timeout after {self.timeout}s"
            )
        except FileNotFoundError:
            return remote_result(
                simulation_returncode,
                forced_error="remote download stage failed",
            )
        if download_result.returncode != 0:
            return remote_result(
                simulation_returncode,
                forced_error="remote download stage failed",
            )
        if simulation_timeout:
            return remote_result(None, timeout_error=f"timeout after {self.timeout}s")
        if ssh_stage_failure:
            return remote_result(
                simulation_returncode,
                forced_error="remote ssh stage failed",
            )
        return remote_result(simulation_returncode)
