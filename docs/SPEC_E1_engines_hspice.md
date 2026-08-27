# SPEC: primesim-bridge E1 — engine-profile refactor + PrimeSim HSPICE engine

> Owner: main session (design) → Codex (implementation). 2026-08-28, **v2**
> (v1 adversarially reviewed against the real runner/parsers/cli/tests and the HSPICE
> guide text; 34 defects fixed — including a fact-sheet correction: without
> `.OPTION MEASFAIL` a failed HSPICE measure silently becomes `0.0e0`).
> Context: the bridge drives ONE binary (`primesim`, engines spice/pro) via
> `PrimeSimSimulator`. E1 refactors the runner around a pluggable **EngineProfile**
> and adds the `hspice` engine; E2 (later) adds `xa`. HSPICE facts cite the local
> "PrimeSim HSPICE Y-2026.03" docs (SA = User Guide: Basic Simulation and Analysis;
> QSG; IN = Installation Notes; printed pages). **(ASSUMED)** items are the only
> non-doc-backed ones — implement as written, list in NOTES_E1 with G2 checks.
> Do not guess HSPICE behavior beyond this sheet.

## Verified HSPICE facts (do not re-litigate; extracted + review-verified 2026-08-28)

Invocation:

- Binary `hspice` (platform-dispatch wrapper; env via `install_dir/hspice/bin/
  bashrc.meta|cshrc.meta`) (SA p.78; IN p.5).
- Input `-i <netlist>` (alias `-hspice`); positional also works (SA pp.69, 76).
  Output root `-o <prefix>`: ".lis" appended for the listing; **without `-o` the
  ENTIRE listing goes to stdout** (SA p.70) — the harness ALWAYS passes `-o`.
  ⚠ Root-name truncation: "Everything up to the LAST period is the root file name"
  (SA p.70 region) — a prefix whose basename contains `.` makes HSPICE write to a
  truncated root and the harness would collect nothing.
- `-mt N` (count REQUIRED — "issues an error" without it, SA p.74). `-wavefmt
  fsdb|wdf|psf|tr0` (alias `-format`) (SA p.69); waveform files also need
  `.OPTION POST` in the deck (SA p.65).
- **CLI deck injection exists**: `-include_first <file>` inserts a file first;
  `-include_last <file>` appends (SA p.71). No `-param` mechanism.
- No accuracy CLI flag (`.option runlvl` is netlist-only, default 5, SA pp.233-234).
  `-hpp` does NOT exist in Y-2026.03 — never emit it. `-case 0|1` (default 0,
  SA p.72). `-alter_select` is 1-based (SA p.77). `-gz` compresses outputs.
  Trap: `hspice.ini` auto-read from cwd → $HOME → install dir (SA p.79).

Status contract:

