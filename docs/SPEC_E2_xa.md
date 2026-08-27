# SPEC: primesim-bridge E2 — PrimeSim XA engine

> Owner: main session (design) → Codex (implementation). 2026-08-28.
> Context: E1 (commit 617a4f3) established the EngineProfile abstraction
> (`src/primesim_bridge/engines.py`) with two profiles (primesim, hspice) — read
> that module and `docs/SPEC_E1_engines_hspice.md` first: E2 adds a THIRD profile,
> `xa`, following the E1 patterns exactly (two contexts, classify-in-final-else,
> aux-file mechanics, per-engine binary resolution, zero edits to pre-E2 tests).
> XA facts cite the local "PrimeSim XA Y-2026.03" docs (UG = User Guide,
> CR = Command Reference, RN = Release Notes, PIN = Installation Notes; printed
> pages; PDFs in the session scratchpad as 03_xa_user.pdf / 04_xa_cmd_ref.pdf).
> **(ASSUMED)** items are the only non-doc-backed ones. Do not guess XA behavior
> beyond this sheet. Note: XA is Linux-only (PIN p.2) — it can never run on this
> Mac; the fake double and remote/LSF paths are the whole home-side story.

## Verified XA facts (do not re-litigate; extracted 2026-08-28)

Invocation:

- Base syntax `xa [-format] netlist_file [options]` — netlist is POSITIONAL,
  exactly one (UG pp.26-27). Dialect flags: `-hspice` (DEFAULT when omitted),
  `-spectre`, `-eldo` (UG p.27 Table 2).
- Output: `-o [outpath/]outfile` = directory + prefix in one option. ⚠ Without
  `-o` the default prefix is the LITERAL `xa` (`-outfilefmt` default `xa`;
  `-outfilefmt hspice` switches the default to the netlist basename) (UG
  pp.31-32). The harness ALWAYS passes `-o`. Canonical example
  (UG pp.26-27): `xa input.sp -o ./OUT -outfilefmt hspice` → `OUT/input.fsdb`,
  `OUT/input.log`, `OUT/input.meas`.
- `-c command_file` — repeatable; commands are processed as if placed on the
  FINAL line of the netlist (command file overrides netlist commands) (UG p.28;
  CR p.14). Optional `xa.ini` auto-read from cwd → $HOME → install dir (UG p.25).
  Some commands (`set_dp_option`, `set_probe_option`) work ONLY from a command
  file (CR p.14). Commands are case-sensitive, Tcl-like (CR pp.14, 17).
- `-mt N|max` multicore (UG p.29); `-wavefmt` (alias `-format`) selects waveform
  format, precedence CLI > `set_waveform_option` > netlist `.opt post=` (UG p.45;
  CR p.276). `set_waveform_option -format` accepts
  `fsdb|out|wdf|tr0|psf|vpd|print` (CR pp.271-277); default output is FSDB
  (UG p.44). `-gz` compresses text outputs (UG p.43 area).
- **No `-param` / `-afile` / include-append CLI mechanism** (UG Table 3 is
  exhaustive for our purposes). Parameter paths are command-file commands
  (`set_parameter_value`, CR p.204) — out of E2 scope.
- Measures: `set_meas_option -format hspice|xa|csv` (CR pp.184-186): default
  `xa` → `.meas` (XA row format); `hspice` → **`.mt`** (HSPICE-compatible —
  note NO digit suffix); `csv` → `.a#.t#.mt.csv`. `-dump_to_log 1` copies
  measures into the log.
- `set_message_option -limit N -pattern "..." -action warn|stop|exit` promotes
  chosen messages; recommended inside the `-c` file (CR pp.186-188).
- License: `SNPSLMD_LICENSE_FILE`/`LM_LICENSE_FILE`; `PRIMESIM=0|1|2` env or
  `-primesim` flag; `PRIMESIM_WAIT_LICENSE*` queueing (UG pp.20-24). FlexLM
  feature names NOT documented.

Status contract (weakest of the three engines):

- **Exit codes NOT documented** anywhere in the four PDFs.
- Log = `<prefix>.log` (UG p.43 Table 6): statistics, runtime, memory, warnings
  and errors. Message grammar: lines start `Error: ` / `Warning: ` (verbatim
  examples UG pp.47, 205, 223, 357).
- **No success banner documented.** Best documented end-of-log marker: the
  runtime report "at the end of the log file", e.g.
  `Total Wall Time = 169331 sec (1day 23hr 2min 11sec)` (UG p.224).
- Progress goes to stderr on a 10 s timer (`XA_STATUS` env; UG p.25); whether
  errors are duplicated to stderr is undocumented.

