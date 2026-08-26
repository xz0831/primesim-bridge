# SPEC: primesim-bridge G0 core — runner, tier-A parsers, CLI (offline-verified)

> Owner: main session (design) → Codex (implementation). 2026-08-21, **v2** (v1 was
> adversarially reviewed against the source PDF and the scaffold; 47 defects fixed —
> every fact below survived that review or was re-verified against the primary source).
> Context: `primesim-bridge` is a new standalone Python package + Claude Code plugin,
> companion to `virtuoso-bridge-lite` (Tsinghua, MIT): schematics are drawn in Cadence
> Virtuoso via that package, exported as HSPICE-format netlists (`si` netlister), and
> simulated with Synopsys PrimeSim via THIS package. G0 delivers the core library so that
> everything testable WITHOUT a PrimeSim binary is implemented and green. Live verification
> against a licensed installation (gate G2) happens later and is out of scope.
> Simulator facts cite "PrimeSim User Guide: Pro and SPICE, Y-2026.03" (Synopsys) by page.
> Do NOT re-derive PrimeSim behavior from memory; items marked **(ASSUMED)** are the ONLY
> ones not backed by the guide — implement them as specified and list them in NOTES.

## Verified simulator facts (do not re-litigate; re-verified 2026-08-21)

Invocation and engines:

- Binary is `primesim`; syntax `primesim [options] deck.sp` (p.45). Netlist is passed
  positionally or via `-i file` / `-hspice file` (HSPICE dialect) (p.47). A `-spectre`
  option exists for Spectre-format input (p.46) but its operand form is not shown —
  G0 never emits it.
- ONE binary hosts TWO engines: default (no flag) = PrimeSim **Pro** (FastSPICE);
  `-spice` selects PrimeSim **SPICE** (true SPICE accuracy) (p.45).
- Accuracy knobs: SPICE engine → `-runlvl 1..6` (1 fastest, 6 most accurate, default 4)
  (pp.47, 212). Pro engine → `-mode {prohd,promd,proxd,spicehd,spicemd,spicexd}`
  (pp.47, 317–318). `-mode` is documented for Pro only; `-runlvl` applies to SPICE.
- `-v` / `-version` prints version information (p.49). G0 does not call it (no binary
  offline) and `SimulationResult` has NO `tool_version` field in G0. Uppercase `-V` is
  NOT documented — never emit it.
- `-o prefix` / `-out prefix` sets the output-file prefix; the prefix may contain a
  directory path (pp.47, 576: `-o run1/out`). `-log file` overrides the log name (p.47).
  Without `-o`, artifacts take the deck stem (p.53: `primesim in.sp` → `in.log`,
  `in.fsdb`, `in.ic`, `in.mt0`); with `-o`, they follow the prefix.
- Threads/processes: `-mt N`, `-np N` (Table 31, pp.571–575). G0 emits only `-mt`.
- `-aopt option[=value]` appends a primesim `.option` from the CLI, overriding the
  netlist (p.48). `-afile file` appends a file to the deck (p.48); repeating `-afile`
  for multiple files is **(ASSUMED)** — not documented. There is NO `-param NAME=VALUE`
  mechanism — parameters live in the netlist or an appended file.
- License env: `SNPSLMD_LICENSE_FILE` (precedence) or `LM_LICENSE_FILE` (p.38); token
  mode `PRIMESIM=0|1|2` env or `-primesim 0|1|2` (pp.39–40, 46); queuing knobs
  `PRIMESIM_WAIT_LICENSE`, `PRIMESIM_WAIT_LICENSE_TIMEOUT`, `PRIMESIM_WAIT_LICENSE_INTERVAL`,
  `PRIMESIM_ORDER`, `PRIMESIM_ONCE_CHECKOUT_LIC` (Table 1, pp.39–40); `-spice_lic` /
  `-pro_lic` restrict checkout (p.47). The names `CKTSIMMC`, `CKTSIMPROFS`,
  `CKTSIMSPICE`, `PRIMESIM_LIC`, `PRIMESIMSPICE_LIC` are license **token names** used
  with `-sim_lic` in Client-Server mode (p.582); probing `lmstat` for them is an
  **(ASSUMED)** opportunistic heuristic — zero matches is a normal, non-failing outcome.
  There is no dedicated license-check CLI flag.

