# primesim-bridge

Drive **Synopsys PrimeSim** (Pro / SPICE) from Python and coding agents (Claude Code, Cursor, ...).

Companion plugin to [virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite):
build schematics in Cadence Virtuoso through virtuoso-bridge-lite, export an HSPICE-format
netlist with `si`, and hand it to PrimeSim through this bridge — PrimeSim's native dialect,
no netlist translation involved. Both packages share the same `VB_*` environment conventions,
so one `.env` / profile drives both. Each also works standalone.

> Status: pre-release. Core runner/parsers are under construction (G0); everything is
> unit-tested offline, but live verification against a licensed PrimeSim installation
> is still pending — see `docs/SPEC_G0_core.md`.

PrimeSim is a trademark of Synopsys, Inc. This project is not affiliated with or endorsed
by Synopsys. It drives the tool strictly through its documented command-line interface.
