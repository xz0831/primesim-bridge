# SPEC: primesim-bridge E2 — PrimeSim XA engine

> Owner: main session (design) → Codex (implementation). 2026-08-28, **v2**
> (v1 adversarially reviewed against the REAL E1 code and the XA PDFs; 27 defects
> fixed). Context: E1 (commit 617a4f3) established EngineProfile
> (`src/primesim_bridge/engines.py`, profiles primesim/hspice) — read that module
> and runner.py/cli.py/parsers.py first; E2 adds the `xa` profile following the
> E1 patterns AS CODED. XA facts cite "PrimeSim XA Y-2026.03" (UG/CR/RN/PIN,
> printed pages; PDFs in the scratchpad). **(ASSUMED)** items are the only
> non-doc-backed ones. XA is Linux-only (PIN p.2) — the fake double and
> remote/LSF paths are the whole home-side story.

## Verified XA facts (review-verified 2026-08-28)

- Syntax `xa [-dialect] netlist [options]` — netlist POSITIONAL, exactly one
  (UG pp.26-27). Dialect flags `-hspice` (DEFAULT when omitted) / `-spectre` /
  `-eldo`. ⚠ `-format` is OVERLOADED: in the synopsis it is a placeholder for
  those dialect flags, but the real option `-format` is an alias of `-wavefmt`
  (UG p.31) — the bridge emits `-spectre`/`-eldo` for dialect and `-wavefmt`
  for waveforms, and NEVER a literal `-format`.
- `-o [outpath/]outfile`: directory+prefix in one option; **without `-o` (or
  with outfile omitted) the prefix defaults per `-outfilefmt`, default =
  literal `xa`** (UG pp.31-32; example UG pp.26-27: `-o ./OUT -outfilefmt
  hspice` → `OUT/input.log`). ⚠ If the `-o` value names an EXISTING directory,
  XA treats it as outpath and writes `<dir>/xa.*` — the harness would collect
  nothing. **(G2: exact outpath/outfile split rules on Linux.)**