- **Exit codes documented** (SA p.773; numerals "may be preceded by the word SIGTERM
  or SIGABRT"): 0 succeeded; 1 "Simulation failed due to errors, example, syntax
  error, non-convergence"; 2 no license; 3 Ctrl+\ ; 6 SIGABRT; 8 floating-point
  exception; 11 segfault; 15 UNIX kill; 24 CPU limit; 28 no space left (cannot
  start); 38 error writing .lis/.tr0 (started); 99 -dp distribution error;
  101 Ctrl+C. ⚠ Codes 3/6/8/11/15 are SIGNALS: `subprocess` reports them as
  NEGATIVE returncodes, and shell/LSF wrappers as `128+N` — normalize before lookup.
- **Success banner** in `.lis`: `***** job concluded` (full line
  `***** job concluded ******`, SA p.235; IN p.6). stdout prints
  `>info: ***** hspice job concluded` (SA p.41). Exit 0 + banner = documented
  double success check. No abort banner documented — aborts surface via exit codes.
- Error lines start `**error**`; warning lines start `**warning**` (SA pp.726-738).
- **Failed measures (v2 correction)**: by DEFAULT a non-executing `.MEASURE` writes
  `0.0e0` into the `.mt#` and `FAILED` only into the `.lis`; **`.OPTION MEASFAIL`
  is required to write failure markers into the measure files** (SA p.213). The
  literal `failed` cell (SA p.222) appears only under that option family.
- License: FlexLM feature **`hspice`** (`lic: Checkout hspice` / `lic: Release
  hspice token(s)` in the .lis, IN p.6, SA p.235); `SNPSLMD_LICENSE_FILE` /
  `LM_LICENSE_FILE`; queueing `PRIMESIM_WAIT_LICENSE(_TIMEOUT)` (SA pp.53-54).

Outputs (with `-o prefix`): `.lis`; `.st#` (status, not a verdict, SA p.64); `.ic#`;
`.tr#/.ac#/.sw#` (# = 0..9999); `.mt#/.ma#/.ms#` measures; `.mc#` MC parameters;
`.pa#` subckt cross-listing; `.printtr#` (with `.OPTION LIS_NEW`); `.mpp#` /
`.ava.report` MC statistics (SA pp.60-65, 598-601). **MEASFORM** (SA pp.217-222):
default multi-line table; 1 = single space-delimited row; **3 = CSV "(*.csv)"** —
exact CSV filename **(ASSUMED: `.mt#.csv`-style twins and/or bare `<prefix>.csv`;
collect both)**. `.ALTER`: each alter re-simulates and increments file indices; an
analysis inside an alter ADDS to the top-level one (SA pp.169-170); measure files
carry an `alter#` column (SA p.221). Monte Carlo rides `SWEEP MONTE=` and lands one
row per sample in the NORMAL `.mt#` (SA Ch.24-26) — multi-row measure handling IS
the MC collection path; no MC-specific code needed in E1.

## Design decisions (fixed — do not deviate)

1. **EngineProfile abstraction, behavior-preserving.** New
   `src/primesim_bridge/engines.py` (starts with `from __future__ import
   annotations`):
   ```python
   @dataclass(frozen=True)
   class EngineContext:
       netlist: Path            # as it must appear in argv for this mode
       prefix: Path             # ditto
       binary: str
       options: Mapping[str, Any]   # stored as MappingProxyType(dict(...))
       extra_args: tuple[str, ...]
       include_files: tuple[Path, ...]  # as they must appear in argv
       threads: Optional[int]
       waveform_format: Optional[str]
       log_file: Optional[Path]
       safety: bool             # False iff options.get("no_safety") is True

   class EngineProfile(Protocol):
       name: str
       default_binary: str
       env_binary_var: str              # "VB_PRIMESIM_BIN" / "VB_HSPICE_BIN"
       log_signatures: tuple[str, ...]  # extra signature strings for parse_log
       def build_argv(self, ctx: EngineContext) -> list[str]: ...
       def aux_files(self, ctx: EngineContext) -> list[tuple[str, str]]: ...
       def log_path(self, ctx: EngineContext) -> Path: ...
       def classify(self, returncode: Optional[int], log: Mapping[str, list],
                    has_artifacts: bool, ctx: EngineContext
                    ) -> tuple[ExecutionStatus, list[str], list[str]]: ...
   ```
   `ENGINE_PROFILES` + `get_profile(engine_option)` ("spice"/"pro" → primesim
   profile; "hspice" → hspice profile; unknown → ValueError naming valid values).
   **TWO contexts per run**: `argv_ctx` (paths as the execution host must see them:
   absolute in local mode, basenames in remote mode — exactly today's convention)
   and `local_ctx` (absolute local paths) — `build_argv` gets `argv_ctx`;
   `aux_files`/`log_path`/`classify` get `local_ctx`.
2. **classify contract.** Returns `(status, extra_errors, extra_warnings)`. It is
   consulted ONLY in the final else-branch of `_finish_result` — the
   engine-independent chain (timeout_error → forced_error → is_parallel_wait)
   is UNCHANGED and runs first. `returncode` may be None; profiles treat None as
   effective code 1 (current behavior). The primesim profile's classify reproduces
   today's `classify_exit` + exit-0/log-errors logic EXACTLY; the DC-signature
   promotion (`runner.py` `dc_signature`/`dc_safety_injected` block) MOVES into the
   primesim profile's classify (hspice does no signature promotion) — outputs
   byte-identical for primesim runs.
3. **Binary resolution.** `PrimeSimSimulator(binary: Optional[str] = None)`;
   resolved per-run AFTER the profile is known: explicit ctor/CLI `--binary` (new
   flag) → `os.environ[profile.env_binary_var]` → `profile.default_binary`.
   Binary-missing error string becomes `f"{profile.name} executable not found:
   {binary}"` — for primesim this stays literally `primesim executable not found:
   ...` (the string pinned by an existing test). `from_env` keeps reading
   `VB_PRIMESIM_BIN` into overrides ONLY when set (unchanged); `VB_HSPICE_BIN` is
   read by resolution, not by from_env.
4. **Safety semantics.** `ctx.safety = not options.get("no_safety", False)`
   (truthy-True only). Primesim per-`-aopt` suppression is UNCHANGED and lives in
   the primesim profile; additionally `no_safety=True` disables both `-aopt`
   injections. The runner computes
   `dc_safety_injected = ctx.safety and not _has_aopt(extra_args,
   "primesim_exit_dc_fail")` and passes it into the primesim profile via the
   context options (implementation detail: profile recomputes it identically —
   either way, add the test: `no_safety=True` + DC log line → SUCCESS-with-warning,
   matching the existing suppressed case).
5. **HSPICE profile.**
   - argv: `[binary, "-i", <netlist>, "-o", <prefix>]` + `["-mt", str(N)]` if
     threads + `["-wavefmt", fmt]` if waveform_format (validated ∈ {fsdb, wdf,
     psf, tr0}) + (safety: `["-include_first", <aux_ref>]`) +
     (per include file: `["-include_last", <path>]`) + extra_args.
     `<aux_ref>` and include/netlist/prefix paths follow the ctx mode (absolute
     local / basename remote). Safety suppressed when `extra_args` already
     contains `-include_first` or `no_safety=True`.
   - aux_files: `[("psb_hspice_options.sp", "* injected by primesim-bridge\n"
     ".option measform=3\n.option measfail=1\n")]` when safety on, else `[]`.
     **(ASSUMED — G2 checks: measform/measfail effective via -include_first; exact
     measfail spelling/value.)**
   - Validation (ValueError): `runlvl`/`mode` set ("accuracy is netlist-only for
     hspice: use .option runlvl"), `log_file` set ("hspice has no log-name flag —
     the listing is <prefix>.lis"), prefix basename containing "." ("hspice
     truncates the output root at the last period — choose a dot-free prefix").
   - log_path: `<prefix>.lis`. log_signatures: `("***** job concluded",)`.
   - classify: normalize `code = -rc if rc < 0 else (rc - 128 if 128 < rc < 160
     else rc)` (raw rc stays in metadata); `EXIT_CODES_HSPICE` lookup — nonzero →
     FAILURE with the mapped meaning (unlisted → f"exit code {code}"). Code 0:
     banner signature present → SUCCESS (PARTIAL if log errors non-empty, same
     generic rule as primesim); banner ABSENT → PARTIAL with warning
     `exit 0 but no 'job concluded' banner in .lis` **(banner-absence semantics
     ASSUMED — G2)**.
6. **EXIT_CODES_HSPICE lives in `models.py`** beside `EXIT_CODE_TABLE`, with
   `classify_exit_hspice(code)` twin. Exact strings (tests compare against the
   dict, never re-typed literals):
   ```python
   EXIT_CODES_HSPICE = {
       0: "Simulation succeeded", 1: "Simulation failed due to errors",
       2: "PrimeSim HSPICE stopped due to lack of license",
       3: "Interrupted (Ctrl+\\)", 6: "Aborted (SIGABRT, e.g. out of memory)",
       8: "Floating-point exception", 11: "Segmentation fault",
       15: "Terminated (UNIX kill)", 24: "CPU time limit exceeded",
       28: "No space left on device (simulation cannot start)",
       38: "Error writing to output file (simulation started)",
       99: "Error during -dp distribution", 101: "Interrupted (Ctrl+C)",
   }
   ```
7. **Aux-file mechanics (no cwd change — `_exec` signature and cwd behavior are
   UNTOUCHED).** Aux files are written into the local `run_dir` before execution in
   BOTH modes. Local argv references them ABSOLUTELY; remote argv by basename (the
   remote command already `cd`s into the run dir). Remote staging: aux files are
   appended to the upload list AFTER netlist and include_files, in BOTH transports.
   The primesim profile returns `aux_files() == []`, so primesim `remote_cmds`
   stay byte-identical to today (guarded by the existing remote-sequence test).
   Dry-run (CLI) does NOT write aux files — it only references the basename.
8. **include_files × engines.** `EngineContext.include_files` carries them; the
   runner keeps transport-level staging for all engines (copy local / upload
   remote — unchanged code); the ARGV reference is the profile's job: primesim
   emits `-afile <path>` per file exactly as today; hspice emits
   `-include_last <path>` per file.
9. **Parsers (additive).** `parse_log`/`parse_log_text` gain
   `extra_signatures: Optional[Sequence[str]] = None`: a case-insensitive
   SUBSTRING hit appends the SIGNATURE STRING ITSELF to `signatures` (not the
   line; the line is not added to errors/warnings) — mirroring the existing
   DC/divergence handling. `_parse_artifacts` gains the parameter, supplied from
   `profile.log_signatures`; `cli parse` passes none (documented limitation in
   NOTES). Bucket additions/changes (state in a parser test):
   `.lis` → log; `.st#` → other; `.printtr#` → print (NEW — previously other);
   `.mpp#`, `.ava.report` → other; `ms` added to the measure regex (`.ms#` now
   flows into measures — NEW); measure `.csv` twins: `.mt#.csv/.ms#.csv/.ma#.csv`
   and bare `<prefix>.csv` → measure. `.pa#` STAYS in print (unchanged).
10. **Measure aggregation.** `data` continues to merge ALL measure files in sorted
    order EXACTLY as today (no lowest-index rule — that was v1's error).
    ADDITIVE: files whose index (the integer in the LAST `\.(mt|ma|ms|md|mc)(\d+)`
    group of the basename; index-less files excluded) exceeds the minimum index
    also get their parsed dict under `metadata["alter_measures"][<basename>]`,
    with `_warnings` drained into result warnings and `_rows` moved to
    `metadata["alter_rows"][<basename>]`. `_parse_artifacts` returns a 6-tuple.
    `cli parse` gains the same aggregation (+1 CLI test).
11. **metadata.** `_finish_result` gains a required `engine: str` parameter (the
    PROFILE name: `"primesim"` for spice/pro, `"hspice"`), emitted alongside
    `transport` — one call site covers every path including binary-missing. Pin
    BOTH values in tests.
12. **CLI.** `--engine` gains `hspice`; new `--binary` flag (all engines).
    `_run` wraps argv-build AND `run_simulation` in the ValueError handler,
    printing the message to stderr and raising `SystemExit(2)`. Tests assert
    `.value.code == 2` + stderr message for `--engine hspice` × each of
    `--runlvl/--mode/--log`. Dry-run prints paths AS TYPED (no resolution).
13. **fake-hspice** (`tests/fake_hspice.py`, executable 100755, conftest chmod
    fixture extended + executability test). Reuses the deck-directive grammar.
    Arg parsing: value-taking options are exactly `{-i,-o,-mt,-wavefmt,-format,
    -include_first,-include_last,-case,-n,-alter_select}`; `-include_first`
    content joins the deck view BEFORE the deck, `-include_last` AFTER (directives
    inside them work) — plus a direct unit test of this parser. Behavior:
    without `-o` → print the deck view to stdout, write NOTHING, exit 0 (stdout
    trap). With `-o`: write `<prefix>.lis` (neutral lines free of the substrings
    error/warning; fake:log lines; `**error** TEXT` per `fake:error=TEXT`; final
    line `***** job concluded ******` unless `fake:no_banner`), `<prefix>.st0`
    (two neutral lines), `<prefix>.ic0` (empty). Measures: measform-3 detection is
    `re.search(r"measform\s*=\s*3", view, re.IGNORECASE)` → CSV `<prefix>.mt0.csv`
    else classic `$DATA1` `<prefix>.mt0`; `fake:measure_failed=NAME` emits the
    literal `failed` cell for NAME (both shapes); `fake:alter_measures=K` writes
    `.mt1..mtK` IN THE SAME FORMAT as `.mt0` (csv → `.mt1.csv` …), one synthetic
    measure each. Honors `fake:fsdb`, `fake:rows`, `fake:measure`, `fake:exit`,
    `fake:sleep`, `fake:sleep_first` as in fake_primesim.

## Deliverable 1 — `src/primesim_bridge/engines.py`
Per Design decisions 1-8: contexts, protocol, primesim profile (extracted,
behavior-identical — including the moved DC promotion), hspice profile, registry.
Pure logic; no subprocess/filesystem.

## Deliverable 2 — runner refactor (`src/primesim_bridge/runner.py`)
Profile resolution + binary resolution (D3); two-context construction (D1);
aux-file writing/staging (D7); include-argv delegation (D8); classify integration
(D2); `metadata["engine"]` (D11). Transport selection, timeout, uniquification,
tier-B, waveview, LSF hint stay engine-independent and work for hspice unchanged.

## Deliverable 3 — CLI (`src/primesim_bridge/cli.py`)
Per D12.

## Deliverable 4 — parsers (`src/primesim_bridge/parsers.py`)
Per D9-D10.

## Deliverable 5 — fake-hspice + tests
`tests/fake_hspice.py` (D13), `tests/test_engines.py` (unit: argv snapshots both
engines; primesim-profile regression pin — `get_profile("spice")` argv equals the
pre-refactor `build_primesim_argv` output for a fixed context; EXIT_CODES_HSPICE
set == {0,1,2,3,6,8,11,15,24,28,38,99,101}; signal normalization: -11 and 139 both
map to the segfault meaning; banner triple: 0+banner→SUCCESS, 0+banner+log-errors→
PARTIAL, 0-no-banner→PARTIAL+warning; waveform-format validation; prefix-with-dot
ValueError; None returncode), `tests/test_behavior_hspice.py` (real subprocess,
decks written INLINE in the test module — the test_lsf_pattern._deck pattern; NO
new files under tests/fixtures/, so MANIFEST is untouched):
1. success + 2 measures + safety → SUCCESS; data from `.mt0.csv`;
   `metadata["engine"] == "hspice"`; argv contains `-include_first` with the aux
   ABSOLUTE path; aux file exists in run dir with measform AND measfail lines.
2. `no_safety=True` → no `-include_first`; classic `$DATA1` `.mt0` parsed to the
   same data.
3. `fake:error=no convergence in operating point` + `fake:exit=1` → FAILURE;
   errors contain EXIT_CODES_HSPICE[1] AND the `**error**` line.
4. `fake:no_banner` (exit 0) → PARTIAL with the banner warning.
5. `fake:exit=2` → FAILURE with EXIT_CODES_HSPICE[2].
6. `fake:measure_failed=vout` → `data["vout"] is None` + failed-measure warning
   (both measform shapes via parametrize with/without no_safety).
7. `fake:alter_measures=2` → `metadata["alter_measures"]` keyed by basenames in
   the same format family as `.mt0`; `data` merged as before.
8. include staging: a directive inside an `-include_last`-passed
   `options["include_files"]` file takes effect (proves D8 for hspice).
9. `fake:fsdb` + `waveview_script: True` → WaveView script generated for an
   hspice run.
10. sync LSF wrapper around fake-hspice → SUCCESS (binary substitution).
11. stdout trap (direct subprocess, no runner): `[sys.executable, fake_hspice,
    "-i", deck]` → rc 0, stdout contains deck text, no `.lis` beside the deck.
12. remote-mode argv shape for hspice (monkeypatched `_exec`): basenames in the
    remote command, aux file in the upload list after netlist+includes.
All existing fake_primesim/companion/live tests pass UNTOUCHED.

## Deliverable 6 — `docs/NOTES_E1.md`
What was built; per-tier counts (paste the pytest summary line); deviations +
reasons; the (ASSUMED) list with G2 checks (measform/measfail via -include_first;
banner-absence semantics; CSV filename pattern); owner-session commands; the
intended commit message verbatim (see Constraints).

## Execution after implementation
> Codex runs the base tier only (`python -m pytest tests/ -q` in an env already
> holding pytest+pydantic; fresh venv is the owner's check). Companion/live-SSH
> tiers stay skipped. fake-hspice subprocess tests must pass in the sandbox.
> Owner session afterwards: fresh venv; full tiers incl. one fake-hspice-over-SSH
> run; packaging; skill doc update; commit/push.

## Constraints
- Do NOT modify: `.claude-plugin/plugin.json`, `pyproject.toml`,
  `skills/primesim/SKILL.md`, `README.md`, `LICENSE`, docs/SPEC_*.md,
  docs/NOTES_G*.md, `tests/fixtures/**` (incl. MANIFEST.md).
- **Pre-E1 test files: ZERO edits.** If any existing test cannot pass unmodified,
  that is a BLOCKER → NOTES_E1 and stop. (The refactor is behavior-preserving for
  primesim runs; `_exec` keeps its exact signature and cwd behavior.)
- Runtime dep `pydantic` only; Python ≥ 3.9 (typing.Optional in pydantic fields;
  `from __future__ import annotations` elsewhere). `virtuoso_bridge` stays
  confined to `_companion.py`.
- **Do NOT commit** (sandbox `.git` is read-only): record the intended message
  `feat: E1 — engine-profile refactor + PrimeSim HSPICE engine` in NOTES_E1;
  the owner commits.
- Blocker protocol: `docs/NOTES_E1.md`, then stop. No improvised workarounds.

## Acceptance criteria
1. `python -m pytest tests/ -q`: 0 failed; ≤ 4 skipped (the same tiers); total
   collected ≥ 160 (138 today + ≥ 22 new). Paste the summary line in NOTES_E1.
2. `primesim-bridge run tb.sp --engine hspice --dry-run` (any cwd) prints one
   line with `shlex.split(out) == ["hspice", "-i", "tb.sp", "-o", "tb",
   "-include_first", "psb_hspice_options.sp"]`; no filesystem writes.
3. Scenario 3 carries BOTH the exit-code meaning and the `**error**` line;
   scenario 4 yields PARTIAL purely from banner absence; scenario 6 proves the
   failed-measure path is alive under the injected options.
4. `set(EXIT_CODES_HSPICE) == {0,1,2,3,6,8,11,15,24,28,38,99,101}`;
   `classify_exit_hspice(-11)` and `(139)` both yield the segfault meaning;
   the G0 primesim exit-table criterion is untouched.
5. `metadata["engine"]` present on every result: `"primesim"` (spice AND pro
   runs) / `"hspice"`, including the binary-missing path; the primesim
   binary-missing error string is unchanged.
6. `git diff --stat` shows no changes to pre-E1 test files or fixtures.
