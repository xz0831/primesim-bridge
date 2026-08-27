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
| **HSPICE** | `--engine hspice` | netlist-only (`.option runlvl`, default 5 — no CLI flag; `-hpp` does not exist in Y-2026) | HSPICE-qualified flows, Monte Carlo (`SWEEP MONTE=`) |
| **XA** (FastSPICE) | `--engine xa` | `set_sim_level` / `-sim_mode` via netlist/command file | large mixed-signal; Linux-only — remote/LSF execution |

XA specifics: dialect via `--dialect {hspice,spectre,eldo}` (hspice default);
classification is log-first (`Error:` lines; exit codes are undocumented) with a
multicore-only `Total Wall Time` success proxy. Safety injection is a `-c`
command file forcing `set_meas_option -format hspice` — without it XA's native
`.meas` is collected but stays unstructured (raw_lines). Never point `-o` at an
existing directory (XA would scatter `xa.*` inside it); alter (`.a#`) measure
aggregation is not yet supported (E2 limitation).

HSPICE specifics: binary resolved via `--binary` / `VB_HSPICE_BIN` / `hspice`;
classification is exit-code-first (documented table, signal codes normalized) plus
the `***** job concluded` banner double-check — exit 0 without the banner is
flagged PARTIAL. Safety injection is `-include_first` with
`.option measform=3` (CSV measures) + `.option measfail=1` (without measfail a
failed measure silently reads `0.0e0`); suppress with `no_safety=True`.
`include_files` ride `-include_last`. MC results arrive as multi-row measures
(the normal sweep shape); alter runs land in `metadata["alter_measures"]`.
Prefixes must not contain a period (HSPICE truncates the output root at the last
`.`). LSF wrappers, remote SSH, and the WaveView handoff work identically to the
other engines.

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

## LSF / shared-filesystem sites (no SSH to compute nodes)

Many EDA sites run tools on LSF compute nodes that users cannot SSH into (Virtuoso
there is bridged via direct TCP, e.g. a site `bridge_setup` layer). In that topology
do NOT use this bridge's SSH remote mode at all — run in **local mode on a
submission-capable host** over the shared filesystem:

- The Virtuoso→PrimeSim handoff still works unchanged: `si` writes the HSPICE netlist
  to the shared FS and this bridge reads it from the same paths.
- LSF enters through the `binary` parameter — point it at a **synchronous** site
  wrapper (verified pattern; a masking variant is exercised in `tests/test_lsf_pattern.py`):

  ```sh
  #!/bin/sh
  # bsub_primesim.sh — synchronous LSF submission wrapper
  trap 'test -n "$LSB_JOBID" && bkill "$LSB_JOBID" 2>/dev/null' INT TERM
  exec bsub -I -q normal -J primesim_bridge primesim "$@"
  ```

  `bsub -I`/`-K` preserve the tool's exit code; PrimeSim's own `-np lsf` /
  `-mt lsf` flags can additionally be passed via `extra_args`.
- If the site path involves `-wait`-style submission (exit code always 0), pass
  `options={"is_parallel_wait": True}` so classification uses the log — without it a
  masked failure degrades to PARTIAL at best.
- Shield against env leakage from a shared virtuoso-bridge `.env`: an exported
  `VB_REMOTE_HOST` would push this bridge into SSH mode. Set `PSB_REMOTE_HOST=`
  (empty) to force local mode regardless; `PSB_REMOTE_HOST/PSB_REMOTE_USER` take
  precedence over `VB_*` when both exist.
- Async submit-and-poll (`bsub` + `bjobs`) is NOT implemented — keep submissions
  synchronous, or build the polling in the site wrapper.

## WaveView handoff (human waveform review)

The agent judges from scalars; humans review waveforms in Synopsys WaveView. The
bridge automates that handoff without ever hand-writing the (undocumented) `.sx`
session format — it emits a documented ACE (Tcl) script and lets WaveView create
the session itself:

```bash
primesim-bridge waveview runs/tb/tb --deck tb.sp   # or: options={"waveview_script": True}
# → runs/tb/tb_waves.tcl  (opens FSDB, displays .probe'd signals, saves tb.sx)
wv -k -ace_gui runs/tb/tb_waves.tcl                # first time (generates tb.sx)
wv -x runs/tb/tb.sx                                # reopen the same run
wv -y runs/tb/tb.sx runs/tb_2/tb.fsdb              # re-apply layout to a NEW run
```

Signal selection: `--signals` list → else `.probe`/`.print` mined from the deck
(explicit paths preferred — huge FSDBs load scopes lazily) → else file-open only.
Sessions are saved with `-relpath` for shared-filesystem portability. Whether a
generated script opens in a licensed WaveView is the one on-site check remaining
(G2-class); generation itself is offline-tested. Default FSDB waveform output is
untouched by this bridge, so ad-hoc `wv run.fsdb` habits keep working.

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