Outputs:

- Waveform format: `.option primesim_output=[fsdb|wdf|psf|psfxl|tr0|psfascii|none|out]`,
  default FSDB for TRAN / WDF for DC-OP-AC (pp.333, 701); the documented multi-value
  combination `"fsdb wdf"` is also legal (p.333). CLI override `-format type` (alias
  `-wavefmt type`) (p.47).
- Two naming conventions (pp.699–700): Convention 1, `<prefix>_<fmt>/…` subdirectories,
  applies to **PSF and WDF only**; Convention 2, `<prefix>[_<ana>][_a#][_t#][_m#][_s#].<ext>`
  beside the prefix, applies to everything else — including `.fsdb`, the TRAN default
  (p.53 shows `in.fsdb` next to `in.log`). Sweep/ALTER/Monte-Carlo runs emit suffixed
  variants like `<prefix>_tran0_a1_m6_s0.mt0` (p.700) and `<prefix>_incr.mt0` (p.576).
- Scalar results (Table 44, pp.701–702): `.mt0` TRAN measures, `.md0` DC measures,
  `.ma0` AC measures; `.pt0/.pd0/.pa0` = `.PRINT` tabular text; `.sw0` DC sweep,
  `.ac0` AC, `.op0` operating point, `.ic` initial conditions; `<name>.log` log.
  `.tr0`/`.sw0` are "PrimeSim HSPICE compatible text or binary" (p.703).
- `.option primesim_measout=[0|1|2|3|4]`: 0 (default) = HSPICE-style ASCII;
  3 = CSV written as `.mt0.csv` (pp.313–314). Whether DC/AC measure files also get
  `.csv` twins under measout=3 is **(ASSUMED)** — the guide names only `.mt0.csv`.
  The CSV's internal row layout is **(ASSUMED)**: header row of measure names, then one
  data row per sweep point. A literal cell value `failed` marking a failed measure is
  **(ASSUMED)** (documented only for the measout=4 `.meas` form, p.314).
- Classic measure-file ASCII layout IS documented by example (pp.312, 563):
  a `$DATA1 SOURCE='…' VERSION='…'` line, a `.TITLE '…'` line, one whitespace-separated
  names row, then one or more whitespace-separated value rows. The `.op0` line-level
  layout is NOT documented. `primesim_output_op=[ascii|ascii_mos_region|wdf]` exists
  (p.337) but is disabled by default and G0 does not inject it — `.op0` parsing exists
  only for externally produced files.
- `-gz` gzips text outputs but never the log (p.50); `primesim_output_gzip` (p.336)
  says compressed files are "`.gzip` files". The actual suffix (`.gz` vs `.gzip`) is
  unconfirmed — handle BOTH.
- `-lock 0|1|2` governs prefix collisions: 0 overwrite, 1 rename-and-proceed (default),
  2 exit with error (p.50). G0 never emits `-lock`.

Status contract:

- Exit codes, Table 45 (pp.703–705), COMPLETE list — codes 26 and 27 do not exist:

  | code | meaning | | code | meaning |
  |---|---|---|---|---|
  | 0 | Simulation succeeded | | 17 | Parallel matrix error |
  | 1 | Memory related error | | 18 | ADFMI related error |
  | 2 | File related error | | 19 | S-element module error |
  | 3 | Not supported yet | | 20 | B-element module error |
  | 4 | Input argument error | | 21 | Monte Carlo module error |
  | 5 | General parsing error | | 22 | Verilog-A error |
  | 6 | I/O file parsing error | | 23 | TMI2 error |
  | 7 | SPF file parsing error | | 24 | Z-Transform module error |
  | 8 | General elaboration error | | 25 | ETMI SOA error |
  | 9 | Unable to resolve expression | | 28 | PrimeSim API multiple analysis statements error |
  | 10 | Option related error | | 29 | PDMI related error |
  | 11 | Netlist connection error | | 30 | Voltage Loop error |
  | 12 | Matrix related error | | 31 | Obsolete Option error (PrimeSim API) |
  | 13 | Unable to converge | | 32 | Bisection error |
  | 14 | Output related error | | 33 | TNA error |
  | 15 | License related error | | 34 | DC not converged (only when `primesim_exit_dc_fail` set) |
  | 16 | Unable to reinvoke by execvp() | | | |

