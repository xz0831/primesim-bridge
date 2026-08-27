from __future__ import annotations

import csv
import gzip
import re
from pathlib import Path
from typing import Any, Optional, Sequence, TextIO


_NUMBER_RE = re.compile(
    r"^(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?P<suffix>meg|[fpnumkxgt])?(?P<trailing>[A-Za-z]*)$",
    re.IGNORECASE,
)
_SUFFIX_SCALE = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "x": 1e6,
    "meg": 1e6,
    "g": 1e9,
    "t": 1e12,
}
_ZERO_SUMMARY_RE = re.compile(r"^\s*0 (errors|warnings)", re.IGNORECASE)


def _open_text(path: Path) -> TextIO:
    if path.name.lower().endswith((".gz", ".gzip")):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="")
    return path.open("r", encoding="utf-8", errors="replace", newline="")


def parse_hspice_number(tok: str) -> float | str:
    match = _NUMBER_RE.fullmatch(tok)
    if match is None:
        return tok
    suffix = match.group("suffix")
    trailing = match.group("trailing")
    if trailing and suffix is None:
        return tok
    value = float(match.group("number"))
    if suffix is not None:
        value *= _SUFFIX_SCALE[suffix.lower()]
    return value


def _shape_rows(
    names: list[str], rows: list[list[str]], *, failed_is_none: bool
) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    warnings: list[str] = []
    columns: list[list[Any]] = [[] for _ in names]
    for row_index, row in enumerate(rows, start=1):
        for column_index, name in enumerate(names):
            token = row[column_index] if column_index < len(row) else ""
            if failed_is_none and token.strip().lower() == "failed":
                value: Any = None
                warnings.append(f"measure {name} failed in row {row_index}")
            else:
                value = parse_hspice_number(token.strip())
            columns[column_index].append(value)
    if len(rows) == 1:
        parsed.update({name: values[0] for name, values in zip(names, columns)})
    else:
        parsed.update({name: values for name, values in zip(names, columns)})
        parsed["_rows"] = len(rows)
    if warnings:
        parsed["_warnings"] = warnings
    return parsed


def parse_measure_csv(path: Path) -> dict[str, Any]:
    with _open_text(path) as handle:
        rows = [row for row in csv.reader(handle) if row]
    if not rows:
        return {}
    names = [name.strip() for name in rows[0]]
    return _shape_rows(names, rows[1:], failed_is_none=True)


def parse_measure_ascii(path: Path) -> dict[str, Any]:
    try:
        with _open_text(path) as handle:
            raw_lines = handle.read().splitlines()
        lines = [line for line in raw_lines if line.strip()]
        if (
            len(lines) < 4
            or not lines[0].lstrip().upper().startswith("$DATA1")
            or not lines[1].lstrip().upper().startswith(".TITLE")
        ):
            return {"raw_lines": raw_lines, "parse_confidence": "low"}
        names = lines[2].split()
        value_rows = [line.split() for line in lines[3:]]
        if not names or not value_rows or any(len(row) != len(names) for row in value_rows):
            return {"raw_lines": raw_lines, "parse_confidence": "low"}
        return _shape_rows(names, value_rows, failed_is_none=True)
    except (OSError, UnicodeError):
        return {"raw_lines": [], "parse_confidence": "low"}


def parse_op_ascii(path: Path) -> dict[str, Any]:
    try:
        with _open_text(path) as handle:
            raw_lines = handle.read().splitlines()
        parsed: dict[str, Any] = {}
        for line in raw_lines:
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            normalized_key = key.strip()
            normalized_value = value.strip().split()[0] if value.strip() else ""
            if normalized_key:
                parsed[normalized_key] = parse_hspice_number(normalized_value)
        if parsed:
            return parsed
        return {"raw_lines": raw_lines, "parse_confidence": "low"}
    except (OSError, UnicodeError):
        return {"raw_lines": [], "parse_confidence": "low"}


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def parse_log_text(
    text: str, extra_signatures: Optional[Sequence[str]] = None
) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    signatures: list[str] = []
    divergence_signature = "ERROR! time step too small (diverged)"
    dc_signature = "DC not converged"
    for line in text.splitlines():
        stripped = line.strip()
        if _ZERO_SUMMARY_RE.match(line):
            continue
        matched_extra_signature = False
        for signature in extra_signatures or ():
            if signature.lower() in line.lower():
                _append_once(signatures, signature)
                matched_extra_signature = True
        if matched_extra_signature:
            continue
        if divergence_signature.lower() in line.lower():
            _append_once(signatures, divergence_signature)
            _append_once(errors, stripped)
        if dc_signature.lower() in line.lower():
            _append_once(signatures, dc_signature)
            _append_once(warnings, stripped)
        lowered = line.lower()
        if "error" in lowered:
            _append_once(errors, stripped)
        if "warning" in lowered:
            _append_once(warnings, stripped)
    return {"errors": errors, "warnings": warnings, "signatures": signatures}


def parse_log(
    path: Path, extra_signatures: Optional[Sequence[str]] = None
) -> dict[str, list[str]]:
    with _open_text(path) as handle:
        return parse_log_text(handle.read(), extra_signatures=extra_signatures)


def _without_compression_suffix(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".gzip"):
        return lowered[:-5]
    if lowered.endswith(".gz"):
        return lowered[:-3]
    return lowered


def _bucket_for(
    path: Path, *, convention_one: bool, prefix_name: str = ""
) -> str:
    if convention_one:
        return "waveform"
    name = _without_compression_suffix(path.name)
    if re.search(r"\.(?:mt|ms|md|ma|mc)\d+(?:\.csv)?$", name) or re.search(
        r"\.meas(?:\.csv)?$", name
    ) or name == prefix_name.lower() + ".csv":
        return "measure"
    if re.search(r"\.mt(?:\.csv)?$", name):
        return "measure"
    if re.search(r"\.(?:pt|pd|pa|printtr)\d+$", name):
        return "print"
    if re.search(r"\.op\d+$", name):
        return "op"
    if name.endswith((".log", ".lis")):
        return "log"
    if name.endswith((".fsdb", ".out", ".wdf", ".psf")) or re.search(
        r"\.(?:tr|sw|ac)\d+$", name
    ):
        return "waveform"
    if name.endswith((".ic", ".ins", ".fast")):
        return "other"
    return "other"


def collect_outputs(prefix: Path) -> dict[str, list[Path]]:
    buckets: dict[str, list[Path]] = {
        "measure": [],
        "print": [],
        "op": [],
        "log": [],
        "waveform": [],
        "other": [],
    }
    parent = prefix.parent
    if not parent.is_dir():
        return buckets
    prefix_name = prefix.name
    for candidate in sorted(parent.iterdir()):
        if not (
            candidate.name.startswith(prefix_name + ".")
            or candidate.name.startswith(prefix_name + "_")
        ):
            continue
        if candidate.is_dir():
            for nested in sorted(path for path in candidate.rglob("*") if path.is_file()):
                buckets["waveform"].append(nested)
            continue
        bucket = _bucket_for(
            candidate, convention_one=False, prefix_name=prefix_name
        )
        buckets[bucket].append(candidate)
    return buckets
