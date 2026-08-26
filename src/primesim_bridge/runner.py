from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel

from .argv import build_primesim_argv, primesim_mode_args
from .models import ExecutionStatus, SimulationResult, classify_exit
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


def _has_aopt(extra_args: list[str], option_name: str) -> bool:
    for index in range(len(extra_args) - 1):
        if extra_args[index] == "-aopt":
            if extra_args[index + 1].split("=", 1)[0] == option_name:
                return True
    return False


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


class PrimeSimSimulator:
    def __init__(
        self,
        *,
        work_dir: Path,
        binary: str = "primesim",
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

    def _next_run_id(self) -> str:
        self._run_counter += 1
        return f"run{self._run_counter}"

    @classmethod
    def from_env(cls, **overrides: Any) -> "PrimeSimSimulator":
        values: Dict[str, Any] = {}
        binary = os.environ.get("VB_PRIMESIM_BIN")
        setup = os.environ.get("VB_SYNOPSYS_SETUP")
        setup_shell = os.environ.get("VB_SYNOPSYS_SETUP_SHELL")
        remote_host = os.environ.get("VB_REMOTE_HOST", "")
        remote_user = os.environ.get("VB_REMOTE_USER")
        if binary:
            values["binary"] = binary
        if setup:
            values["env_setup"] = setup
        if setup_shell:
            values["env_setup_shell"] = setup_shell
        if remote_host and remote_host.lower() != "localhost":
            values["remote"] = RemoteSpec(host=remote_host, user=remote_user or None)
        values.update(overrides)
        return cls(**values)

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
        self, prefix: Path, explicit_log: Path | None = None
    ) -> tuple[dict[str, Any], list[str], list[str], list[str], dict[str, list[Path]]]:
        outputs = collect_outputs(prefix)
        if explicit_log is not None and explicit_log.is_file():
            if explicit_log not in outputs["log"]:
                outputs["log"].append(explicit_log)
                outputs["log"].sort()
        data: dict[str, Any] = {}
        errors: list[str] = []
        warnings: list[str] = []
        signatures: list[str] = []
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
            if "_rows" in parsed:
                row_count = parsed.pop("_rows")
                data["_rows"] = max(int(data.get("_rows", 0)), int(row_count))
            data.update(parsed)
        for op_path in outputs["op"]:
            data.update(parse_op_ascii(op_path))
        for log_path in outputs["log"]:
            parsed_log = parse_log(log_path)
            _merge_unique(errors, parsed_log["errors"])
            _merge_unique(warnings, parsed_log["warnings"])
            _merge_unique(signatures, parsed_log["signatures"])
        return data, errors, warnings, signatures, outputs

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
        dc_safety_injected: bool,
        explicit_log: Path | None = None,
        remote_cmds: list[list[str]] | None = None,
        timeout_error: str | None = None,
        forced_error: str | None = None,
    ) -> SimulationResult:
        data, errors, warnings, signatures, outputs = self._parse_artifacts(
            prefix, explicit_log
        )
        metadata: dict[str, Any] = {
            "argv": argv,
            "returncode": returncode,
            "output_files": self._serialized_outputs(outputs),
            "prefix": str(prefix),
            "log_signatures": signatures,
        }
        if remote_cmds is not None:
            metadata["remote_cmds"] = remote_cmds
        if "_rows" in data:
            metadata["_rows"] = data.pop("_rows")

        dc_signature = "DC not converged"
        if dc_safety_injected and dc_signature in signatures:
            promoted = [item for item in warnings if dc_signature.lower() in item.lower()]
            warnings = [
                item for item in warnings if dc_signature.lower() not in item.lower()
            ]
            if promoted:
                _merge_unique(errors, promoted)
            else:
                _merge_unique(errors, [dc_signature])

        artifact_count = sum(len(paths) for paths in outputs.values())
        if timeout_error is not None:
            _merge_unique(errors, [timeout_error])
            status = ExecutionStatus.PARTIAL if artifact_count else ExecutionStatus.FAILURE
        elif forced_error is not None:
            _merge_unique(errors, [forced_error])
            status = ExecutionStatus.FAILURE
        elif is_parallel_wait:
            status = ExecutionStatus.FAILURE if errors else ExecutionStatus.SUCCESS
        else:
            effective_returncode = returncode if returncode is not None else 1
            exit_status, exit_error = classify_exit(effective_returncode)
            if exit_status is ExecutionStatus.FAILURE:
                if exit_error is not None:
                    _merge_unique(errors, [exit_error])
                status = ExecutionStatus.FAILURE
            elif errors:
                status = ExecutionStatus.PARTIAL
            else:
                status = ExecutionStatus.SUCCESS
        return SimulationResult(
            status=status,
            data=data,
            errors=errors,
            warnings=warnings,
            metadata=metadata,
        )

    def run_simulation(
        self, netlist: Path, options: dict[str, Any] | None = None
    ) -> SimulationResult:
        selected = dict(options or {})
        netlist = Path(netlist)
        supplied_prefix = selected.get("prefix")
        prefix = Path(supplied_prefix) if supplied_prefix is not None else self._default_prefix(netlist)
        run_dir = prefix.parent
        run_dir.mkdir(parents=True, exist_ok=True)

        engine_args = primesim_mode_args(
            selected.get("engine", "spice"),
            runlvl=selected.get("runlvl"),
            mode=selected.get("mode"),
        )
        extra_args = list(selected.get("extra_args") or [])
        include_files = [Path(path) for path in selected.get("include_files") or []]
        is_parallel_wait = bool(selected.get("is_parallel_wait", False))
        dc_safety_injected = not _has_aopt(extra_args, "primesim_exit_dc_fail")
        log_value = selected.get("log_file")
        waveform_format = selected.get("waveform_format")

        if self.remote is None:
            include_destinations: list[Path] = []
            for include_file in include_files:
                destination = netlist.parent / include_file.name
                if include_file.resolve() != destination.resolve():
                    shutil.copy2(include_file, destination)
                include_destinations.append(destination)
            local_extra_args = list(extra_args)
            for destination in include_destinations:
                local_extra_args.extend(["-afile", str(destination)])
            argv = build_primesim_argv(
                netlist=str(netlist),
                prefix=str(prefix),
                binary=self.binary,
                log_file=str(log_value) if log_value is not None else None,
                engine_args=engine_args,
                threads=selected.get("threads"),
                waveform_format=waveform_format,
                extra_args=local_extra_args,
            )
            explicit_log = Path(log_value) if log_value is not None else None
            if self.env_setup is None and shutil.which(self.binary) is None:
                return self._finish_result(
                    prefix=prefix,
                    argv=argv,
                    returncode=None,
                    is_parallel_wait=is_parallel_wait,
                    dc_safety_injected=dc_safety_injected,
                    explicit_log=explicit_log,
                    forced_error=f"primesim executable not found: {self.binary}",
                )
            command = _wrap_env_setup(argv, self.env_setup, self.env_setup_shell)
            try:
                completed = _exec(command, timeout=self.timeout)
            except FileNotFoundError:
                return self._finish_result(
                    prefix=prefix,
                    argv=argv,
                    returncode=None,
                    is_parallel_wait=is_parallel_wait,
                    dc_safety_injected=dc_safety_injected,
                    explicit_log=explicit_log,
                    forced_error=f"primesim executable not found: {self.binary}",
                )
            except subprocess.TimeoutExpired:
                return self._finish_result(
                    prefix=prefix,
                    argv=argv,
                    returncode=None,
                    is_parallel_wait=is_parallel_wait,
                    dc_safety_injected=dc_safety_injected,
                    explicit_log=explicit_log,
                    timeout_error=f"timeout after {self.timeout}s",
                )
            return self._finish_result(
                prefix=prefix,
                argv=argv,
                returncode=completed.returncode,
                is_parallel_wait=is_parallel_wait,
                dc_safety_injected=dc_safety_injected,
                explicit_log=explicit_log,
            )

        run_id = self.run_id_factory()
        remote_dir = f"~/.primesim_bridge/runs/{run_id}"
        target = (
            f"{self.remote.user}@{self.remote.host}"
            if self.remote.user
            else self.remote.host
        )
        remote_extra_args = list(extra_args)
        for include_file in include_files:
            remote_extra_args.extend(["-afile", include_file.name])
        remote_log = Path(log_value).name if log_value is not None else None
        argv = build_primesim_argv(
            netlist=netlist.name,
            prefix=prefix.name,
            binary=self.binary,
            log_file=remote_log,
            engine_args=engine_args,
            threads=selected.get("threads"),
            waveform_format=waveform_format,
            extra_args=remote_extra_args,
        )
        remote_cmds: list[list[str]] = []
        upload = [
            "scp",
            str(netlist),
            *(str(path) for path in include_files),
            f"{target}:{remote_dir}/",
        ]
        remote_cmds.append(upload)
        try:
            upload_result = _exec(upload, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return self._finish_result(
                prefix=prefix,
                argv=argv,
                returncode=None,
                is_parallel_wait=is_parallel_wait,
                dc_safety_injected=dc_safety_injected,
                remote_cmds=remote_cmds,
                timeout_error=f"timeout after {self.timeout}s",
            )
        except FileNotFoundError:
            return self._finish_result(
                prefix=prefix,
                argv=argv,
                returncode=None,
                is_parallel_wait=is_parallel_wait,
                dc_safety_injected=dc_safety_injected,
                remote_cmds=remote_cmds,
                forced_error="remote upload stage failed",
            )
        if upload_result.returncode != 0:
            return self._finish_result(
                prefix=prefix,
                argv=argv,
                returncode=upload_result.returncode,
                is_parallel_wait=is_parallel_wait,
                dc_safety_injected=dc_safety_injected,
                remote_cmds=remote_cmds,
                forced_error="remote upload stage failed",
            )

        wrapped = _wrap_env_setup(argv, self.env_setup, self.env_setup_shell)
        remote_command = f"cd {remote_dir} && {shlex.join(wrapped)}"
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
            return self._finish_result(
                prefix=prefix,
                argv=argv,
                returncode=simulation_returncode,
                is_parallel_wait=is_parallel_wait,
                dc_safety_injected=dc_safety_injected,
                explicit_log=run_dir / remote_log if remote_log else None,
                remote_cmds=remote_cmds,
                timeout_error=f"timeout after {self.timeout}s",
            )
        except FileNotFoundError:
            return self._finish_result(
                prefix=prefix,
                argv=argv,
                returncode=simulation_returncode,
                is_parallel_wait=is_parallel_wait,
                dc_safety_injected=dc_safety_injected,
                explicit_log=run_dir / remote_log if remote_log else None,
                remote_cmds=remote_cmds,
                forced_error="remote download stage failed",
            )
        if download_result.returncode != 0:
            return self._finish_result(
                prefix=prefix,
                argv=argv,
                returncode=simulation_returncode,
                is_parallel_wait=is_parallel_wait,
                dc_safety_injected=dc_safety_injected,
                explicit_log=run_dir / remote_log if remote_log else None,
                remote_cmds=remote_cmds,
                forced_error="remote download stage failed",
            )
        if simulation_timeout:
            return self._finish_result(
                prefix=prefix,
                argv=argv,
                returncode=None,
                is_parallel_wait=is_parallel_wait,
                dc_safety_injected=dc_safety_injected,
                explicit_log=run_dir / remote_log if remote_log else None,
                remote_cmds=remote_cmds,
                timeout_error=f"timeout after {self.timeout}s",
            )
        if ssh_stage_failure:
            return self._finish_result(
                prefix=prefix,
                argv=argv,
                returncode=simulation_returncode,
                is_parallel_wait=is_parallel_wait,
                dc_safety_injected=dc_safety_injected,
                explicit_log=run_dir / remote_log if remote_log else None,
                remote_cmds=remote_cmds,
                forced_error="remote ssh stage failed",
            )
        return self._finish_result(
            prefix=prefix,
            argv=argv,
            returncode=simulation_returncode,
            is_parallel_wait=is_parallel_wait,
            dc_safety_injected=dc_safety_injected,
            explicit_log=run_dir / remote_log if remote_log else None,
            remote_cmds=remote_cmds,
        )