- Two documented traps: (a) network-parallel runs with `-wait` ALWAYS return exit 0
  (p.575); (b) DC non-convergence is by default a WARNING and simulation continues
  (p.253); `.option primesim_exit_dc_fail=1` makes it exit 34 (p.267). Documented log
  signatures: `DC not converged` (p.253), `ERROR! time step too small (diverged)`
  (p.250). There is NO documented success banner and NO documented `0 errors`-style
  summary line — never classify success from log text.

## Design decisions (fixed — do not deviate)

1. **Self-contained package.** The string `virtuoso_bridge` must not appear anywhere
   under `src/` (no import in any form). Runtime dependency = `pydantic` only.
2. **SPICE engine is the default** (`engine="spice"` → `-spice`); Pro is opt-in.
   Rationale: the binary's own default (Pro = FastSPICE) silently downgrades accuracy
   for verification flows.
3. **Exit-code-first classification.** Exit 0 + log errors present → `PARTIAL`
   (errors carried into `result.errors`); exit 0 + warnings only → `SUCCESS`;
   nonzero exit → `FAILURE` per the table (log adds detail, never overrides).
   `is_parallel_wait=True` is a classification HINT only (it does not emit `-wait` —
   callers pass `-wait`/`-bsub` etc. via `extra_args`): it forces log-based
   classification because the process exit code is meaningless there (always 0):
   log errors → `FAILURE`, else `SUCCESS`.
4. **Harness safety injection.** Unless suppressed (see Deliverable 2), the runner
   appends `-aopt primesim_exit_dc_fail=1` and `-aopt primesim_measout=3`; with
   `waveform_format` set it adds `-format <fmt>`. Injected argv is visible in
   `SimulationResult.metadata["argv"]`.
5. **Tier-A parsing is the product**: measure CSV, classic measure ASCII (documented
   shape), the log, and exit codes. `.op0` gets a best-effort non-raising parser for
   externally produced files. Waveform (fsdb/psf/…) parsing is OUT of G0 scope.
6. **Fixture honesty via MANIFEST.** `tests/fixtures/MANIFEST.md` lists EVERY fixture
   file with: filename, what it represents, and its provenance — either the guide page
   it mirrors (e.g. "layout mirrors p.563 example") or the tag `SYNTHETIC-FROM-DOC
   (assumed layout)`. Parsers must NOT be taught to skip comment/marker lines — a
   fixture's content must be exactly what a real artifact could contain. In-file
   markers are allowed ONLY where the format has real comment syntax (e.g. `*` in
   SPICE decks).

## Deliverable 1 — `src/primesim_bridge/models.py`

- `class ExecutionStatus(str, Enum)`: `SUCCESS`, `PARTIAL`, `FAILURE`.
  (Deliberately a SUBSET of virtuoso-bridge-lite's model: no `ERROR` member, no
  `tool_version`, no `save_json` — do not copy those from the reference.)
- `class SimulationResult(pydantic.BaseModel)`: `status: ExecutionStatus`,
  `data: Dict[str, Any]`, `errors: List[str]`, `warnings: List[str]`,
  `metadata: Dict[str, Any]`; `@property ok -> bool` (`status is SUCCESS`).
  `data` value domain: measure name → `float | str | None`, or a list thereof for
  multi-row (sweep) results; reserved key `_rows: int` carries the row count when > 1.
- **Python 3.9 rule (applies to every file):** in pydantic model FIELD annotations use
  `typing.Optional[...]` / `typing.Union[...]` / `typing.Dict/List` — PEP-604 `X | Y`
  is forbidden there (pydantic evaluates field annotations at runtime; 3.9 cannot).
  Plain function signatures may use `X | None` under `from __future__ import annotations`.