- `-c command_file` (repeatable): commands processed as if on the FINAL line of
  the netlist; command file overrides netlist commands (UG p.28; CR p.14).
  Commands case-sensitive, Tcl-like; `#` at line start is a comment (CR "Tcl
  On-and-Off Behavior"). `xa.ini` auto-read from cwd → $HOME → install dir
  (UG p.25).
- `-mt N|max` (UG p.29). `-I dir` adds an include SEARCH path only — no CLI
  include-APPEND mechanism; no `-param` (UG Table 3). Accuracy knobs exist as
  `-sim_mode` / `set_sim_level` but NOT as runlvl/mode-style flags (UG p.29).
- Measures (CR pp.184-186, `set_meas_option -format hspice|xa|csv`; the
  `-format hspice` example is verbatim in CR): default `xa` → `.meas` (XA row
  format); `hspice` → "*.mt output format" (HSPICE-compatible); `csv` →
  `.a#.t#.mt.csv`. **(ASSUMED — G2: whether hspice-format files are literally
  digitless `<prefix>.mt` and whether their content is `$DATA1`-shaped as our
  classic parser requires; CR's `-measalt` alter-index option suggests indexed
  variants exist.)**
- Status contract (weakest engine): **exit codes NOT documented** (searched all
  four PDFs). Log = `<prefix>.log` (UG p.43); message grammar `Error: ` /
  `Warning: ` line prefixes (UG pp.47, 205, 223, 357). No success banner;
  `Total Wall Time = ...` "at the end of the log file" is documented **for
  multicore runs** (UG p.224, Multicore chapter) — single-core end-of-log
  wording is undocumented. Progress goes to stderr on a timer (UG p.25).
- Waveforms: default FSDB; UG p.45 Table 7 formats = {fsdb, out, wdf, psf,
  tr0} (CLI `-wavefmt`); `set_waveform_option` additionally accepts vpd/print
  (CR p.271) — the bridge validates against the NARROWER Table-7 set.
- `.ALTER` → `.a#` suffixes FROM 0 (`xa.a0.meas`, `xa.a0.log`, UG p.54);
  one log per alter. MC native (UG pp.204-218); per-sample outputs only via
  `set_monte_carlo_option`; summary `.mc` (+ `.mc0` parameter file, `.mc_params`,
  `.mc.csv`). E2 needs no MC code.
- License env identical family to primesim (SNPSLMD/LM_LICENSE_FILE,
  PRIMESIM=0|1|2, PRIMESIM_WAIT_LICENSE*; UG pp.20-24); FlexLM feature names
  undocumented.

## Design decisions (fixed — do not deviate)

1. **XA profile in `ENGINE_PROFILES` under `"xa"`**: `name="xa"`,
   `default_binary="xa"`, `env_binary_var="VB_XA_BIN"`,
   `log_signatures=("Total Wall Time =",)`,
   `log_path(ctx) -> Path(str(ctx.prefix) + ".log")`.
   **Sanctioned pre-E2 test edit (the ONLY one):**
   `tests/test_engines.py::test_profile_registry_aliases_and_unknown_value`
   used `"xa"` as its unknown-engine sentinel — replace `get_profile("xa")`
   with `get_profile("nosuchengine")` and update `match=` to
   `"spice, pro, hspice, xa"`; update `get_profile`'s error message in
   `engines.py` accordingly. Every other pre-E2 test file: zero diff.
2. **argv** (argv_ctx paths, E1 conventions AS CODED):
   `[binary]` + dialect flag (`options.get("dialect")`: None/"hspice" → emit
   NOTHING; "spectre"/"eldo" → `-spectre`/`-eldo`; other → ValueError) +
   `[<netlist>]` + `["-o", <prefix>]` + `["-mt", str(N)]` if threads +
   `["-wavefmt", fmt]` if waveform_format (lowercase-normalized, validated ∈
   {fsdb, out, wdf, psf, tr0}, emit normalized — E1 pattern) +
   (safety: `["-c", <aux_ref>]`) + extra_args.
   **Aux reference = `Path("psb_xa.cmd")` when `ctx.options.get("dry_run") is
   True`, else `ctx.prefix.parent / "psb_xa.cmd"`** — byte-for-byte the E1
   HspiceProfile branch (engines.py:159-165).
3. **Safety aux** (when `ctx.safety` and no caller `-c` in extra_args):
   `("psb_xa.cmd", "# injected by primesim-bridge\nset_meas_option -format hspice\n")`.
4. **Validation** (ValueError; exact strings — tests match on them):
   - runlvl/mode set → `"xa accuracy is set with set_sim_level / -sim_mode, not
     runlvl/mode"`
   - log_file set → `"xa log is always <prefix>.log"`
   - include_files non-empty → `"xa has no CLI include-append mechanism (-I
     only adds a search path) — use .include/.lib inside the netlist"`
   - prefix path exists AND is a directory → `"xa -o treats an existing
     directory as the output directory; choose a prefix that is not a
     directory"` (dot-containing prefixes are ALLOWED — no hspice-style rule)
   - **dialect on other engines**: add `_validate` to `PrimeSimProfile` (it
     currently has NONE — new static method; note PRIMESIM_PROFILE backs both
     "spice" and "pro") and extend `HspiceProfile._validate`: both raise
     `"dialect is only valid for engine xa"` when
     `ctx.options.get("dialect") is not None` — NEVER `"dialect" in options`
     (the CLI dry-run dict is not None-filtered).
5. **classify** (log-first): nonzero returncode → FAILURE `f"exit code {rc}"`
   (no table, no signal normalization — undocumented). `None` returncode →
   FAILURE `"no exit code (process did not complete)"` (branch reachable only
   from unit tests — the runner chain intercepts earlier). Exit 0: compute
   status FIRST (log errors → PARTIAL, else SUCCESS); THEN, independently,
   when `ctx.threads is not None` and `"Total Wall Time ="` is absent from
   `log["signatures"]`, append warning `exit 0 but no 'Total Wall Time'
   end-of-log marker (undocumented success proxy)` WITHOUT changing status
   (the marker is documented for multicore runs only — single-core runs get no
   warning; **proxy semantics ASSUMED — G2**). Unit-test all four cells of
   {errors, no-errors} × {marker, no-marker} with threads set, plus one
   threads-None no-marker case asserting NO warning. No signature promotion.
6. **Parsers — exactly ONE code change**: a separate `_bucket_for` clause
   matching `\.mt(?:\.csv)?$` → measure (placed so the existing compression
   stripping still applies; also matches `.a#.t#.mt` / `.a#.t#.mt.csv` via the
   same ending). Do NOT relax `\d+` → `\d*` in the existing alternation (that
   would wrongly make digitless `.mc` a measure — `.mc` stays `other` via
   fall-through, as do `.valog/.errt/.errz/.hotspot/.power/.rcxt/.err/.hiz` —
   regression-test those as already-other, no code). `.out`/`.psf` → ADD to
   the waveform clause (they are XA waveform formats; currently fall to
   other). **Known E2 limitation (document in NOTES, one documenting test):**
   XA alter files (`.a#.…`) carry no E1-recognizable index, so
   `metadata["alter_measures"]` stays empty and later `.a#` measures merge
   into `data` with last-wins — deferred to E3 (extend the index regex).
7. **fake-xa** (`tests/fake_xa.py`, stdlib-only, same directive grammar):
   value options exactly `{-o, -c, -mt, -wavefmt, -format, -outfilefmt}`
   (keep E1's `index + 1 < len(args)` guard); bare flags
   `{-hspice, -spectre, -eldo, -gz}` consumed silently; FIRST positional =
   netlist, later positionals ignored; `-c` REPEATABLE, contents join the deck
   view AFTER the deck (final-line semantics; the runner passes the aux path
   absolutely in local mode, so the fake just reads the given path).
   **Deliberate divergence from fake_hspice:** with no `-o`, fake-xa does NOT
   dump to stdout — it writes `xa.*` into the process cwd (models the literal-
   `xa` trap). With `-o <prefix>`: writes `<prefix>.log` (neutral lines free of
   error/warning substrings + `fake:log` lines + `Error: TEXT` per
   `fake:error=TEXT` + final line `Total Wall Time = 1 sec (0hr 0min 1sec)`
   unless `fake:no_walltime`). Measures: deck view containing
   `set_meas_option -format hspice` (case-sensitive) → classic `$DATA1`-shaped
   `<prefix>.mt`; else `<prefix>.meas` with content
   `# XA measure row format (not parsed by the bridge)\n<name>: <value>\n`.
   Honors `fake:fsdb`/`fake:measure`/`fake:rows` (rows apply to `.mt`)/
   `fake:exit`/`fake:sleep`/`fake:sleep_first`. Executable: run
   `chmod 755 tests/fake_xa.py` after creating it (git mode 100755).
8. **Zero edits to pre-E2 test files/fixtures** except the single sanctioned
   edit in D1. Do NOT edit `tests/conftest.py`: define a module-scoped
   `fake_xa_path` fixture in `tests/test_behavior_xa.py` that does
   `os.chmod(path, 0o755)` THEN asserts `os.access(path, os.X_OK)`.

## Deliverable 1 — XA profile (`engines.py`, D1-D5) + the sanctioned test edit
## Deliverable 2 — parser changes (D6) + regression tests
## Deliverable 3 — CLI
`--engine` gains `xa`; new `--dialect {hspice,spectre,eldo}` argument; plumb
`"dialect": args.dialect` into BOTH `dry_options` (cli.py:101 area) AND the run
`options` dict (cli.py:131 area) — the run dict is None-filtered, the dry dict
is not, hence D4's `.get(...) is not None` rule. SystemExit(2) path covers the
new validations (existing handler).
## Deliverable 4 — fake-xa + tests

`tests/test_engines_xa.py` (unit): argv snapshots (default dialect emits no
flag; spectre dialect; `-c` present / suppressed via no_safety / suppressed via
caller `-c` in extra_args; wavefmt normalization + junk rejected; positional
netlist before `-o`); validation errors (all five D4 messages, incl.
dialect-on-spice AND dialect-on-hspice, include_files via
`profile.build_argv(ctx)` DIRECTLY — run_simulation copies includes before
validating, a pre-existing ordering noted in NOTES); classify matrix per D5;
existing-directory prefix rejection.
`tests/test_behavior_xa.py` (real subprocess, inline decks, base tier):
1. success + measures + safety → SUCCESS; argv has `-c` with ABSOLUTE aux
   path; aux file in run dir contains `set_meas_option -format hspice`;
   `data` parsed (keyed values) from classic `.mt`;
   `metadata["engine"] == "xa"`.
2. `no_safety=True` → no `-c`; `.meas` written and bucketed measure; classic
   parser falls back: assert `result.data.get("parse_confidence") == "low"`,
   `"vout" not in result.data`, and
   `any("vout" in line for line in result.data["raw_lines"])` — the value
   survives only as unstructured raw_lines; the injection is what makes it a
   keyed value. (This verifies HARNESS wiring; the XA-side claim stays G2.)
3. `fake:error=simulation aborted` + `fake:exit=2` → FAILURE "exit code 2" +
   the `Error:` line present in errors.
4. threads=2 + exit 0 + `fake:no_walltime` → SUCCESS with the proxy warning;
   threads unset + `fake:no_walltime` → SUCCESS with NO proxy warning.
5. exit 0 + `fake:error=...` → PARTIAL.
6. `fake:fsdb` + `waveview_script: True` → WaveView script generated.
7. sync LSF wrapper around fake-xa → SUCCESS.
8. remote-mode argv shape (monkeypatched `_exec`): pin the WHOLE remote argv
   list — `["xa-custom", "tb.sp", "-o", "tb", "-c", "psb_xa.cmd"]` (bare
   basenames; `Path(".")/x` collapses) — plus aux in the upload list after the
   netlist (E1 test_behavior_hspice remote test is the template).
9. no-`-o` trap (direct subprocess): `subprocess.run([sys.executable,
   str(fake_xa_path), str(deck)], cwd=str(tmp_path), ...)` → rc 0 and
   `(tmp_path/"xa.log").is_file()` — never rely on ambient cwd.
10. dialect: `run tb.sp --engine xa --dialect spectre --dry-run` argv contains
    `-spectre` (and criterion-2 form without dialect).
11. alter-limitation documenting test: pre-created `xa.a0.mt`-style files →
    `alter_measures` absent/empty and `data` merged last-wins (assert the
    CURRENT behavior; comment marks it an E2 limitation → E3).

## Deliverable 5 — `docs/NOTES_E2.md`
Built/counts/deviations/(ASSUMED)+G2 list (digitless `.mt` naming + `$DATA1`
shape; walltime proxy semantics; `-o` outpath/outfile split; XA summary-line
false-positive risk for parse_log's generic error/warning grep; ambient
`xa.ini` since `_exec` inherits caller cwd)/owner commands/intended commit
message `feat: E2 — PrimeSim XA engine`.

## Execution after implementation
> Codex: base tier only (`python -m pytest tests/ -q`); tiers stay SKIPPED; no
> network; do NOT commit (sandbox `.git` read-only). Owner: fresh venv, all
> tiers incl. one fake-xa-over-SSH run, skill doc, commit/push.

## Constraints
Same as E1 (protected files; pydantic-only; py≥3.9 typing rules;
`virtuoso_bridge` confined to `_companion.py`; blocker → `docs/NOTES_E2.md`
and stop). Pre-E2 test edits: ONLY the D1-sanctioned sentinel change.

## Acceptance criteria
1. `python -m pytest tests/ -q`: 0 failed; ≤ 4 skipped; ≥ 203 collected;
   pre-E2 test files show zero diff EXCEPT the D1-sanctioned edit.
2. `primesim-bridge run tb.sp --engine xa --dry-run` (any cwd) prints one line
   with `shlex.split(out) == ["xa", "tb.sp", "-o", "tb", "-c", "psb_xa.cmd"]`.
3. Behavior pair 1 vs 2 passes with the EXACT assertions of scenario 2
   (raw_lines-based).
4. `metadata["engine"] == "xa"` on every xa path; primesim/hspice tests
   untouched and green.
5. `docs/NOTES_E2.md` per Deliverable 5.
