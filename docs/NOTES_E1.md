# E1 implementation notes

## What was built

E1 adds the engine-profile refactor and PrimeSim HSPICE support described in
`SPEC_E1_engines_hspice.md` and its v2.1 Design decision 2 amendment:

- A frozen `EngineContext`, profile protocol, PrimeSim and HSPICE profiles, and
  engine registry in `src/primesim_bridge/engines.py`. PrimeSim SPICE/Pro command
  construction remains behavior-compatible; HSPICE has its documented argv,
  validation, listing, exit-code, banner, safety-include, and binary-environment
  contracts.
- Per-run profile and binary resolution in the runner, absolute-local versus
  basename-remote contexts, engine-owned include argv, HSPICE auxiliary-option
  creation and staging in both transports, and `metadata["engine"]` on every
  result path.
- The v2.1 DC-signature rule: PrimeSim-only promotion remains log
  post-processing in the runner before the engine-independent status chain. It
  is guarded by `profile.name == "primesim"`, safety enablement, and the existing
  `primesim_exit_dc_fail` suppression check. Neither profile classifier promotes
  DC signatures, and HSPICE leaves such lines as warnings.
- HSPICE CLI selection and explicit `--binary` support, including filesystem-free
  dry runs and exit-2 validation for HSPICE-incompatible accuracy/log options.
- Additive log signatures and HSPICE artifact families, failed-measure handling
  in CSV and classic tables, and indexed alter-measure metadata in both runner
  and standalone CLI parsing.
- An executable, dependency-free `tests/fake_hspice.py` and new unit/real-subprocess
  tests covering profiles, classification, safety modes, failed and alter
  measures, include staging, WaveView, LSF wrapping, stdout-only invocation,
  binary precedence, and remote argv/upload order.

The standalone `primesim-bridge parse` command has no engine-profile input, so
it intentionally does not apply engine-specific extra log signatures.

## Verification

The required offline base tier passed:

```text
179 passed, 4 skipped in 2.72s
```

The four skips are the unchanged opt-in companion/live-SSH tiers. Companion and
live-SSH tests were not enabled, no SSH host was contacted, and no network access
was used. The fake-HSPICE real-subprocess scenarios passed in the sandbox.

The available `python3` lacked pytest and pydantic together, so the exact
`python -m pytest tests/ -q` command used an existing Python 3.11 environment and
the repository source path:

```sh
PATH=/Users/rick/Projects/openclaw-brain/.venv/bin:$PATH PYTHONPATH=src python -m pytest tests/ -q
```

Additional checks passed: Python 3.9 compilation with a temporary bytecode cache,
`git diff --check`, executable mode 100755 for `tests/fake_hspice.py`, and the
protected-file audit. No pre-E1 test or fixture was edited.

## Deviations and reasons

There are no product-behavior deviations from the amended E1 specification.

The specification asks to extend the existing `tests/conftest.py` chmod fixture,
but the stronger hard rule requires zero edits to pre-E1 tests. The existing
conftest therefore remains untouched: `tests/fake_hspice.py` carries mode 100755
directly, and its new test module verifies executability. This preserves the
required behavior while honoring the protected-test constraint.

The test interpreter selection above is an execution-environment detail, not a
runtime dependency or repository change.

## (ASSUMED) items awaiting G2 checks

- Confirm `.option measform=3` and `.option measfail=1` take effect when injected
  through `-include_first`, including the exact `measfail` spelling and value.
- Confirm that exit zero without the `job concluded` banner should be classified
  as `PARTIAL` with `exit 0 but no 'job concluded' banner in .lis`.
- Confirm the MEASFORM=3 CSV filename family: `.mt#.csv`-style files and/or bare
  `<prefix>.csv`.

## Owner-session commands

Create a fresh environment, install the project, and repeat the base tier:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
```

Install the pinned companion and run the companion and live-SSH tiers (set the
user only when SSH configuration does not provide it):

```sh
scripts/install_companion_pin.sh
RUN_COMPANION_TESTS=1 python -m pytest tests/ -q
RUN_COMPANION_TESTS=1 RUN_LIVE_SSH_TESTS=1 PSB_TEST_SSH_HOST=your-host PSB_TEST_SSH_USER=your-user python -m pytest tests/test_companion.py tests/test_live_ssh.py -q
```

Perform one fake-HSPICE-over-SSH run by staging `tests/fake_hspice.py` in the
runner's chosen remote run directory, making it executable, and invoking
`PrimeSimSimulator(binary="./fake_hspice.py", remote=RemoteSpec(...))` with
`options={"engine": "hspice"}`. Assert `SUCCESS`, parsed measure data, the
HSPICE engine metadata, and the uploaded `psb_hspice_options.sp`.

Then run the packaging checks, update the protected PrimeSim skill documentation
for `--engine hspice` and `VB_HSPICE_BIN`, and commit/push from the owner session:

```sh
python -m build
python -m pip install --force-reinstall --no-deps dist/*.whl
primesim-bridge run tb.sp --engine hspice --dry-run
git diff --check
git add src/primesim_bridge docs/NOTES_E1.md tests/fake_hspice.py tests/test_engines.py tests/test_behavior_hspice.py skills/primesim/SKILL.md
git commit -m "feat: E1 — engine-profile refactor + PrimeSim HSPICE engine"
git push
```

No commit was created in the sandbox, as required; `.git` is read-only here.

## Intended commit message

```text
feat: E1 — engine-profile refactor + PrimeSim HSPICE engine
```
