# SPEC: primesim-bridge E1 — engine-profile refactor + PrimeSim HSPICE engine

> Owner: main session (design) → Codex (implementation). 2026-08-28.
> Context: the bridge currently drives ONE binary (`primesim`, engines spice/pro) via
> `PrimeSimSimulator`. Sites also run PrimeSim HSPICE (`hspice`) and PrimeSim XA (`xa`).
> The three tools differ in argv shape, injection channel, log naming, and — critically —
> status contracts. E1 (this spec) refactors the runner around a pluggable
> **EngineProfile** and adds the `hspice` engine; E2 (later) adds `xa` on the stabilized
> abstraction. HSPICE facts below were extracted from the local "PrimeSim HSPICE
> Y-2026.03" documentation set (SA = User Guide: Basic Simulation and Analysis, 773pp;
> QSG = Quick Start; IN = Installation Notes; page cites are printed pages). Items
> marked **(ASSUMED)** are the only ones not doc-backed — implement as written, list in
> NOTES with their G2 checks. Do not guess HSPICE behavior beyond this sheet.

## Verified HSPICE facts (do not re-litigate; extracted 2026-08-28)

Invocation:

- Binary `hspice` (a platform-dispatch wrapper script; env normally set by sourcing
  `install_dir/hspice/bin/bashrc.meta|cshrc.meta`) (SA p.78; IN p.5).
- Input: `-i <netlist>` (alias `-hspice`); positional netlist also works (SA pp.69, 76;
  QSG p.41). Output root: `-o <prefix>` — ".lis" appended for the listing;
  **without `-o` the ENTIRE listing goes to stdout** (SA p.70) — the harness must
  therefore ALWAYS pass `-o`.
- Threads `-mt N` — "If thread_count is not entered, PrimeSim HSPICE issues an error"
  (SA p.74); `-mp [N]` multi-process for alter/sweep/MC (SA p.74) — E1 exposes only
  `-mt`.
- Waveform format: `-wavefmt fsdb|wdf|psf|tr0` (alias `-format`) (SA p.69). Waveform
  files require `.OPTION POST` in the deck (SA p.65); `.tr0` native format is
  proprietary (SA p.240).
- **Deck injection from the CLI exists**: `-include_first <file>` inserts a file first;
  `-include_last <file>` appends one (SA p.71). There is NO `-param` mechanism.
- No accuracy CLI flag: accuracy is netlist-only (`.option runlvl`, default 5, SA
  pp.233-234). `-hpp` does NOT exist in Y-2026.03 (zero doc hits) — never emit it.
- `-case 0|1` — case sensitivity OFF by default (SA p.72). `-alter_select` is 1-based
  (SA p.77). `-gz` compresses outputs (`.tr0.gz` etc., SA p.242); netlist inputs may be
  `.sp.gz` (SA p.151).
- Trap: `hspice.ini` is auto-read from cwd → $HOME → install dir and silently alters
  runs (SA p.79). Scratch goes to /tmp unless `tmpdir` env set (SA p.56).

Status contract (the strongest of the three engines):

- **Exit codes are documented** (SA p.773; values may be prefixed by the word SIGTERM
  or SIGABRT): 0 "Simulation succeeded"; 1 "Simulation failed due to errors (e.g.
  syntax error, non-convergence)"; 2 no license; 3 Ctrl+\ ; 6 SIGABRT (e.g. out of
  memory); 8 floating-point exception; 11 segfault (also triggered by >1024-char
  paths, SA p.66); 15 UNIX kill; 24 CPU limit; 28 "No space left on device
  (simulation cannot start)"; 38 "Error writing to file (.lis or .tr0) (simulation
  started)"; 99 error during -dp distribution; 101 Ctrl+C.
- **Success banner** in the `.lis`: `***** job concluded ******` (SA p.235; IN p.6:
  banner near the bottom ⇒ simulation successful). Exit code 0 + banner is the
  documented double success check. stdout additionally prints
  `>info: ***** hspice job concluded` (SA p.41).
