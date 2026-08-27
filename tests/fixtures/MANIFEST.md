# G0 fixture manifest

| Filename | Represents | Provenance |
|---|---|---|
| `classic.mt0` | Classic single-row TRAN measure output | Layout mirrors PrimeSim User Guide p.563 example |
| `fake_dc_fail.sp` | Fake-driver DC non-convergence behavior deck | SYNTHETIC-FROM-DOC (assumed layout) |
| `fake_exit.sp` | Fake-driver explicit exit-code behavior deck | SYNTHETIC-FROM-DOC (assumed layout) |
| `fake_include.sp` | Fake-driver appended-include behavior deck | SYNTHETIC-FROM-DOC (assumed layout) |
| `fake_rows.sp` | Fake-driver multi-row measure behavior deck | SYNTHETIC-FROM-DOC (assumed layout) |
| `fake_sleep.sp` | Fake-driver post-artifact timeout behavior deck | SYNTHETIC-FROM-DOC (assumed layout) |
| `fake_sleep_first.sp` | Fake-driver pre-artifact timeout behavior deck | SYNTHETIC-FROM-DOC (assumed layout) |
| `fake_success.sp` | Fake-driver successful CSV/classic measure behavior deck | SYNTHETIC-FROM-DOC (assumed layout) |
| `malformed.mt0` | A measure file lacking the documented classic envelope | SYNTHETIC-FROM-DOC (assumed layout) |
| `measure_failed.mt0.csv` | CSV measure output containing a failed measure | SYNTHETIC-FROM-DOC (assumed layout) |
| `measure_single.mt0.csv` | Single-row CSV measure output | SYNTHETIC-FROM-DOC (assumed layout) |
| `measure_sweep.mt0.csv` | Multi-row swept CSV measure output | SYNTHETIC-FROM-DOC (assumed layout) |
| `operating_point.op0` | Externally produced ASCII operating-point key/value output | SYNTHETIC-FROM-DOC (assumed layout) |
| `simulation.log` | Log containing the documented divergence and DC non-convergence signatures | Signature text mirrors PrimeSim User Guide pp.250, 253; surrounding layout is SYNTHETIC-FROM-DOC (assumed layout) |