- `EXIT_CODE_TABLE: Dict[int, str]` — ALL codes from the table above ({0..25} ∪ {28..34});
  `classify_exit(returncode: int) -> Tuple[ExecutionStatus, Optional[str]]`:
  0 → (SUCCESS, None); any other listed code → (FAILURE, its documented meaning);
  unlisted nonzero → (FAILURE, f"exit code {rc}").

## Deliverable 2 — `src/primesim_bridge/argv.py`

- `primesim_mode_args(engine: str = "spice", *, runlvl: int | None = None,
  mode: str | None = None) -> list[str]`:
  "spice" → `["-spice"]` (+ `["-runlvl", str(runlvl)]`, validated 1..6);
  "pro" → `[]` (+ `["-mode", mode]`, validated against the six documented values).
  runlvl with pro, or mode with spice, raises `ValueError` naming valid combinations.
- `build_primesim_argv(*, netlist: str, prefix: str, binary: str = "primesim",
  log_file: str | None = None, engine_args: list[str] | None = None,
  threads: int | None = None, waveform_format: str | None = None,
  extra_args: list[str] | None = None, inject_safety: bool = True) -> list[str]`:
  `[binary, *engine_args, netlist, "-o", prefix]` + (`["-log", log_file]` if set) +
  (`["-mt", str(threads)]` if set) + (`["-format", fmt]` if set) + safety `-aopt`
  pairs + `extra_args`.
  - Safety suppression: a safety pair is skipped when `extra_args` already contains the
    two-element form `["-aopt", "<same-option-name>[=…]"]` (match on the option name
    before `=`). Other spellings (`-aopt=…`) are out of scope.
  - `waveform_format`: lowercase-normalize; accept the documented single values
    {fsdb, wdf, psf, psfxl, tr0, psfascii, none, out} and the exact documented
    combination `"fsdb wdf"`; anything else raises `ValueError`.

## Deliverable 3 — `src/primesim_bridge/parsers.py`

- `parse_hspice_number(tok: str) -> float | str`: shared numeric parser. Handles
  scientific notation; unit suffixes case-insensitively with **longest match first**:
  `meg` (1e6) before `m` (1e-3); full set `f,p,n,u,m,k,x,meg,g,t` (x = meg = 1e6,
  t = 1e12, f = 1e-15). Alphabetic characters trailing a recognized number+suffix are
  tolerated and ignored (`1.5ns` → 1.5e-9, `10pF` → 1e-11). Unparseable → returned as
  the original string.
- `parse_measure_csv(path: Path) -> dict[str, Any]`: CSV per the (ASSUMED) layout —
  header row of names, ≥1 data rows. Single row → flat `{name: value}`; multiple rows →
  `{name: [values...], "_rows": n}`. Cell `failed` (case-insensitive) → `None`, and a
  message is appended to the reserved key `_warnings: list[str]` in the returned dict.
  The runner drains `_warnings` into `SimulationResult.warnings` and removes it (and
  keeps `_rows` in metadata, not data).
- `parse_measure_ascii(path: Path) -> dict[str, Any]`: parses the DOCUMENTED classic
  shape (pp.312/563): `$DATA1 …` line, `.TITLE …` line, one names row, ≥1 value rows —
  same return shape as the CSV parser. Only when that structure is absent, return
  `{"raw_lines": [...], "parse_confidence": "low"}` — never raise.
- `parse_op_ascii(path: Path) -> dict[str, Any]`: best-effort key/value extraction;
  same non-raising fallback contract (`raw_lines` + `parse_confidence: "low"`).
- `parse_log(path: Path) -> dict` and `parse_log_text(text: str) -> dict` (no
  path-or-text union): return `{"errors": [...], "warnings": [...], "signatures": [...]}`.
  Rules: line containing `ERROR! time step too small (diverged)` → signatures AND
  errors. Line containing `DC not converged` → signatures AND **warnings** (the
  documented default behavior; the RUNNER promotes it to errors when it injected
  `primesim_exit_dc_fail=1` — see Deliverable 4). Generic lines containing
  `error`/`warning` (case-insensitive) are collected into the respective list, except
  lines matching `^\s*0 (errors|warnings)` which are skipped — this suppression is
  **(ASSUMED)**, PrimeSim documents no such summary line. Files are read with
  `errors="replace"`; gzipped logs are NOT expected (`-gz` never compresses the log).