- **Error lines** start with `**error**` (e.g. `**error** internal timestep too small
  in transient analysis`, `**error** no convergence in operating point`, SA pp.732,
  738). **Warning lines** start with `**warning**` (SA pp.726-727). No abort banner is
  documented — aborts surface via exit codes.
- `.st#` status file: CPU-phase times, option echo, preprocessing check status —
  NOT a completion verdict (SA p.64).
- License: FlexLM feature name **`hspice`** (`lic: Checkout hspice` / `lic: Release
  hspice token(s)` lines in the .lis, IN p.6, SA p.235); env `SNPSLMD_LICENSE_FILE`
  or `LM_LICENSE_FILE`; queueing via `PRIMESIM_WAIT_LICENSE`(=1 default queue) /
  `PRIMESIM_WAIT_LICENSE_TIMEOUT` minutes (SA pp.53-54).

Outputs (with `-o prefix`):

- `.lis` listing; `.st#` status; `.ic#` (from .SAVE); `.tr#/.ac#/.sw#` waveforms
  (# = 0..9999); `.mt#/.ma#/.ms#` measures; `.mc#` MC parameter file; `.pa#` subckt
  cross-listing; `.printtr#` (with `.OPTION LIS_NEW`); `.mpp#` / `.ava.report` MC
  statistics report (SA pp.60-65, 598-601).
- **MEASFORM** (SA pp.217, 221-222): default = multi-line wrapped table;
  `MEASFORM=1` = single space-delimited row; **`MEASFORM=3` = CSV**. A failed measure
  prints the literal `failed` in the value column (SA p.222). The exact FILENAME the
  CSV form uses is not stated beyond "(*.csv)" — **(ASSUMED: `<prefix>.mt#.csv`-like
  or `<prefix>*.csv` beside the prefix; collect both patterns)**.
- `.ALTER` numbering (SA pp.169-170): every alter re-simulates and increments the
  file index; an analysis statement inside an alter ADDS to (not replaces) the
  top-level one, so index counts can exceed the alter count. Measure files carry an
  `alter#` column (SA p.221). Harness rule: collect ALL `.mt#/.ms#/.ma#`, not just 0.
- Monte Carlo (SA Ch.24-26): rides `.TRAN/.DC/.AC ... SWEEP MONTE=...`; one row per
  sample (with index) lands in the NORMAL `.mt#` — i.e. our existing multi-row
  measure handling is the MC collection path; `.mc#` holds parameter samples;
  `.mpp#`/`.ava.report` holds statistics. E1 needs no MC-specific code beyond
  collecting those files into buckets.

## Design decisions (fixed — do not deviate)

1. **EngineProfile abstraction, behavior-preserving refactor.** New module
   `src/primesim_bridge/engines.py` defines:
   ```python
   @dataclass(frozen=True)
   class EngineContext:  # everything a profile may consult
       netlist: Path; prefix: Path; binary: str
       options: Mapping[str, Any]      # the run_simulation options dict
       extra_args: tuple[str, ...]; threads: Optional[int]
       waveform_format: Optional[str]; log_file: Optional[Path]
       safety: bool                    # False when the caller suppressed injection

   class EngineProfile(Protocol):
       name: str
       default_binary: str
       def build_argv(self, ctx: EngineContext) -> list[str]: ...
       def aux_files(self, ctx: EngineContext) -> list[tuple[str, str]]: ...
           # (filename, content) written into the run dir BEFORE execution and
           # staged to the remote run dir in remote mode; argv may reference them
           # by BASENAME (they sit next to the cwd the command runs in)
       def log_path(self, ctx: EngineContext) -> Path: ...
       def classify(self, returncode: int, log: dict, has_artifacts: bool,
                    ctx: EngineContext) -> tuple[ExecutionStatus, list[str]]: ...
           # log = the parse_log dict; returns (status, extra_errors)
   ```
   plus `ENGINE_PROFILES: dict[str, EngineProfile]` and `get_profile(name)`.
   The `primesim` profile (covering engine options "spice"/"pro") is EXTRACTED from
   the current runner logic with **behavior identical**: every existing test passes
   UNCHANGED (the only permitted edit class in existing test files: adding the new
   additive `metadata["engine"]` expectation where a test compares a full metadata
   dict — do not touch anything else).
2. **Engine selection.** `options["engine"]` now accepts `"spice"|"pro"|"hspice"`.
   "spice"/"pro" resolve to the `primesim` profile exactly as today (including
   `-spice` default and runlvl/mode validation). `"hspice"` resolves to the new
   profile. Unknown engine → ValueError listing valid names. `runlvl`/`mode` with
   engine="hspice" → ValueError explaining accuracy is netlist-only for HSPICE.
   CLI `--engine` gains `hspice`. `SimulationResult.metadata["engine"]` carries the
   profile name on every path (including early returns).
3. **HSPICE profile.**
   - argv: `[binary, "-i", netlist, "-o", prefix]` + `["-mt", N]` if threads +
     `["-wavefmt", fmt]` if waveform_format (validated ∈ {fsdb, wdf, psf, tr0}) +
     safety injection + extra_args. ALWAYS passes `-o` (stdout trap).
   - Safety injection (when not suppressed): aux file `psb_hspice_options.sp` with
     content `* injected by primesim-bridge\n.option measform=3\n`, referenced as
     `["-include_first", "psb_hspice_options.sp"]`. Suppression rule: skipped when
     `extra_args` already contains `-include_first` OR the caller passes
     `options["no_safety"] = True` (new generic option; the primesim profile maps it
     to skipping its `-aopt` pair as well — existing per-`-aopt` suppression is
     unchanged). Whether an option file injected via -include_first reliably sets
     MEASFORM is **(ASSUMED — G2 check #1)**.
   - log_path: `<prefix>.lis` (a `log_file` option is REJECTED for hspice with a
     ValueError — HSPICE has no log-name flag).
   - classify (exit-code-first with banner double-check):
     `EXIT_CODES_HSPICE = {0,1,2,3,6,8,11,15,24,28,38,99,101}` with the documented
     meanings. Nonzero → FAILURE with the mapped meaning (unlisted → `exit code N`).
     Exit 0 + banner `***** job concluded` present in the log text → SUCCESS
     (PARTIAL if log errors non-empty, mirroring the generic rule). Exit 0 WITHOUT
     the banner → PARTIAL with warning `"exit 0 but no 'job concluded' banner in
     .lis"` **(banner-absence semantics ASSUMED — G2 check #2)**.
     `is_parallel_wait=True` keeps the same meaning as today (log-first; the LSF
     wrapper case is engine-independent).
   - parse_log already collects `**error**`/`**warning**` lines via the generic
     "error"/"warning" substring rule — no parser change needed; the banner check
     reads the raw log text (profile receives it via the log dict — add a
     `"text_head_tail"` or reuse `signatures`: implement by extending parse_log with
     an optional `extra_signatures: list[str]` parameter the runner passes per
     profile (`["***** job concluded"]` for hspice) so matched lines land in
     `signatures`; default empty keeps current behavior).
4. **collect_outputs additions** (generic, engine-safe): `.lis` → `log` bucket;
   `.st#` → `other`; `.printtr#` → `print`; `.mpp#` and `.ava.report` → `other`;
   `.pa#` → `other`; measure CSV twins per the (ASSUMED) naming: any `.mt#.csv` /
   `.ms#.csv` / `.ma#.csv` → `measure` (bare `<prefix>.csv` → `measure` as well).
   Existing classifications unchanged.
5. **Measure aggregation across alters** (generic): `data` comes from the
   lowest-index measure file (e.g. `.mt0` / its csv twin) exactly as today; when
   ADDITIONAL measure files exist (`.mt1`, ...), parse each and attach under
   `metadata["alter_measures"] = {"<filename>": {…}}` (parsed dicts, `_warnings`
   drained into result warnings). No cross-file merging.
6. **fake-hspice** (`tests/fake_hspice.py`): a second dependency-free executable test
   double, reusing the G1 deck-directive grammar (`* fake:key[=value]`), modeling the
   HSPICE contract: reads `-i`/positional netlist, `-o` prefix (REQUIRED — if absent,
   print the deck listing to stdout and exit 0 WITHOUT writing files, modeling the
   stdout trap), `-mt`, `-wavefmt`, `-include_first`/`-include_last` (their contents
   join the deck view — directives inside them work). Writes `<prefix>.lis` with
   neutral lines (same no-"error"/"warning"-substring rule), fake:log lines, and —
   unless `fake:no_banner` — the final line `***** job concluded ******`. Measures:
   `measform=3` present in the deck view → CSV `<prefix>.mt0.csv`; else classic
   `$DATA1`-shaped `<prefix>.mt0`. Error modeling: `fake:error=TEXT` appends
   `**error** TEXT` to the .lis; `fake:exit=N` sets the exit code. Also writes
   `<prefix>.st0` (two neutral lines) and honors `fake:fsdb` / `fake:rows` /
   `fake:measure` / `fake:sleep` / `fake:sleep_first` as in fake_primesim.
7. **Test tiers unchanged** (base/companion/live-SSH). New behavior tests run the
   fake-hspice double through the REAL subprocess path.

## Deliverable 1 — `src/primesim_bridge/engines.py`

As per Design decision 1: `EngineContext`, `EngineProfile` protocol, the `primesim`
profile (extracted, behavior-identical), the `hspice` profile (Design decision 3),
`ENGINE_PROFILES`, `get_profile`. Pure logic only — no subprocess, no filesystem
writes except through the runner's aux-file mechanism.

## Deliverable 2 — runner refactor (`src/primesim_bridge/runner.py`)

- `run_simulation` resolves the profile from `options["engine"]` (default "spice" →
  primesim profile) and delegates argv construction, aux-file emission, log-path
  resolution, and classification to it. Aux files are written into the run directory
  before execution (local) and staged with the netlist (remote, both transports;
  they ride the existing include-staging mechanism — aux files are NOT `-afile`'d
  for hspice, they are referenced by their own flags from `build_argv`).
  ⚠ Aux-file argv references are by basename, and the remote command already `cd`s
  into the run dir; locally, `_exec` must therefore run with `cwd=<run_dir>`
  (this is a CHANGE: today netlist paths are passed as given — keep passing the
  netlist path ABSOLUTE so the cwd change cannot break resolution; assert in tests).
- `metadata["engine"]` set at every `_finish_result` call site (extend the
  `transport` mechanism added in G1).
- Everything else (transport selection, timeout, uniquification, tier-B, waveview,
  LSF hint) is engine-independent and must keep working for hspice runs unchanged.

## Deliverable 3 — CLI (`src/primesim_bridge/cli.py`)

`--engine` gains `hspice`; `--runlvl`/`--mode` with `--engine hspice` exit 2 with the
profile's ValueError message; `--log` with hspice likewise. `run --dry-run` works for
hspice (prints argv incl. the `-include_first psb_hspice_options.sp` injection —
dry-run still touches NO filesystem: the aux file is not written, only referenced).

## Deliverable 4 — parsers (`src/primesim_bridge/parsers.py`)

Design decisions 4-5: bucket additions, `extra_signatures` parameter on
`parse_log`/`parse_log_text` (default `[]`), alter-measure aggregation helper if
needed. Existing behavior unchanged when the new inputs are absent.

## Deliverable 5 — `tests/fake_hspice.py` + behavior tests (`tests/test_behavior_hspice.py`)

fake-hspice per Design decision 6 (executable, mode 100755, conftest chmod fixture
extended to cover it + an executability test). Scenarios (real subprocess, base tier):
1. success + 2 measures + measform injection → SUCCESS; `data` parsed from
   `.mt0.csv`; `metadata["engine"] == "hspice"`; argv contains
   `-include_first psb_hspice_options.sp`; the aux file exists in the run dir.
2. `no_safety=True` → no `-include_first` in argv; classic `$DATA1` `.mt0` written
   and parsed to the same data.
3. `fake:error=no convergence in operating point` + `fake:exit=1` → FAILURE;
   errors contain the documented meaning for exit 1 AND the `**error**` line.
4. `fake:no_banner` (exit 0) → PARTIAL with the banner warning.
5. exit 2 → FAILURE "no license".
6. runlvl/mode/log_file with engine hspice → ValueError (unit level, not subprocess).
7. alter files: deck directive `fake:alter_measures=K` makes the fake ALSO write
   `<prefix>.mt1..mtK` (classic shape, one synthetic measure each) →
   `metadata["alter_measures"]` carries them; `data` still from `.mt0.csv`.
   (Add this directive to fake-hspice only.)
8. `fake:fsdb` + `waveview_script: True` → WaveView handoff works for an hspice run
   (script generated; proves engine-independence of the G-features).
9. LSF wrapper reuse: sync wrapper around fake-hspice → SUCCESS (one scenario,
   proving binary-substitution works for the new engine).
10. Existing fake_primesim scenarios keep passing UNTOUCHED.
Plus unit tests for the hspice profile: argv snapshots, EXIT_CODES_HSPICE table
(`set == {0,1,2,3,6,8,11,15,24,28,38,99,101}`), banner classification triple
(0+banner=SUCCESS, 0+banner+log-errors=PARTIAL, 0-no-banner=PARTIAL), waveform-format
validation, and a primesim-profile regression pin: `get_profile("spice")` argv equals
the pre-refactor `build_primesim_argv` output for a fixed context.

## Execution after implementation

> Codex runs the base tier only (`python -m pytest tests/ -q` in an env that already
> has pytest+pydantic; fresh-venv install is the owner's check). Companion/live-SSH
> tiers stay skipped. The fake-hspice subprocess tests must pass in the sandbox.
> Owner session afterwards: fresh venv, full tiers (companion + live-SSH vs a cluster
> host — including one fake-hspice-over-SSH run), packaging check, skill doc update,
> commit/push. State the (ASSUMED) items + G2 checks in NOTES_E1
> (measform-via-include_first; banner-absence semantics; CSV filename pattern).

## Constraints

- Do NOT modify: `.claude-plugin/plugin.json`, `pyproject.toml`,
  `skills/primesim/SKILL.md`, `README.md`, `LICENSE`, docs/SPEC_*.md, docs/NOTES_G*.md.
- Runtime dependency stays `pydantic` only. Python ≥ 3.9; the pydantic/PEP-604 field
  rule applies (typing.Optional in pydantic fields; `from __future__ import
  annotations` elsewhere).
- **Existing tests pass UNCHANGED** except the single additive edit class from Design
  decision 1 (adding `"engine"` to full-metadata comparisons). Weakening any
  assertion is forbidden. The refactor is behavior-preserving for primesim runs.
- The string `virtuoso_bridge` stays confined to `_companion.py`.
- ONE commit on `master`: `feat: E1 — engine-profile refactor + PrimeSim HSPICE engine`.
  Do not push. (Sandbox `.git` read-only → record in NOTES and stop; owner commits.)
- Blocker protocol: `docs/NOTES_E1.md`, then stop. No improvised workarounds.

## Acceptance criteria

1. `python -m pytest tests/ -q`: all non-skipped green; total collected ≥ 134 + 25;
   the pre-E1 test files show no diff except the sanctioned additive metadata edits.
2. `primesim-bridge run tb.sp --engine hspice --dry-run` prints one shlex line:
   `hspice -i .../tb.sp -o ... -include_first psb_hspice_options.sp` (order per
   Design decision 3), touching no filesystem.
3. Scenario 3's result carries BOTH the exit-code meaning and the `**error**` line;
   scenario 4 yields PARTIAL purely from banner absence.
4. `set(EXIT_CODES_HSPICE) == {0,1,2,3,6,8,11,15,24,28,38,99,101}`; the primesim
   exit table and its criterion from G0 are untouched.
5. `metadata["engine"]` present on every result (both engines, all paths incl.
   binary-missing).
6. `docs/NOTES_E1.md`: what was built, per-tier counts, deviations + reasons, the
   (ASSUMED) list with G2 checks, and the owner-session commands.
