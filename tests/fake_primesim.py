#!/usr/bin/env python3

import csv
import os
import re
import sys
import time


DIRECTIVE = re.compile(
    r"^\s*\*\s*fake:(?P<key>[a-z_]+)(=(?P<value>.*))?\s*$"
)


def parse_args(arguments):
    deck = None
    prefix = None
    log_file = None
    aopts = []
    afiles = []
    value_options = {"-runlvl", "-mode", "-o", "-out", "-log", "-mt", "-format"}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in value_options and index + 1 < len(arguments):
            value = arguments[index + 1]
            if argument in {"-o", "-out"}:
                prefix = value
            elif argument == "-log":
                log_file = value
            index += 2
            continue
        if argument in {"-aopt", "-afile"} and index + 1 < len(arguments):
            value = arguments[index + 1]
            (aopts if argument == "-aopt" else afiles).append(value)
            index += 2
            continue
        if argument == "-spice" or argument.startswith("-"):
            index += 1
            continue
        if deck is None:
            deck = argument
        index += 1
    if deck is None:
        deck = "deck.sp"
    if prefix is None:
        prefix = os.path.splitext(deck)[0]
    if log_file is None:
        log_file = prefix + ".log"
    return deck, prefix, log_file, aopts, afiles


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def directives(deck, afiles):
    view = read_text(deck) + "".join(read_text(path) for path in afiles)
    selected = {
        "exit": None,
        "sleep": None,
        "sleep_first": None,
        "rows": "1",
        "log": [],
        "measure": [],
        "dc_fail": False,
        "no_artifacts": False,
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
        elif key in {"log", "measure"}:
            selected[key].append(value or "")
        elif key in {"dc_fail", "no_artifacts", "fsdb"}:
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


def write_log(path, extra_lines, dc_fail):
    ensure_parent(path)
    lines = [
        "PrimeSim fake driver",
        "elaboration complete",
        "analysis complete",
        *extra_lines,
    ]
    if dc_fail:
        lines.append("DC not converged")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def parsed_measures(values):
    measures = []
    for directive in values:
        if "=" in directive:
            name, value = directive.split("=", 1)
            measures.append((name, value))
    return measures


def write_measures(prefix, measures, rows, csv_mode):
    if not measures:
        return
    names = [name for name, _ in measures]
    data_rows = [
        [row_value(value, index) for _, value in measures]
        for index in range(rows)
    ]
    if csv_mode:
        with open(prefix + ".mt0.csv", "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(names)
            writer.writerows(data_rows)
        return
    with open(prefix + ".mt0", "w", encoding="utf-8") as handle:
        handle.write("$DATA1 SOURCE='fake.sp' VERSION='FAKE'\n")
        handle.write(".TITLE 'fake measures'\n")
        handle.write(" ".join(names) + "\n")
        for row in data_rows:
            handle.write(" ".join(row) + "\n")


def main(arguments):
    deck, prefix, log_file, aopts, afiles = parse_args(arguments)
    selected = directives(deck, afiles)
    sleep_first = number(selected["sleep_first"], 0.0)
    if sleep_first > 0:
        time.sleep(sleep_first)

    ensure_parent(prefix)
    write_log(log_file, selected["log"], selected["dc_fail"])
    if not selected["no_artifacts"]:
        rows = max(0, integer(selected["rows"], 1))
        measures = parsed_measures(selected["measure"])
        write_measures(
            prefix,
            measures,
            rows,
            "primesim_measout=3" in aopts,
        )
        with open(prefix + ".ic", "w", encoding="utf-8"):
            pass
        if selected["fsdb"]:
            with open(prefix + ".fsdb", "w", encoding="utf-8"):
                pass

    sleep_after = number(selected["sleep"], 0.0)
    if sleep_after > 0:
        time.sleep(sleep_after)

    if selected["exit"] is not None:
        return integer(selected["exit"], 0)
    if selected["dc_fail"] and "primesim_exit_dc_fail=1" in aopts:
        return 34
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
