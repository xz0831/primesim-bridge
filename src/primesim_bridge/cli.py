from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import _companion
from . import runner as runner_module
from .argv import PRO_MODES
from .engines import EngineContext, get_profile
from .parsers import (
    collect_outputs,
    parse_log,
    parse_measure_ascii,
    parse_measure_csv,
    parse_op_ascii,
)
from .runner import PrimeSimSimulator, RemoteSpec


STATUS_ENV_VARS = (
    "PSB_REMOTE_HOST",
    "PSB_REMOTE_USER",
    "VB_PRIMESIM_BIN",
    "VB_SYNOPSYS_SETUP",
    "VB_SYNOPSYS_SETUP_SHELL",
    "VB_REMOTE_HOST",
    "VB_REMOTE_USER",
    "SNPSLMD_LICENSE_FILE",
    "LM_LICENSE_FILE",
    "PRIMESIM",
    "PRIMESIM_ORDER",
    "PRIMESIM_WAIT_LICENSE",
    "PRIMESIM_WAIT_LICENSE_TIMEOUT",
    "PRIMESIM_WAIT_LICENSE_INTERVAL",
)
LICENSE_TOKEN_NAMES = (
    "CKTSIMMC",
    "CKTSIMPROFS",
    "CKTSIMSPICE",
    "PRIMESIM_LIC",
    "PRIMESIMSPICE_LIC",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="primesim-bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("netlist")
    run_parser.add_argument(
        "--engine", choices=("spice", "pro", "hspice", "xa"), default="spice"
    )
    run_parser.add_argument("--dialect", choices=("hspice", "spectre", "eldo"))
    run_parser.add_argument("--binary")
    run_parser.add_argument("--runlvl", type=int)
    run_parser.add_argument("--mode", choices=sorted(PRO_MODES))
    run_parser.add_argument("-o", "--prefix")
    run_parser.add_argument("--mt", type=int, dest="threads")
    run_parser.add_argument("--format", dest="waveform_format")
    run_parser.add_argument("--log", dest="log_file")
    run_parser.add_argument("--remote")
    run_parser.add_argument("--timeout", type=int, default=3600)
    run_parser.add_argument("--waveforms", action="store_true")
    run_parser.add_argument("--dry-run", action="store_true")

    parse_parser = subparsers.add_parser("parse")
    parse_parser.add_argument("prefix")

    waveview_parser = subparsers.add_parser("waveview")
    waveview_parser.add_argument("prefix")
    waveview_parser.add_argument("--deck", help="netlist to mine .probe/.print signals from")
    waveview_parser.add_argument("--signals", nargs="*", default=None)
    waveview_parser.add_argument("--no-session", action="store_true")

    subparsers.add_parser("status")
    return parser


def _default_prefix(netlist: str) -> str:
    path = Path(netlist)
    return str(path.parent / path.stem)


def _run(args: argparse.Namespace) -> int:
    try:
        profile = get_profile(args.engine)
        if args.dry_run:
            binary = (
                args.binary
                if args.binary is not None
                else os.environ.get(profile.env_binary_var, profile.default_binary)
            )
            dry_options = {
                "engine": args.engine,
                "runlvl": args.runlvl,
                "mode": args.mode,
                "dialect": args.dialect,
                "dry_run": True,
            }
            argv = profile.build_argv(EngineContext(
                netlist=Path(args.netlist),
                prefix=Path(args.prefix or _default_prefix(args.netlist)),
                binary=binary,
                options=dry_options,
                extra_args=(),
                include_files=(),
                threads=args.threads,
                waveform_format=args.waveform_format,
                log_file=Path(args.log_file) if args.log_file is not None else None,
                safety=True,
            ))
            print(shlex.join(argv))
            return 0

        simulator_overrides: dict[str, Any] = {
            "work_dir": Path.cwd(),
            "timeout": args.timeout,
        }
        if args.binary is not None:
            simulator_overrides["binary"] = args.binary
        if args.remote:
            simulator_overrides["remote"] = RemoteSpec(host=args.remote)
        simulator = PrimeSimSimulator.from_env(**simulator_overrides)
        options = {
            "engine": args.engine,
            "runlvl": args.runlvl,
            "mode": args.mode,
            "dialect": args.dialect,
            "threads": args.threads,
            "waveform_format": args.waveform_format,
            "log_file": args.log_file,
            "prefix": args.prefix,
            "parse_waveforms": args.waveforms,
        }
        result = simulator.run_simulation(
            Path(args.netlist),
            {key: value for key, value in options.items() if value is not None},
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    print(
        json.dumps(
            {
                "status": result.status.value,
                "data": result.data,
                "errors": result.errors,
                "warnings": result.warnings,
            },
            sort_keys=True,
        )
    )
    return 0 if result.ok else 1


def _parse(args: argparse.Namespace) -> int:
    prefix = Path(args.prefix)
    outputs = collect_outputs(prefix)
    data: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []
    signatures: list[str] = []
    rows = 0
    alter_measures: dict[str, dict[str, Any]] = {}
    alter_rows: dict[str, int] = {}
    indexed_measures: dict[Path, int] = {}
    for path in outputs["measure"]:
        matches = list(
            re.finditer(r"\.(?:mt|ma|ms|md|mc)(\d+)", path.name.lower())
        )
        if matches:
            indexed_measures[path] = int(matches[-1].group(1))
    minimum_index = min(indexed_measures.values(), default=None)
    for path in outputs["measure"]:
        name = path.name.lower()
        if name.endswith(".gzip"):
            name = name[:-5]
        elif name.endswith(".gz"):
            name = name[:-3]
        parsed = parse_measure_csv(path) if name.endswith(".csv") else parse_measure_ascii(path)
        warnings.extend(parsed.pop("_warnings", []))
        parsed_rows = int(parsed.pop("_rows", 0))
        rows = max(rows, parsed_rows)
        measure_index = indexed_measures.get(path)
        if (
            minimum_index is not None
            and measure_index is not None
            and measure_index > minimum_index
        ):
            alter_measures[path.name] = dict(parsed)
            if parsed_rows:
                alter_rows[path.name] = parsed_rows
        data.update(parsed)
    for path in outputs["op"]:
        data.update(parse_op_ascii(path))
    for path in outputs["log"]:
        parsed_log = parse_log(path)
        errors.extend(parsed_log["errors"])
        warnings.extend(parsed_log["warnings"])
        signatures.extend(parsed_log["signatures"])
    metadata: dict[str, Any] = {
        "output_files": {
            bucket: [str(path) for path in paths] for bucket, paths in outputs.items()
        },
        "log_signatures": signatures,
    }
    if rows:
        metadata["_rows"] = rows
    if alter_measures:
        metadata["alter_measures"] = alter_measures
    if alter_rows:
        metadata["alter_rows"] = alter_rows
    print(
        json.dumps(
            {
                "data": data,
                "errors": errors,
                "warnings": warnings,
                "metadata": metadata,
            },
            sort_keys=True,
        )
    )
    return 0


def _status() -> int:
    companion = _companion.companion_info()
    companion_env_file = _companion.env_file()
    report: dict[str, Any] = {
        "primesim": shutil.which("primesim"),
        "env_set": [name for name in STATUS_ENV_VARS if name in os.environ],
        "license_tokens": [],
        "companion": {
            "available": companion.available,
            "version": companion.version,
            "verified": companion.verified,
            "capabilities": sorted(companion.capabilities),
            "env_file": (
                str(companion_env_file) if companion_env_file is not None else None
            ),
        },
    }
    if shutil.which("lmstat") is not None:
        try:
            completed = runner_module._exec(["lmstat", "-a"], timeout=10)
            token_pattern = re.compile(
                r"Users of (?:" + "|".join(re.escape(name) for name in LICENSE_TOKEN_NAMES) + r")"
            )
            report["license_tokens"] = [
                line
                for line in (completed.stdout or "").splitlines()
                if token_pattern.search(line)
            ]
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            pass
    print(json.dumps(report, sort_keys=True))
    return 0


def _waveview(args: argparse.Namespace) -> int:
    from primesim_bridge.waveview import write_waveview_script

    deck_text = ""
    if args.deck:
        deck_path = Path(args.deck)
        if deck_path.is_file():
            deck_text = deck_path.read_text(errors="replace")
    outcome = write_waveview_script(
        Path(args.prefix),
        deck_text=deck_text,
        signals=args.signals,
        save_session=not args.no_session,
    )
    print(
        json.dumps(
            {
                "script": str(outcome["script"]) if outcome["script"] else None,
                "session": str(outcome["session"]) if outcome["session"] else None,
                "fsdb_files": [str(p) for p in outcome["fsdb_files"]],
                "signals": outcome["signals"],
                "launch": outcome["launch"],
                "warnings": outcome["warnings"],
            },
            sort_keys=True,
        )
    )
    return 0 if outcome["script"] else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "parse":
        return _parse(args)
    if args.command == "waveview":
        return _waveview(args)
    return _status()


if __name__ == "__main__":
    raise SystemExit(main())
