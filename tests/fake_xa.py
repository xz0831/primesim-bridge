#!/usr/bin/env python3

import os
import re
import sys
import time


DIRECTIVE = re.compile(r"^\s*\*\s*fake:(?P<key>[a-z_]+)(=(?P<value>.*))?\s*$")
VALUE_OPTIONS = {"-o", "-c", "-mt", "-wavefmt", "-format", "-outfilefmt"}
BARE_FLAGS = {"-hspice", "-spectre", "-eldo", "-gz"}


def parse_args(arguments):
    deck = None
    prefix = None
    command_files = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in VALUE_OPTIONS and index + 1 < len(arguments):
            value = arguments[index + 1]
            if argument == "-o":
                prefix = value
            elif argument == "-c":
                command_files.append(value)
            index += 2
            continue
        if argument in BARE_FLAGS:
            index += 1
            continue
        if not argument.startswith("-") and deck is None:
            deck = argument
        index += 1
    return deck, prefix, command_files


def read_text(path):
    if path is None:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def deck_view(deck, command_files):
    return read_text(deck) + "".join(read_text(path) for path in command_files)


def directives(view):
    selected = {
        "exit": None,
        "sleep": None,
        "sleep_first": None,
        "rows": "1",
        "log": [],
        "error": [],
        "measure": [],
        "no_walltime": False,
        "fsdb": False,
    }
    for line in view.splitlines():
        match = DIRECTIVE.match(line)
        if match is None:
            continue
        key = match.group("key")
        value = match.group("value")
        if value is not None:
            value = value.rstrip()
        if key in {"exit", "sleep", "sleep_first", "rows"}:
            selected[key] = value
        elif key in {"log", "error", "measure"}:
            selected[key].append(value or "")
        elif key in {"no_walltime", "fsdb"}:
            selected[key] = True
    return selected


def number(value, fallback):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def integer(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def row_value(value, index):
    try:
        return str(float(value) + index)
    except ValueError:
        return value


def ensure_parent(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)


def parsed_measures(values):
    measures = []
    for directive in values:
        if "=" in directive:
            measures.append(tuple(directive.split("=", 1)))
    return measures


def write_log(prefix, selected):
    lines = ["PrimeSim XA fake driver", "analysis complete"]
    lines.extend(selected["log"])
    lines.extend(f"Error: {message}" for message in selected["error"])
    if not selected["no_walltime"]:
        lines.append("Total Wall Time = 1 sec (0hr 0min 1sec)")
    with open(prefix + ".log", "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def write_classic_measure(prefix, measures, rows):
    with open(prefix + ".mt", "w", encoding="utf-8") as handle:
        handle.write("$DATA1 SOURCE='fake.sp' VERSION='FAKE'\n")
        handle.write(".TITLE 'fake XA measures'\n")
        handle.write(" ".join(name for name, _ in measures) + "\n")
        for row_index in range(rows):
            handle.write(
                " ".join(row_value(value, row_index) for _, value in measures)
                + "\n"
            )


def write_xa_measure(prefix, measures):
    with open(prefix + ".meas", "w", encoding="utf-8") as handle:
        handle.write("# XA measure row format (not parsed by the bridge)\n")
        for name, value in measures:
            handle.write(f"{name}: {value}\n")


def main(arguments):
    deck, prefix, command_files = parse_args(arguments)
    view = deck_view(deck, command_files)
    output_prefix = prefix if prefix is not None else "xa"
    selected = directives(view)

    sleep_first = number(selected["sleep_first"], 0.0)
    if sleep_first > 0:
        time.sleep(sleep_first)

    ensure_parent(output_prefix)
    write_log(output_prefix, selected)
    measures = parsed_measures(selected["measure"])
    if measures:
        if "set_meas_option -format hspice" in view:
            write_classic_measure(
                output_prefix,
                measures,
                max(0, integer(selected["rows"], 1)),
            )
        else:
            write_xa_measure(output_prefix, measures)
    if selected["fsdb"]:
        with open(output_prefix + ".fsdb", "w", encoding="utf-8"):
            pass

    sleep_after = number(selected["sleep"], 0.0)
    if sleep_after > 0:
        time.sleep(sleep_after)
    return integer(selected["exit"], 0) if selected["exit"] is not None else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