Outputs (prefix = `-o` value): `.log`; `.meas` (XA row format) / `.mt` (hspice
format) / `.a#.t#.mt.csv` (csv format); `.valog` (Verilog-A); `.errt/.errz/
.hotspot/.power/.rcxt/.err/.hiz`; `.time.ic` (OP, time-stamped e.g.
`xa.1e-07.ic`); waveforms `.fsdb` (default) / `.out` / `.wdf` / `.psf` / `.tr0`
(UG pp.43-45). `.ALTER` → suffix `.a#` STARTING AT 0 (`xa.a0.fsdb`, UG p.54);
`.DATAVAR` sweeps → `.s#`. Monte Carlo is native (transient, HSPICE-format
netlists; `.TRAN ... SWEEP monte=...`, UG p.204): summary `.mc` + samples in the
global `.meas`/`.mt`; per-sample files ONLY when
`set_monte_carlo_option -sample_output ... -dump_waveform ...` is set (default
none — UG pp.207-209, 218). E2 needs no MC code beyond buckets.

## Design decisions (fixed — do not deviate)

1. **XA profile follows the E1 EngineProfile protocol exactly** — new entry in
   `ENGINE_PROFILES` under engine option `"xa"`; `name="xa"`,
   `default_binary="xa"`, `env_binary_var="VB_XA_BIN"`,
   `log_signatures=("Total Wall Time =",)`.
2. **argv** (argv_ctx paths, same absolute/basename convention as E1):
   `[binary]` + (dialect flag: `options["dialect"]` ∈ {"hspice","spectre",
   "eldo"}, default "hspice" which emits NOTHING — hspice is XA's own default;
   "spectre"/"eldo" emit `-spectre`/`-eldo`; invalid → ValueError) +
   `[<netlist>]` (positional) + `["-o", <prefix>]` + `["-mt", str(N)]` if
   threads + `["-wavefmt", fmt]` if waveform_format (validated ∈ {fsdb, out,
   wdf, tr0, psf}) + (safety: `["-c", <aux_ref>]`) + extra_args.
3. **Safety aux file** (when `ctx.safety` and `extra_args` does not already
   contain `-c`): `("psb_xa.cmd", "# injected by primesim-bridge\n"
   "set_meas_option -format hspice\n")` — forces HSPICE-compatible `.mt`
   measures so the existing classic parser applies. **(ASSUMED — G2 checks:
   `.mt` content is `$DATA1`-shaped; `-c` injection order vs a user `-c` in
   extra_args; the `#` comment syntax in command files.)** Suppression:
   `no_safety=True` or a caller-provided `-c` in extra_args.
4. **Validation (ValueError)**: `runlvl`/`mode` ("xa has no accuracy CLI knob"),
   `log_file` ("xa log is always <prefix>.log"), `include_files` non-empty
   ("xa has no CLI include mechanism — use .include/.lib inside the netlist"),
   prefix basename containing "." is ALLOWED (no documented truncation for xa —
   do not copy the hspice rule).
5. **classify** (log-first — exit codes undocumented):
   - nonzero/None returncode → FAILURE `f"exit code {rc}"` (no table; None → 1).
     No signal normalization (no documented signal semantics — raw value).
   - exit 0: log errors present → PARTIAL (generic rule, same as other engines);
     else SUCCESS — and when the `Total Wall Time =` signature is ABSENT, append
     warning `exit 0 but no 'Total Wall Time' end-of-log marker (undocumented
     success proxy)` while KEEPING the status (**success-proxy semantics
     ASSUMED — G2**; weaker than hspice's banner→PARTIAL because XA documents
     no banner at all).
   - `is_parallel_wait` keeps the engine-independent meaning (chain unchanged).
   - No signature promotion (like hspice).
6. **Parsers (additive; state each in a parser test)**: `.mt` WITHOUT digits →
   measure bucket (new — the digitless XA form; `.mt.csv`/`.a#.t#.mt.csv` →
   measure too); `.valog` → other; `.errt/.errz/.hotspot/.power/.rcxt/.err/
   .hiz` → other; `.mc` (digitless) → other (it is a statistics TEXT summary,
   not a measure table; digited `.mc#` stays measure per G0). Existing
   classifications unchanged. The digitless `.mt` participates in `data` via
   the existing sorted-merge (it has no index → per E1's rule it is never an
   "alter" file; it merges into `data` like any measure file).
7. **fake-xa** (`tests/fake_xa.py`, executable 100755, same deck-directive
   grammar; stdlib only): value options exactly `{-o, -c, -mt, -wavefmt,
   -format, -outfilefmt}`; bare flags `{-hspice, -spectre, -eldo, -gz}`
   consumed silently; positional = netlist. `-c` file contents join the deck
   view AFTER the deck (matching "final line of the netlist" semantics —
   directives inside command files work). Without `-o`: prefix = literal `xa`
   in cwd (models the trap; a direct-subprocess test asserts `xa.log` appears
   in cwd — run it inside tmp_path). Writes `<prefix>.log` with neutral lines
   (free of error/warning substrings) + `fake:log` lines + `Error: TEXT` per
   `fake:error=TEXT` + final line `Total Wall Time = 1 sec (0hr 0min 1sec)`
   unless `fake:no_walltime`. Measures: if the deck view contains
   `set_meas_option -format hspice` (case-sensitive commands) → classic
   `$DATA1`-shaped `<prefix>.mt`; else XA-native `<prefix>.meas` whose content
   is `# XA measure row format (not parsed by the bridge)\n<name>: <value>\n`
   lines. Honors `fake:fsdb` (writes `<prefix>.fsdb`), `fake:measure`,
   `fake:rows` (rows apply to the `.mt` shape), `fake:exit`, `fake:sleep`,
   `fake:sleep_first`.
