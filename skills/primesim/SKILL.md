---
name: primesim
description: "Run Synopsys PrimeSim (Pro/SPICE) simulations from HSPICE-format netlists and parse measure/log results. TRIGGER when the user wants to run PrimeSim, simulate an HSPICE netlist (.sp), continue a Virtuoso si-exported netlist into simulation, check PrimeSim availability/license env, or parse existing PrimeSim outputs (.mt0/.mt0.csv/.log). Works standalone or as a companion to the virtuoso skill (schematic → si netlist → PrimeSim). For Spectre simulation use the spectre skill instead."
---

# PrimeSim Skill

Drive Synopsys PrimeSim through `primesim-bridge` — locally or over SSH — and read
back scalar results (measures, log, exit codes). The scalar tier is the product:
waveform parsing is optional and gated (see Tier-B below).

## Before you start

1. `primesim-bridge status` — one JSON object: binary presence, which `VB_*`/license
   env vars are set, and a `companion` block (see below). Never fails hard.
2. `primesim` must be on PATH, or set `VB_PRIMESIM_BIN`, or set `VB_SYNOPSYS_SETUP`
   (+ `VB_SYNOPSYS_SETUP_SHELL=sh|csh`) to an environment script that provides it.
3. Remote execution: `VB_REMOTE_HOST` / `VB_REMOTE_USER` (same conventions as
   virtuoso-bridge-lite — one `.env` can drive both bridges).
4. **Never `pip install virtuoso-bridge-lite` from PyPI** — that name is a stale
   placeholder (0.0.1, no author). The real companion installs from GitHub:
   `scripts/install_companion_pin.sh` (pinned SHA).

## Core pattern

```python
from pathlib import Path
from primesim_bridge.runner import PrimeSimSimulator

sim = PrimeSimSimulator.from_env(work_dir=Path("./runs"))
result = sim.run_simulation(Path("tb.sp"))

if result.ok:
    vout = result.data["vout"]        # from .mt0.csv (measout=3 injected)
else:
    print(result.errors)              # exit-code meaning first, log detail second
```

CLI equivalents:

```bash
primesim-bridge run tb.sp                    # SPICE engine, safety options injected
primesim-bridge run tb.sp --dry-run          # print exact argv, touch nothing
primesim-bridge run tb.sp --runlvl 6         # sign-off accuracy dial
primesim-bridge parse runs/tb/tb             # re-parse existing artifacts
```

## Engines and accuracy (UNBENCHED — no measured ladder yet)

| engine | select | accuracy knob | use for |
|---|---|---|---|
| SPICE (**our default**) | `-spice` (auto) | `--runlvl 1..6` (default 4; 6 = most accurate) | verification, sign-off-ish runs |
| Pro (FastSPICE) | `--engine pro` | `--mode prohd/promd/proxd/spicehd/spicemd/spicexd` | big mixed-signal, exploration |

The binary's OWN default is Pro (FastSPICE) — this bridge deliberately flips the
default to `-spice` so verification runs don't silently downgrade accuracy. Unlike the
spectre skill's preset table, NO measured accuracy/speed ladder exists yet for these
knobs: treat the table as documentation, not calibration, until a licensed-environment
bench (G2) lands.

## Result contract

- `result.status`: SUCCESS / PARTIAL / FAILURE. Exit codes are the primary contract
  (PrimeSim documents them; e.g. 13 = convergence, 15 = license, 34 = DC not
  converged). Exit 0 with error lines in the log → PARTIAL — check `result.errors`.
- Safety injections (visible in `metadata["argv"]`, suppressible by passing the same
  `-aopt` name in `extra_args`):
  - `-aopt primesim_exit_dc_fail=1` — makes DC non-convergence a deterministic
    exit 34 instead of the tool's default warn-and-continue.
  - `-aopt primesim_measout=3` — measure results as `.mt0.csv` (machine-stable CSV).
- Network-parallel runs (`-wait`) ALWAYS exit 0 — pass
  `options={"is_parallel_wait": True}` so classification uses the log instead.
- `metadata["transport"]`: `local` / `openssh-subprocess` / `companion-sshrunner`.

## Companion integration (virtuoso-bridge-lite installed alongside)

`primesim-bridge status` → `companion.available/verified/capabilities`. When present:
- remote runs automatically use its SSH transport (tunnels/profiles shared with the
  virtuoso skill's bridge); `PSB_NO_COMPANION=1` forces standalone behavior.
- **Tier-B waveforms**: `--waveforms` (CLI) / `options={"parse_waveforms": True}` runs
  its PSF-ASCII parser over waveform subdirectories. Results carry
  `dialect_verified: false` — whether PrimeSim's psfascii matches the Spectre dialect
  is UNVERIFIED (an empty result adds a warning, not an error). Prefer measures.

Workflow handoff from the **virtuoso** skill: export the schematic netlist with the
`si` batch netlister in HSPICE format — that is PrimeSim's native dialect — then run
it here. No netlist translation is involved.

## Gotchas (verified in our tests unless marked)

- Remote `binary` paths must be ABSOLUTE: execution is `cd <run_dir> && <binary> ...`,
  so a home-relative binary resolves against the run dir and fails with exit 127.
- Remote paths are home-RELATIVE (`.primesim_bridge/runs/...`) — never `~/`-prefixed
  (the companion transport shell-quotes paths, which defeats tilde expansion).
- Parameters: there is no `-param`. Bake `.param` into the netlist or pass a generated
  file via `options={"include_files": [...]}` (appended with `-afile`).
- Compressed text outputs may be `.gz` or `.gzip` (both handled; real suffix is a G2
  question).
- What is NOT yet verified against a real PrimeSim: the (ASSUMED) items and G2
  checklists in `docs/NOTES_G0.md` / `docs/NOTES_G1.md` — measure CSV exact layout,
  `failed` cell form, `-afile` repeatability, lmstat feature visibility, psfascii
  dialect. Do not present results as silicon-grade until G2 closes.

## Related skills

- **virtuoso** — schematic/layout/Maestro control and `si` netlist export (companion)
- **spectre** — Cadence Spectre runs from `.scs` netlists (same repo as virtuoso)
- **netlist** — semantic cleanup of exported netlists before simulation
