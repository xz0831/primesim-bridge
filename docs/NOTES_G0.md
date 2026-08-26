# G0 implementation notes

## What was built

G0 adds the self-contained `primesim_bridge` core:

- Pydantic result and remote models, the complete documented exit-code map, and exit classification.
- Pure PrimeSim engine/accuracy and command-line construction with default SPICE selection, safety-option injection, suppression detection, and waveform-format validation.
- Tier-A parsers for CSV and classic measure results, logs, and best-effort ASCII operating-point data, plus output discovery for both naming conventions and compressed text twins.
- A deterministic local/remote runner with one subprocess seam, environment-setup wrapping, include staging, timeout handling, output collection, and exit-code-first result assembly.
- The `run`, `parse`, and `status` CLI commands, including a filesystem-free dry run.
- Provenance-tracked fixtures and offline tests covering all G0 deliverables.

## Verification

The repository-local Python 3.11 `.venv` was populated offline and installed with `pip install -e ".[dev]"` using wheels reconstructed from the machine's read-only package cache because outbound package-index access is unavailable in the sandbox.

- `python -m pytest tests/ -q`: **88 passed**.
- Python 3.9 `compileall` with a temporary bytecode cache: passed.
- Editable console entry point: `primesim-bridge run tb.sp --dry-run` passed and created no netlist or output directory.
- `pip check`: no broken requirements.
- Complete exit-code-set assertion, forbidden-source-string scan, single-subprocess-seam scan, fixture ignore check, and `git diff --check`: passed.

No real PrimeSim binary, license server, SSH endpoint, or simulator output was used, as required for G0.

## Deviations

There are no implementation deviations from `SPEC_G0_core.md`.

The initial ordinary pip attempt could not resolve the Hatch build backend because network access is disabled. The final local-venv install used the same cached package distributions repacked as ordinary temporary wheels; this changes neither project files nor declared dependencies.

## Hard blocker

The implementation, notes, fixtures, and 88-test offline suite are complete, but the required final Git commit could not be created. The managed sandbox exposes `.git` read-only: `git add` failed with `Unable to create '.git/index.lock': Operation not permitted`. The worktree files remain unstaged on `master`, and no commit was created or pushed. Per the blocker protocol, no alternate Git directory, index, or ref-writing workaround was attempted.

## Assumed details and G2 checks

The following items are deliberately implemented as specified even though G0 cannot confirm them live:

1. Repeated `-afile` accepts multiple appended files. G2: run a deck with two independently required append files and verify both affect elaboration.
2. The five documented token-name strings are useful as opportunistic `lmstat` matches, while zero matches is normal. G2: compare `status` output with the installed license tooling and an observed PrimeSim checkout.
3. Measure-output mode 3 may create CSV twins for DC and AC measure files as well as TRAN. G2: run one measured DC, AC, and TRAN analysis and record the exact filenames.
4. Measure CSV uses one measure-name header followed by one row per sweep point. G2: capture unswept and swept mode-3 output and compare column/row shape and quoting behavior.
5. A case-insensitive `failed` CSV cell denotes a failed measure. G2: force a failed measure and record its literal cell value and surrounding row.
6. Generic `0 errors` and `0 warnings` lines, if present, are summaries rather than findings. G2: inspect real logs across successful, warning, and failing runs and confirm whether these lines occur and should be suppressed.

The `.gz` versus `.gzip` compressed-text suffix is also unconfirmed, so both are accepted. G2 should run `-gz` and the netlist gzip option and record every emitted suffix. The ASCII `.op0` line layout is undocumented; G2 should capture output with operating-point ASCII explicitly enabled and revise only the best-effort parser if needed.

## G2-unverifiable behavior checklist

G0 runner, environment-setup, and remote tests assert command shape only. G2 must verify:

- POSIX shell and csh setup-script sourcing in the target installation.
- OpenSSH upload, remote working-directory behavior, simulation exit propagation, and artifact download semantics.
- Real PrimeSim artifact contents for every Tier-A format and naming variant.
- The actual compressed-output suffix and transparent parsing of those files.
- `lmstat` visibility and text for the five candidate feature/token names.

G2 should additionally exercise both engines and their accuracy controls, caller-supplied prefixes and lock behavior, parallel `-wait` classification, DC non-convergence with and without the injected exit option, and custom log paths against a licensed installation.
