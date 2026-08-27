# G1 implementation notes

## What was built

G1 adds the optional companion integration and behavior-test layers described in
`SPEC_G1_integration.md`:

- A soft-imported, cached capability probe and adapter for the pinned companion's
  SSH transport, PSF ASCII directory parser, and environment-file resolver. All
  companion package references are isolated in `src/primesim_bridge/_companion.py`.
- Automatic companion SSH transport selection with observable transport and
  home-relative remote-directory metadata. The OpenSSH path now creates its remote
  directory before upload.
- Explicit per-run waveform parsing through `options["parse_waveforms"]` and the CLI
  `--waveforms` flag, including the unverified-dialect envelope and warnings.
- A dependency-free, directive-driven executable `tests/fake_primesim.py`, flat
  provenance-tracked synthetic decks, and local real-subprocess behavior tests.
- Base-tier adapter/stub tests plus opt-in real-companion and live-SSH tiers.
- The pinned companion installer and the expanded single-object `status` JSON report.

## Verification

No companion package was installed or imported, and no SSH host was contacted.
The collected suite contains 120 tests, exceeding the required G0-plus-25 floor.

- BASE tier: **116 passed, 4 skipped**. The skips are two real-companion tests, one
  companion live-SSH test, and one OpenSSH live test.
- BASE tier with the kill switch set for the whole run: **116 passed, 4 skipped**.
- Companion tier: not run in this sandbox; owner-session responsibility.
- Live-SSH tier: not run in this sandbox; owner-session responsibility.

The machine had no single Python installation containing both pytest and pydantic.
The runs therefore used Python 3.11 plus already-extracted, read-only package-cache
entries through `PYTHONPATH`; no network or package installation was used. A temporary
`/tmp/psb-g1-bin/python` symlink made the required `python -m pytest` invocation
available. The exact criterion 1 invocation was:

```sh
PSB_DEPS=/Users/rick/.cache/uv/archive-v0/nWhO1PiKgIG07K01:/Users/rick/.cache/uv/archive-v0/tLF7JnNkAm7m-pSX:/Users/rick/.cache/uv/archive-v0/IOycwRUvPOgWD6Jl:/Users/rick/.cache/uv/archive-v0/MQwQl_7nu0LL24c3:/Users/rick/.cache/uv/archive-v0/s8FMujIdIDbRzwyo:/Users/rick/Library/Python/3.9/lib/python/site-packages
PATH="/tmp/psb-g1-bin:$PATH" PYTHONPATH="src:$PSB_DEPS" python -m pytest tests/ -q
```

The exact criterion 4 invocation was:

```sh
PSB_DEPS=/Users/rick/.cache/uv/archive-v0/nWhO1PiKgIG07K01:/Users/rick/.cache/uv/archive-v0/tLF7JnNkAm7m-pSX:/Users/rick/.cache/uv/archive-v0/IOycwRUvPOgWD6Jl:/Users/rick/.cache/uv/archive-v0/MQwQl_7nu0LL24c3:/Users/rick/.cache/uv/archive-v0/s8FMujIdIDbRzwyo:/Users/rick/Library/Python/3.9/lib/python/site-packages
PATH="/tmp/psb-g1-bin:$PATH" PYTHONPATH="src:$PSB_DEPS" PSB_NO_COMPANION=1 python -m pytest tests/ -q
```

Additional checks passed: Python 3.9 compileall, `git diff --check`, protected-file
integrity, companion-reference isolation, flat fixture-manifest exactness, the absent
companion `status` JSON assertion, and executable working-tree modes for the fake and
installer.

## Owner-session optional-tier commands

Run the companion tier from a fresh environment as follows:

```sh
python -m pip install -e ".[dev]"
scripts/install_companion_pin.sh
RUN_COMPANION_TESTS=1 python -m pytest tests/ -q
```

Run both live transports against the selected cluster host as follows (set
`PSB_TEST_SSH_USER` too when the SSH configuration does not supply it):

```sh
RUN_COMPANION_TESTS=1 RUN_LIVE_SSH_TESTS=1 PSB_TEST_SSH_HOST=your-host PSB_TEST_SSH_USER=your-user python -m pytest tests/test_companion.py tests/test_live_ssh.py -q
```

## Deviations and blockers

There are no implementation deviations from the G1 specification. The package-cache
test bootstrap above is an execution-environment detail, not a project dependency or
product change.

The implementation, notes, executable files, and both required BASE-tier runs are
complete, but the required final Git commit could not be created. The managed sandbox
exposes `.git` read-only: `git add` failed with
`Unable to create '.git/index.lock': Operation not permitted`. The worktree remains
unstaged on `master`; no commit was created and nothing was pushed. The fake and
installer have working-tree mode 100755, but their committed modes cannot be verified
until the owner stages them. Per the blocker protocol, no alternate Git directory,
index, or ref-writing workaround was attempted.