8. **Zero edits to pre-E2 test files/fixtures** (same constraint discipline as
   E1; blocker protocol otherwise).

## Deliverable 1 — XA profile in `src/primesim_bridge/engines.py` (D1-D5)
## Deliverable 2 — parser bucket additions (D6)
## Deliverable 3 — CLI: `--engine` gains `xa`; `--dialect {hspice,spectre,eldo}`
   run-option plumbed through `options["dialect"]` (rejected with SystemExit(2)
   path for non-xa engines via profile ValueError "dialect is only valid for
   engine xa" — add that validation to ALL three profiles: primesim and hspice
   reject a set dialect).
## Deliverable 4 — `tests/fake_xa.py` + tests

`tests/test_engines_xa.py` (unit): argv snapshots (default dialect emits no
flag; spectre dialect; -c injection present/suppressed via no_safety and via
caller `-c`; -wavefmt validation; positional netlist before -o), validation
errors (runlvl/mode/log_file/include_files/dialect-on-primesim/hspice), classify
triple (nonzero → FAILURE "exit code N"; 0+errors → PARTIAL; 0 clean+no
walltime → SUCCESS with the proxy warning; 0 clean+walltime → SUCCESS no
warning).
`tests/test_behavior_xa.py` (real subprocess, decks inline, base tier):
1. success + measures + safety → SUCCESS; `-c psb_xa.cmd` in argv (absolute);
   aux file in run dir; data parsed from classic `.mt`;
   `metadata["engine"] == "xa"`.
2. `no_safety=True` → no `-c`; `.meas` written; measure parse falls back
   (raw_lines/low-confidence path visible in data or absence thereof) — assert
   `data` does NOT contain the measure value, proving the injection is what
   makes XA results machine-readable.
3. `fake:error=simulation aborted` + `fake:exit=2` → FAILURE "exit code 2" +
   the `Error:` line in errors.
4. exit 0 + `fake:no_walltime` → SUCCESS with the proxy warning.
5. exit 0 + `fake:error=...` (no exit directive) → PARTIAL.
6. `fake:fsdb` + `waveview_script: True` → WaveView script generated (engine
   independence).
7. sync LSF wrapper around fake-xa → SUCCESS.
8. remote-mode argv shape (monkeypatched `_exec`): positional netlist basename,
   `-c` by basename, aux uploaded after netlist.
9. no `-o` trap (direct subprocess in tmp cwd): `xa.log` appears in cwd.
10. dialect: `--dialect spectre` dry-run argv contains `-spectre`.

## Deliverable 5 — `docs/NOTES_E2.md`
Built/counts/deviations/(ASSUMED)+G2 checks (mt-shape, -c ordering, comment
syntax, walltime proxy)/owner commands/intended commit message
`feat: E2 — PrimeSim XA engine`.

## Execution after implementation
> Codex: base tier only (`python -m pytest tests/ -q`; pre-E2 tests ZERO edits;
> tiers stay SKIPPED; no network; do NOT commit — sandbox `.git` read-only).
> Owner: fresh venv, all tiers incl. one fake-xa-over-SSH run, skill doc,
> commit/push.

## Constraints
Same as E1: protected files (all docs/SPEC_*, NOTES_G*/E1, pyproject, plugin
manifest, skill, README, LICENSE, tests/fixtures/**), pydantic-only runtime,
py≥3.9 typing rules, `virtuoso_bridge` confined to `_companion.py`, blocker →
`docs/NOTES_E2.md` and stop.

## Acceptance criteria
1. `python -m pytest tests/ -q`: 0 failed; ≤ 4 skipped; collected ≥ 183 + 20;
   pre-E2 test files: zero diff.
2. `primesim-bridge run tb.sp --engine xa --dry-run` (any cwd) prints one line
   with `shlex.split(out) == ["xa", "tb.sp", "-o", "tb", "-c", "psb_xa.cmd"]`.
3. Behavior pair 1 vs 2 proves the `-c` injection is what makes XA measures
   machine-readable.
4. `metadata["engine"] == "xa"` on every xa path; primesim/hspice results
   unchanged (their tests untouched and green).
5. `docs/NOTES_E2.md` per Deliverable 5.