- `collect_outputs(prefix: Path) -> dict[str, list[Path]]`: match `<prefix>.*` AND
  `<prefix>_*` in the prefix's parent directory, plus files inside `<prefix>_<fmt>/`
  subdirectories (Convention 1). Also match `.gz`/`.gzip` twins of any text extension;
  parsers open those transparently via `gzip.open`. Bucket mapping (extension after
  stripping `.gz`/`.gzip`, `#` = any digits):
  - `measure`: `.mt#`, `.md#`, `.ma#`, `.mc#`, `.meas`, and their `.csv` twins
  - `print`: `.pt#`, `.pd#`, `.pa#`
  - `op`: `.op#`
  - `log`: `.log`
  - `waveform`: `.fsdb`, `.tr#`, `.sw#`, `.ac#`, `.wdf`, and all files under
    `<prefix>_<fmt>/` directories
  - `other`: `.ic`, `.ins`, `.fast`, and anything unmatched

## Deliverable 4 — `src/primesim_bridge/runner.py`

- `class RemoteSpec(pydantic.BaseModel)`: `host: str`, `user: Optional[str] = None`.
- `class PrimeSimSimulator`: constructor `binary: str = "primesim"`, `work_dir: Path`,
  `env_setup: str | None = None`, `env_setup_shell: str = "sh"` (`sh`|`csh`),
  `timeout: int = 3600`, `remote: RemoteSpec | None = None`,
  `run_id_factory: Callable[[], str] | None = None` (default: instance-local monotonic
  counter producing `"run1"`, `"run2"`, … — deterministic so tests can assert argv).
  - `@classmethod from_env(cls, **overrides)`: reads ONLY `os.environ` (no dotenv in
    G0): `VB_PRIMESIM_BIN`, `VB_SYNOPSYS_SETUP`, `VB_SYNOPSYS_SETUP_SHELL`,
    `VB_REMOTE_HOST`, `VB_REMOTE_USER`. Empty/missing/`localhost` host → local mode.
  - `run_simulation(netlist: Path, options: dict | None = None) -> SimulationResult`.
    `options` keys: `engine`, `runlvl`, `mode`, `threads`, `waveform_format`,
    `log_file`, `extra_args`, `include_files`, `is_parallel_wait` (bool, default
    False), `prefix`.
    - Default prefix: `work_dir/<stem>/<stem>`; when `work_dir/<stem>/` already exists,
      uniquify the DIRECTORY only: `work_dir/<stem>_2/<stem>`, `_3`, … A caller-supplied
      `prefix` is used verbatim (never uniquified — `-lock` semantics govern collisions).
      `run_simulation` creates the run directory (`mkdir -p`).
    - `include_files`: each staged next to the netlist (local: copied; remote: scp'd),
      then appended as repeated `-afile <basename-path>` flags (**ASSUMED** repeatable).
    - Execution: every subprocess goes through module-level
      `_exec(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess` —
      the ONE seam tests monkeypatch. `timeout` applies per `_exec` call.
    - `env_setup` wrapping (exact forms):
      `sh`: `["sh", "-c", ". " + shlex.quote(script) + " && exec " + shlex.join(argv)]`
      (POSIX `.` — `source` is a bashism and breaks on dash);
      `csh`: `["csh", "-fc", "source " + shlex.quote(script) + "; exec " + shlex.join(argv)]`.
    - Binary pre-check: `shutil.which(binary)` ONLY when `env_setup is None and remote
      is None`; on miss (or `FileNotFoundError` from `_exec`) return
      `FAILURE` with `errors=["primesim executable not found: <binary>"]` and
      `metadata["argv"]` populated — never raise. With `env_setup`/`remote` set, rely on
      the subprocess's own failure (the binary legitimately appears only after sourcing).
    - Timeout (`subprocess.TimeoutExpired`): collect outputs anyway; status `PARTIAL`
      if ≥1 artifact exists else `FAILURE`; in both cases append `timeout after <N>s`.
    - Remote mode (OpenSSH subprocess only): remote run dir
      `~/.primesim_bridge/runs/<run-id>/` (run-id from `run_id_factory`);
      sequence = `scp` netlist+includes → `ssh host 'cd <dir> && <wrapped cmd>'` →
      `scp -r` artifacts back into the local run dir → parse locally. `user` set →
      `user@host` form. Any scp/ssh nonzero exit (other than the simulation's own) →
      `FAILURE` with the failing stage named. `metadata["argv"]` = the primesim argv;
      the ssh/scp command lines go to `metadata["remote_cmds"]`.
    - Classification: per Design decision 3. When the runner injected
      `primesim_exit_dc_fail=1` (i.e. safety not suppressed), a `DC not converged`
      log signature is promoted from warnings to errors before classification.
    - Result assembly: parse tier-A artifacts from `collect_outputs` buckets
      (`measure` bucket → data; `_warnings` drained; `_rows` → metadata), log →
      errors/warnings/signatures (also stored in `metadata["log_signatures"]`),
      `metadata` also carries `returncode`, `output_files`, `prefix`.
- NOT in G0: parallel pools, license queuing, waveform parsing, dotenv discovery,
  `virtuoso_bridge` transport reuse, `-v` version capture.

## Deliverable 5 — `src/primesim_bridge/cli.py`

`main()` via `argparse`, subcommands:
- `run NETLIST [--engine spice|pro] [--runlvl N] [--mode M] [-o PREFIX] [--mt N]
  [--format FMT] [--log FILE] [--remote HOST] [--timeout S] [--dry-run]`.
  `--dry-run` builds the argv DIRECTLY via Deliverable 2 (no `run_simulation`, no
  filesystem access of any kind — no existence check, no mkdir) and prints exactly one
  line: `shlex.join(argv)`, exit 0. A normal run prints a JSON summary
  (`status`, `data`, `errors`, `warnings`) and exits 0 iff `result.ok`.
- `parse PREFIX`: `collect_outputs` + tier-A parsers on existing artifacts; JSON out.
- `status`: report `shutil.which("primesim")`; which of these env vars are SET (names
  only, never values): `VB_PRIMESIM_BIN`, `VB_SYNOPSYS_SETUP`, `VB_SYNOPSYS_SETUP_SHELL`,
  `VB_REMOTE_HOST`, `VB_REMOTE_USER`, `SNPSLMD_LICENSE_FILE`, `LM_LICENSE_FILE`,
  `PRIMESIM`, `PRIMESIM_ORDER`, `PRIMESIM_WAIT_LICENSE`, `PRIMESIM_WAIT_LICENSE_TIMEOUT`,
  `PRIMESIM_WAIT_LICENSE_INTERVAL`; when `lmstat` is on PATH, lines containing
  `Users of <name>` for the five token names (zero matches = normal). Never fail hard.

## Deliverable 6 — tests (`tests/`)

- `tests/fixtures/MANIFEST.md` per Design decision 6, listing every fixture file.
- `test_argv.py`: mode-args validation (both engines + cross-use errors);
  argv snapshots (safety injection present; suppressed when caller passes the same
  `-aopt` name; `-format` validation incl. `"fsdb wdf"` accepted and junk rejected;
  binary substitution; `--log`/threads placement).
- `test_models.py`: `classify_exit` — parametrize over ALL table codes + one unlisted
  code (e.g. 27 → "exit code 27").
- `test_parsers.py`: `parse_hspice_number` (sci-notation, `meg` vs `m` longest-first,
  `f`/`t`, trailing-unit tolerance, junk passthrough); CSV single-row / multi-row sweep /
  `failed`→None+`_warnings`; gz-compressed CSV (both `.gz` and `.gzip` suffixes);
  classic `.mt0` mirroring the p.563 example shape; malformed classic file → raw_lines
  + low confidence, no exception; log signature rules incl. the DC-signature-to-warnings
  default and the (ASSUMED) `0 errors` suppression; `collect_outputs` classification
  incl. `<prefix>_a1_s0.mt0`-style suffixed names, `<prefix>_wdf/` subdir contents,
  and gz twins. A manifest-check test: every file under `tests/fixtures/` appears in
  `MANIFEST.md` (and vice versa).
- `test_runner.py` (monkeypatch `_exec`): end-to-end argv; exit-code → status flows;
  exit 0 + log errors → PARTIAL; `is_parallel_wait=True` + exit 0 + log error → FAILURE;
  DC-signature promotion when safety injected vs not (suppressed) — different status;
  binary-missing (local, no env_setup) FAILURE without raise; which-precheck SKIPPED
  when `env_setup` set (assert `_exec` was called); timeout → PARTIAL-with-artifacts and
  FAILURE-without; unique-directory rerun (`<stem>_2`); remote sequence scp→ssh→scp
  argv shapes with deterministic run-id, scp failure → FAILURE naming the stage.
- `test_cli.py`: `--dry-run` single-line output contains `-spice`, `-o`,
  `-aopt primesim_exit_dc_fail=1`, `-aopt primesim_measout=3`, and touches no
  filesystem (run against a nonexistent netlist path); `parse` on fixtures; `status`
  never raises without primesim/lmstat.
- Suite target: `pip install -e ".[dev]"` in a fresh venv, then
  `python -m pytest tests/ -q` fully green.

## Execution after implementation

> Codex sandbox blocks localhost sockets and outbound SSH — G0 needs NEITHER: all
> execution paths are monkeypatched at the `_exec` seam. Codex runs the offline suite
> only. Any step that seems to require a real `primesim`, `ssh`, or license server is
> OUT OF SCOPE — record it in NOTES and stop rather than improvising.
> **Known limit (state it in NOTES, do not "fix" it):** runner/env-setup/remote tests
> assert argv SHAPE only — they cannot verify shell sourcing behavior, scp/ssh
> semantics, real artifact formats, the gz suffix, or lmstat feature visibility.
> Those form the G2 live checklist.
> The main session performs: final test run in its own venv, packaging check
> (`pip install -e .` + `primesim-bridge run tb.sp --dry-run`), commit review.

## Constraints

- Do NOT modify: `.claude-plugin/plugin.json`, `pyproject.toml`,
  `skills/primesim/SKILL.md`, `README.md`, `LICENSE`, this spec.
  (`pyproject.toml` already declares pydantic>=2.0, requires-python>=3.9, the
  `primesim-bridge = "primesim_bridge.cli:main"` entry point, and `dev = ["pytest>=7"]`
  — verified.) `.gitignore` already un-ignores `tests/fixtures/**` — name fixtures
  naturally, but run `git status --ignored tests/fixtures/` before committing to
  confirm nothing is excluded.
- Runtime dependency = `pydantic` only; tests = `pytest` only. Python ≥ 3.9 — see the
  pydantic/PEP-604 rule in Deliverable 1.
- Do not reproduce Synopsys manual sentences in code/comments. Flag names, exit-code
  meanings, literal log-signature strings, and file-format tokens needed for matching
  are facts — fine.
- ONE commit on the current default branch (**`master`**):
  `feat: G0 core — runner, tier-A parsers, CLI (offline-verified)`. Do not push.
- Blocker protocol: on any hard blocker, write `docs/NOTES_G0.md` describing it and
  stop. Do not improvise around a constraint.

## Acceptance criteria

1. Fresh venv + `pip install -e ".[dev]"` + `python -m pytest tests/ -q` → all green.
2. Every file under `tests/fixtures/` is listed in `tests/fixtures/MANIFEST.md` with
   provenance (guide page or `SYNTHETIC-FROM-DOC (assumed layout)`); enforced by the
   manifest-check test.
3. `primesim-bridge run tb.sp --dry-run` (nonexistent path OK) prints one
   `shlex.join`-formatted line containing `-spice`, `-o`,
   `-aopt primesim_exit_dc_fail=1`, `-aopt primesim_measout=3` — no PrimeSim installed,
   no filesystem writes.
4. `grep -rn "virtuoso_bridge" src/` returns nothing.
5. `set(EXIT_CODE_TABLE) == set(range(0, 26)) | set(range(28, 35))`; no `-V` or
   success-banner-string classification anywhere in `src/`.
6. `docs/NOTES_G0.md` exists: what was built, test count, every deviation + reason,
   the list of (ASSUMED) format details with their G2 confirmation checks, and the
   G2-unverifiable behavior list from "Execution after implementation".
