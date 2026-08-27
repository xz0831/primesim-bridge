#!/usr/bin/env python3

import csv
import os
import re
import sys
import time


DIRECTIVE = re.compile(r"^\s*\*\s*fake:(?P<key>[a-z_]+)(=(?P<value>.*))?\s*$")
VALUE_OPTIONS = {
    "-i",
    "-o",
    "-mt",
    "-wavefmt",
    "-format",
    "-include_first",
    "-include_last",
    "-case",
    "-n",
    "-alter_select",
}


def parse_args(arguments):
    deck = None
    prefix = None
    include_first = []
    include_last = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in VALUE_OPTIONS and index + 1 < len(arguments):
            value = arguments[index + 1]
            if argument == "-i":
                deck = value
            elif argument == "-o":
                prefix = value
            elif argument == "-include_first":
                include_first.append(value)
            elif argument == "-include_last":
                include_last.append(value)
            index += 2
            continue
        index += 1
    return deck, prefix, include_first, include_last


def read_text(path):
    if path is None:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def deck_view(deck, include_first, include_last):
    return (
        "".join(read_text(path) for path in include_first)
        + read_text(deck)
        + "".join(read_text(path) for path in include_last)
    )


def directives(view):
    selected = {
        "exit": None,
        "sleep": None,
        "sleep_first": None,
        "rows": "1",
        "alter_measures": "0",
        "log": [],
        "error": [],
        "measure": [],
        "measure_failed": [],
        "no_banner": False,
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
        if key in {"exit", "sleep", "sleep_first", "rows", "alter_measures"}:
            selected[key] = value
        elif key in {"log", "error", "measure", "measure_failed"}:
            selected[key].append(value or "")
        elif key in {"no_banner", "fsdb"}:
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
    if value.lower() == "failed":
        return value
    try:
        return str(float(value) + index)
    except ValueError:
        return value


def ensure_parent(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)


def parsed_measures(values, failed_names):
    measures = []
    for directive in values:
        if "=" in directive:
            name, value = directive.split("=", 1)
            measures.append((name, "failed" if name in failed_names else value))
    present = {name for name, _ in measures}
    measures.extend((name, "failed") for name in failed_names if name not in present)
    return measures


def write_measure(prefix, index, measures, rows, csv_mode):
    names = [name for name, _ in measures]
    data_rows = [
        [row_value(value, row_index) for _, value in measures]
        for row_index in range(rows)
    ]
    suffix = f".mt{index}.csv" if csv_mode else f".mt{index}"
    with open(prefix + suffix, "w", encoding="utf-8", newline="") as handle:
        if csv_mode:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(names)
            writer.writerows(data_rows)
        else:
            handle.write("$DATA1 SOURCE='fake.sp' VERSION='FAKE'\n")
            handle.write(".TITLE 'fake measures'\n")
            handle.write(" ".join(names) + "\n")
            for row in data_rows:
                handle.write(" ".join(row) + "\n")


def write_listing(prefix, selected):
    lines = ["PrimeSim HSPICE fake driver", "analysis complete"]
    lines.extend(selected["log"])
    lines.extend(f"**error** {message}" for message in selected["error"])
    if not selected["no_banner"]:
        lines.append("***** job concluded ******")
    with open(prefix + ".lis", "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(arguments):
    deck, prefix, include_first, include_last = parse_args(arguments)
    view = deck_view(deck, include_first, include_last)
    if prefix is None:
        sys.stdout.write(view)
        return 0

    selected = directives(view)
    sleep_first = number(selected["sleep_first"], 0.0)
    if sleep_first > 0:
        time.sleep(sleep_first)

    ensure_parent(prefix)
    write_listing(prefix, selected)
    with open(prefix + ".st0", "w", encoding="utf-8") as handle:
        handle.write("fake status\nanalysis complete\n")
    with open(prefix + ".ic0", "w", encoding="utf-8"):
        pass

    csv_mode = re.search(r"measform\s*=\s*3", view, re.IGNORECASE) is not None
    measures = parsed_measures(selected["measure"], selected["measure_failed"])
    if measures:
        write_measure(
            prefix,
            0,
            measures,
            max(0, integer(selected["rows"], 1)),
            csv_mode,
        )
    for index in range(1, max(0, integer(selected["alter_measures"], 0)) + 1):
        write_measure(prefix, index, [(f"alter{index}", str(index))], 1, csv_mode)
    if selected["fsdb"]:
        with open(prefix + ".fsdb", "w", encoding="utf-8"):
            pass

    sleep_after = number(selected["sleep"], 0.0)
    if sleep_after > 0:
        time.sleep(sleep_after)
    return integer(selected["exit"], 0) if selected["exit"] is not None else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
