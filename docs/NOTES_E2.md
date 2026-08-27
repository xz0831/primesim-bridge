# E2 implementation notes

## What was built

E2 adds the PrimeSim XA engine described in `SPEC_E2_xa.md`:

- An `XaProfile` registered as `xa`, with `xa`/`VB_XA_BIN` binary resolution,
  positional-netlist argv construction, `-spectre`/`-eldo` dialect flags,
  `-wavefmt` waveform selection, exact XA-specific validations, the documented
  `<prefix>.log` path, and log-first status classification.
- XA safety injection through `psb_xa.cmd`. The command file requests HSPICE
  measure format and follows the coded E1 context rules: absolute in local
  execution, basename in remote and dry-run argv, and uploaded after the
  netlist for remote execution.
- Dialect validation on the shared PrimeSim SPICE/Pro profile and the HSPICE
  profile, using `ctx.options.get("dialect") is not None` so the non-filtered
  CLI dry-run options remain valid when no dialect is selected.
- CLI support for `--engine xa` and `--dialect {hspice,spectre,eldo}` in both
  dry-run and normal-run option paths. XA never emits a literal `-format`;
  waveform selection uses `-wavefmt`.
- Artifact bucketing for digitless `.mt`/`.mt.csv` XA measures, including
  compressed and `.a#.t#` endings, while preserving the existing indexed
  measure alternation. `.out` and `.psf` are now waveform artifacts; digitless
  `.mc` and the documented non-measure families remain `other`.
- An executable, dependency-free `tests/fake_xa.py` (mode 100755), plus XA
  profile, parser, CLI, local subprocess, synchronous LSF-wrapper, remote-shape,
  WaveView, output-prefix-trap, and alter-limitation tests.

The only edited pre-E2 test is the sanctioned sentinel in
`tests/test_engines.py::test_profile_registry_aliases_and_unknown_value`: its
unknown value is now `nosuchengine`, and its expected registry list includes
`xa`. No fixture or other pre-E2 test file was edited.

## Verification

The required offline base tier passed:

```text
238 passed, 4 skipped in 2.92s
```

The 59 XA-focused tests also passed independently. The four skips are the
unchanged companion/live-SSH tiers. No companion or live-SSH opt-in was enabled,
no SSH host was contacted, and no network access was used.

This shell has no `python` executable and its system `python3` lacks pytest.
As in E1, the exact base command was run with an existing Python 3.11
environment and the repository source path:

```sh
PATH=/Users/rick/Projects/openclaw-brain/.venv/bin:$PATH PYTHONPATH=src python -m pytest tests/ -q
```

The edited and added Python files also passed `python3 -m compileall` under the
available Python 3.14 interpreter. No commit was created.

## Deviations and known limitations

There are no product-behavior deviations from the E2 specification.

XA `.a#` alter filenames do not match the E1 indexed-measure regex. E2
therefore buckets files such as `xa.a0.mt` as measures but leaves
`metadata["alter_measures"]` absent/empty; later alter files merge into `data`
with last-wins behavior. A documenting test pins this limitation for the E3
index-regex extension.

`PrimeSimSimulator.run_simulation` stages requested include files before a
profile validates them. Consequently, callers that pass `include_files` to XA
may see the pre-existing copy step before the exact XA rejection. The direct
profile unit test validates the intended error without that runner ordering;
the ordering itself was not changed in E2.

## (ASSUMED) items and G2 checks

- Confirm that HSPICE-format XA measure artifacts are literally digitless
  `<prefix>.mt` files and that their contents have the classic `$DATA1` shape
  consumed by `parse_measure_ascii`. Indexed variants may also exist.
- Confirm the proxy semantics for `Total Wall Time =`: E2 warns, without status
  promotion, only for exit-zero multicore runs where the marker is absent.
- Confirm the exact Linux `-o` outpath/outfile split, especially how XA treats
  existing directories and path-like values in edge cases.
- Assess false positives from the generic `parse_log` substring scan when XA
  summary lines contain words such as `error` or `warning`; only `0 errors` and
  `0 warnings` receive the existing special handling.
- Assess ambient `xa.ini` influence. Local `_exec` inherits the caller's cwd,
  so XA may auto-read a site or user initialization file outside the run
  directory before falling back through its documented search order.

## Owner-session commands

Create a fresh environment, install the project, and repeat the base tier:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
```

Install the pinned companion and run the opt-in tiers, then perform one
fake-XA-over-SSH run using `tests/fake_xa.py` and
`options={"engine": "xa"}`. Assert `SUCCESS`, XA metadata, parsed measure data,
and upload of `psb_xa.cmd` after the deck:

```sh
scripts/install_companion_pin.sh
RUN_COMPANION_TESTS=1 python -m pytest tests/ -q
RUN_COMPANION_TESTS=1 RUN_LIVE_SSH_TESTS=1 PSB_TEST_SSH_HOST=your-host PSB_TEST_SSH_USER=your-user python -m pytest tests/test_companion.py tests/test_live_ssh.py -q
```

Then run packaging and dry-run checks, update the protected PrimeSim skill
documentation for XA, and commit/push from the owner session:

```sh
python -m build
python -m pip install --force-reinstall --no-deps dist/*.whl
primesim-bridge run tb.sp --engine xa --dry-run
primesim-bridge run tb.sp --engine xa --dialect spectre --dry-run
git diff --check
git add src/primesim_bridge/cli.py src/primesim_bridge/engines.py src/primesim_bridge/parsers.py docs/NOTES_E2.md tests/fake_xa.py tests/test_behavior_xa.py tests/test_engines.py tests/test_engines_xa.py tests/test_parsers_xa.py skills/primesim/SKILL.md
git commit -m "feat: E2 — PrimeSim XA engine"
git push
```

No commit was created in the sandbox, as required; `.git` remained read-only.

## Intended commit message

```text
feat: E2 — PrimeSim XA engine
```
