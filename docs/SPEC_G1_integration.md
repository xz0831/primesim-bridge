# SPEC: primesim-bridge G1 — companion adapter, fake-primesim behavior tests, live-SSH tier

> Owner: main session (design) → Codex (implementation). 2026-08-27, **v2** (v1 was
> adversarially reviewed against the companion source at the pinned SHA and the real G0
> tree; 29 defects fixed — including two G0 latent bugs this spec now SANCTIONS
> amending, see "Sanctioned G0 amendments").
> Context: G0 (docs/SPEC_G0_core.md, commit 6a46119) delivered the standalone core with
> 88 offline shape-tests. G1 adds (a) the OPTIONAL integration layer with the companion
> package `virtuoso-bridge-lite` — soft-import only; the companion repo is never
> modified and never becomes a declared dependency — and (b) a `fake-primesim` test
> double that upgrades shape-tests to BEHAVIOR tests for everything except the real
> simulator. Companion API facts below were pinned by reading the companion source at
> commit `fb5af05fe206794baa7afb90a1db70c684a9e24f`; file:line anchors refer to that
> tree. Do not guess companion APIs beyond this fact sheet.

## Verified companion facts (pinned at fb5af05f; do not re-litigate)

- Import package: `virtuoso_bridge`. **Distribution** name when installed from the
  GitHub repo: `virtuoso-bridge`, version `0.8.0`. The PyPI project
  *`virtuoso-bridge-lite`* (0.0.1, no author metadata) is NOT the real package — never
  install or reference it. Companion install = from GitHub, pinned:
  `pip install "git+https://github.com/Arcadia-1/virtuoso-bridge-lite@fb5af05fe206794baa7afb90a1db70c684a9e24f"`.
- Version detection: `importlib.metadata.version("virtuoso-bridge")` — may raise
  `PackageNotFoundError` even when the import succeeds → treat as `"unknown"`.
- `virtuoso_bridge.transport.ssh.SSHRunner` (class at ssh.py:276, ctor :279):
  `SSHRunner(host, user=None, jump_host=None, jump_user=None, ssh_key_path=None,
  ssh_config_path=None, ssh_cmd=None, timeout=600, connect_timeout=30,
  persistent_shell=False, backend=None, max_sessions=None, verbose=False)`.
  ⚠ `__init__`'s FIRST statement is `load_vb_env()` (ssh.py:295) — companion dotenv
  discovery with `override=True`. Construct it only after our own env reads are done,
  and never cache `os.environ` across its construction. It can also RAISE (e.g.
  `ValueError` on a bad `VB_SSH_BACKEND` in the loaded env) — construction goes inside
  the same guard as calls (see Design decision 5).
  Methods used by G1:
  - `run_command(command: str, timeout: float | None = None) -> CommandResult` (:683) —
    returns the REMOTE command's exit code; raises `subprocess.TimeoutExpired` on
    budget exhaustion.
  - `upload_batch(files: list[tuple[Path, str]], timeout: float | None = None)
    -> CommandResult` (:916) — installs each file at its paired remote path. Reality
    check: it issues ONE tar pipe per distinct (remote dir, local parent dir) group and
    returns the first non-zero `CommandResult`; its docstring ("all to the same remote
    dir") is stale — trust this fact sheet, not the docstring. Raises `ValueError` on
    duplicate remote basenames in one group.
  - `download(remote_path: str, local_path: Path, recursive: bool = False,
    timeout: float | None = None) -> CommandResult` (:982). With `recursive=True` the
    tar path atomically REPLACES `local_path` (renames any existing target away) — it
    does NOT merge like `scp -r dir/. target`.
  - `test_connection(timeout: float | None = None) -> bool` (:640).
  `CommandResult` = NamedTuple `(returncode, stdout, stderr)` (ssh.py:160).
  ⚠ Companion transfer helpers `shlex.quote` every remote path (transfer.py:127 etc.),
  which DEFEATS tilde expansion: a `~/…` remote path creates a literal `~` directory.
  All companion remote paths must be home-RELATIVE (`.primesim_bridge/runs/…`).
- `virtuoso_bridge.env.resolve_env_path(explicit=None, *, cwd=None) -> Path | None`
  (env.py:36) — resolves the companion `.env` path WITHOUT loading it.
  `load_vb_env` (env.py:66) MUTATES `os.environ` with `override=True` —
  primesim-bridge must NEVER call it directly (SSHRunner calls it internally; that is
  unavoidable and tolerated).
- Tier-B waveform parsing (Spectre-dialect PSF ASCII):
  `virtuoso_bridge.spectre.parsers.parse_spectre_psf_ascii(psf_path: Path)` (parsers.py:18)
  — NOT used by G1 — and `parse_psf_ascii_directory(output_dir: Path) -> dict`
  (parsers.py:70) — used by G1. It returns a plain merged signal dict and `{}` when
  nothing matches (it scans Spectre-shaped names like `*.tran.tran` — a PrimeSim tree
  may legitimately yield `{}`; that is the R1 dialect risk, surfaced via the envelope's
  `empty` flag, Design decision 4).

## Sanctioned G0 amendments (explicit carve-outs from "do not weaken G0 tests")

A. **Remote mkdir stage (G0 latent bug).** G0's subprocess remote path scp's into
   `~/.primesim_bridge/runs/<run_id>/` without ever creating it — on a real host the
   upload fails. FIX in this phase: the subprocess branch gains an
   `ssh <target> 'mkdir -p <remote_dir>'` FIRST stage; a mkdir failure reports
   `"remote upload stage failed"`. Update `tests/test_runner.py` accordingly:
   the recorded sequence becomes `["ssh","scp","ssh","scp"]` and the upload-failure
   test expects the mkdir call in `remote_cmds` (2 entries at failure). This is the
   ONLY place existing G0 assertions may be rewritten (plus amendment B).
B. **Home-relative remote dir.** Both branches use `.primesim_bridge/runs/<run_id>`
   (no `~/` prefix): ssh/scp resolve relative paths against `$HOME`, and the companion
   helpers quote `~` literally. Update G0 test expectations that encode the `~/` form.
   Record the path used in `metadata["remote_dir"]`.

## Design decisions (fixed — do not deviate)

1. **All companion access lives in ONE module**: `src/primesim_bridge/_companion.py`.
   `grep -rn "virtuoso_bridge" src/` may hit ONLY `_companion.py`.
2. **Capability probing, not version gating alone.** `_companion.py` exposes
   `CompanionInfo` (pydantic): `available: bool`, `version: str`, `verified: bool`
   (version in `VERIFIED_COMPANION_VERSIONS = ("0.8.0",)`), `capabilities:
   FrozenSet[str]` ⊆ `{"transport", "psf_ascii", "env"}`. A capability is granted only
   if the needed attributes exist and are callable AND (`verified` or
   `PSB_COMPANION_FORCE=1`). `PSB_NO_COMPANION=1` → `available=False` (kill switch).
   Env flags are truthy ONLY when the value is exactly `"1"`.
   The `import virtuoso_bridge` happens INSIDE the probe via
   `importlib.import_module`, never at module import time; the probe catches
   `Exception` (not just ImportError — stubs and real installs can raise other
   errors at import). Probe result is cached; `reset_cache()` clears it.
3. **Transport upgrade is automatic + observable.** In remote mode with capability
   `"transport"`: use the companion transport; else the G0 subprocess path.
   `metadata["transport"]` ∈ {`"companion-sshrunner"`, `"openssh-subprocess"`,
   `"local"`} — implemented as a `transport: str` keyword on `_finish_result`, passed
   at EVERY call site (including the binary-missing and FileNotFoundError early
   returns, which carry `"local"`).
4. **Tier-B is explicit opt-in per call**: `options["parse_waveforms"] = True`
   (default False). Implemented as post-processing in `run_simulation` AFTER the
   `SimulationResult` is built (do not change `_parse_artifacts`): candidate dirs =
   `sorted(d for d in prefix.parent.iterdir() if d.is_dir() and
   d.name.startswith(prefix.name + "_"))`; for each, attach
   `metadata["waveforms"][str(d)] = {"parser": "virtuoso-bridge-psfascii",
   "dialect_verified": False, "empty": <dict is empty>, "data": <dict>}`.
   Any empty result appends warning
   `"waveform parsing produced no signals (PSF dialect mismatch is unverified — G2/R1)"`.
   Requested-but-capability-absent → warning
   `"waveform parsing requested but companion package not available"` — never an error.
5. **Companion calls never propagate exceptions.** Every companion interaction —
   INCLUDING `CompanionTransport` construction — is wrapped:
   `subprocess.TimeoutExpired` → the G0 timeout path (`timeout after <N>s`);
   any other exception → stage-named FAILURE (same strings as G0:
   `"remote upload stage failed"` / `"remote ssh stage failed"` /
   `"remote download stage failed"`) with the exception text appended to `errors`.
   `run_simulation` must never raise.
6. **fake-primesim is deck-directive driven** (Deliverable 3) and dependency-free.
7. **Test tiers.** Base: always runs (includes local fake-primesim behavior tests —
   real subprocess, NOT monkeypatched). Companion tier: skipif unless
   `import virtuoso_bridge` succeeds AND `RUN_COMPANION_TESTS=1`. Live-SSH tier:
   skipif unless `RUN_LIVE_SSH_TESTS=1` AND `PSB_TEST_SSH_HOST` set. Codex runs ONLY
   the base tier; skipped tiers are the owner session's job.

## Deliverable 1 — `src/primesim_bridge/_companion.py`

- `CompanionInfo`, `companion_info()`, `reset_cache()` per Design decision 2.
- `class CompanionTransport`: ctor `(host: str, user: Optional[str], timeout: int)`
  (constructs the pinned `SSHRunner(host, user=user, timeout=timeout)`); methods
  `check() -> bool` (delegates `test_connection()`; used by live-tier skip logic),
  `run(command: str, timeout: int) -> Tuple[int, str, str]`,
  `put_batch(files: List[Tuple[Path, str]], timeout: int) -> int`,
  `get_dir(remote_path: str, local_path: Path, timeout: int) -> int` — get_dir calls
  `download(remote_path, local_path, recursive=True, timeout=timeout)`; because that
  REPLACES the target, get_dir must download into a fresh staging dir
  `local_path.parent / (local_path.name + ".dl")` and then move the staged contents
  INTO `local_path` (merging; pre-existing files under `local_path` survive), removing
  the staging dir.
  All methods normalize `CommandResult` → returncode int. `CompanionUnavailable`
  raised only if constructed while capability absent (programming-error guard).
- `parse_psf_dir(output_dir: Path) -> dict` — the Design-decision-4 envelope.
- `env_file() -> Optional[Path]` — wraps `resolve_env_path()` ONLY (never
  `load_vb_env`). Used by CLI `status`.

## Deliverable 2 — runner integration (edit `src/primesim_bridge/runner.py`)

- Apply Sanctioned amendments A and B to the subprocess branch.
- Companion branch sequence (when capability `"transport"`):
  `run("mkdir -p <remote_dir>")` → `put_batch(netlist+includes)` →
  `run("cd <remote_dir> && <wrapped cmd>")` → `get_dir(remote_dir, run_dir)`.
  Stage-failure rule identical to G0. Exit-code discrimination identical to G0:
  the run stage's returncode `255` → `"remote ssh stage failed"`; any other code is
  the simulator's and goes to `classify_exit`. Upload/download stages fail on any
  non-zero return.
- `metadata["remote_cmds"]` stays `list[list[str]]` in BOTH branches. Companion
  branch records synthetic argv-shaped entries:
  `["companion","mkdir",remote_dir]`, `["companion","put_batch",*remote_targets]`,
  `["companion","run",remote_command]`, `["companion","get_dir",remote_dir,str(run_dir)]`.
- `metadata["transport"]` and `metadata["remote_dir"]` per Design decision 3 /
  amendment B.

## Deliverable 3 — `tests/fake_primesim.py`

- Executable (`chmod +x`, mode 100755 in git — verify `git ls-files -s` shows it),
  shebang `#!/usr/bin/env python3`, stdlib only, single file, no primesim_bridge import.
- Understands (ignores everything else silently): positional deck path, `-spice`,
  `-runlvl N`, `-mode M`, `-o/-out prefix`, `-log file`, `-mt N`, `-format fmt`,
  `-aopt k[=v]` repeatable, `-afile f` repeatable.
- Deck view = deck text + appended `-afile` file contents in order; a missing or
  unreadable `-afile` target is skipped silently.
- Directive grammar: a line matches
  `^\s*\*\s*fake:(?P<key>[a-z_]+)(=(?P<value>.*))?\s*$`, case-sensitive, value
  right-stripped. Unknown keys ignored. `exit`/`sleep`/`sleep_first`/`rows` take the
  LAST occurrence; `log`/`measure` accumulate in order. `measure=NAME=VALUE` splits on
  the FIRST `=` only.
  - `* fake:exit=N` → final exit code N (after artifacts)
  - `* fake:log=TEXT` → append TEXT as a log line
  - `* fake:measure=NAME=VALUE` → one measure column
  - `* fake:rows=K` → K data rows; row i (0-based) emits VALUE+i for numeric VALUE,
    VALUE unchanged otherwise
  - `* fake:sleep=S` → artifacts are written AND file handles closed FIRST, then sleep
  - `* fake:sleep_first=S` → sleep BEFORE writing anything (incl. the log)
  - `* fake:dc_fail` → append log line `DC not converged`; exit 34 IF
    `primesim_exit_dc_fail=1` appears in any `-aopt`, ELSE continue normally (exit 0)
  - `* fake:no_artifacts` → write only the log
- Artifacts: `<prefix>.log` always (unless sleep_first still sleeping) — neutral lines
  are EXACTLY `PrimeSim fake driver`, `elaboration complete`, `analysis complete`
  (hard rule: neutral lines must not contain the substrings `error`/`warning` in any
  case) + fake:log lines + the dc_fail line when triggered; honor `-log file`.
  Measures: `primesim_measout=3` in `-aopt`s → `<prefix>.mt0.csv` (header row + rows);
  otherwise classic `<prefix>.mt0` in the documented `$DATA1`/`.TITLE`/names/values
  shape. Plus an empty `<prefix>.ic`. Create the prefix directory with
  `os.makedirs(exist_ok=True)` if missing.

## Deliverable 4 — behavior tests (`tests/test_behavior_fake.py`, base tier)

`tests/conftest.py`: session fixture that `os.chmod(fake_path, 0o755)` + asserts
`os.access(fake_path, os.X_OK)` + asserts `shutil.which("python3") is not None`
(if python3 is not on PATH that is a documented NOTES blocker, not a workaround
target). Plus `test_fake_primesim_is_executable`.
Deck fixtures live DIRECTLY in `tests/fixtures/` (no subdirectories — the G0
manifest check is non-recursive), named `fake_<scenario>.sp`, each with a MANIFEST
row whose provenance column contains the exact literal
`SYNTHETIC-FROM-DOC (assumed layout)`.
Real-subprocess scenarios via `PrimeSimSimulator(binary=str(fake_path), work_dir=tmp)`:
1. success + 2 measures → SUCCESS; `data` parsed from `.mt0.csv`;
   `metadata["transport"] == "local"`; safety `-aopt`s present in `metadata["argv"]`.
2. `fake:dc_fail`, default safety → exit 34 → FAILURE, error mentions
   "DC not converged".
3. `fake:dc_fail`, safety suppressed
   (`extra_args=["-aopt","primesim_exit_dc_fail=0"]`) → exit 0; DC line → warning;
   status SUCCESS. (Pair 2/3 proves injection changes the outcome end-to-end.)
4. `fake:exit=13` → FAILURE "Unable to converge".
5. measures + `extra_args=["-aopt","primesim_measout=0"]` → classic `.mt0` written and
   parsed to the same `data`.
6. (a) `fake:sleep_first=5` + `timeout=1` → zero artifacts → FAILURE with
   `timeout after 1s`; (b) `fake:sleep=5` + `timeout=1` → PARTIAL (artifacts exist).
7. include staging: `* fake:measure=vout=1.0` in an `-afile` include, not the deck →
   measure appears.
8. `fake:rows=3` + `fake:measure=vout=1.0` →
   `result.data == {"vout": [1.0, 2.0, 3.0]}` AND `result.metadata["_rows"] == 3`
   (the runner pops `_rows` into metadata).
9. unique-directory rerun: second call lands in `<stem>_2/`.

## Deliverable 5 — companion + live tests

- `tests/test_companion.py` — autouse fixture: `monkeypatch.delenv` both
  `PSB_NO_COMPANION` and `PSB_COMPANION_FORCE` (raising=False) and
  `_companion.reset_cache()` before AND after each test.
  Base-tier states (no real package needed):
  - absent: force ImportError (`monkeypatch.setitem(sys.modules, "virtuoso_bridge",
    None)`) → `available=False`; runner remote path uses subprocess transport
    (assert `metadata["transport"]`); tier-B request → warning, not error, and the
    base-tier empty-envelope warning path.
  - present-broken WITHOUT force: stub `types.ModuleType("virtuoso_bridge")`
    (`__path__=[]`) in sys.modules → `available=True`, `verified=False`
    (metadata lookup fails → "unknown"), `capabilities == frozenset()`.
  - present-broken WITH `PSB_COMPANION_FORCE=1`: same stub PLUS
    `sys.modules["virtuoso_bridge.env"]` exposing `resolve_env_path` and
    `sys.modules["virtuoso_bridge.spectre"]` / `…spectre.parsers` exposing
    `parse_psf_ascii_directory`, but NO `…transport.ssh` →
    `capabilities == frozenset({"env","psf_ascii"})`.
  - kill switch: stub present + `PSB_NO_COMPANION=1` → `available=False`.
  Real-companion tier (skipif per Design decision 7):
  - `companion_info()`: available, version == "0.8.0" (assert exactly — a silent pin
    drift must fail loudly), capabilities ⊇ {"transport","psf_ascii","env"}.
  - `parse_psf_dir` on a minimal synthesized Spectre-SHAPED PSF ASCII fixture
    (constructed in-test or as a MANIFEST'd fixture) → `empty is False` and ≥1 signal.
  - When ALSO in the live-SSH tier: `CompanionTransport.check()` gates; put/run/get
    round-trip on a temp dir asserting pre-existing local files SURVIVE `get_dir`
    (the replace-vs-merge trap); then full fake-over-companion E2E: pre-upload the
    fake via `put_batch([(fake_path, f"{remote_dir}/fake_primesim.py")])`, then
    `run(f"chmod +x {remote_dir}/fake_primesim.py")`, skip-with-reason if
    `run("command -v python3")` non-zero, construct the simulator with
    `binary=f"{remote_dir}/fake_primesim.py"` → assert
    `metadata["transport"] == "companion-sshrunner"` and SUCCESS data.
- `tests/test_live_ssh.py` (live-SSH tier): same fake E2E over the G0 subprocess path
  (scp/ssh) — validates amendments A/B against a real host.

## Deliverable 6 — `scripts/install_companion_pin.sh`

New `scripts/` dir. `set -euo pipefail`, `chmod +x`, installs the pinned git SHA with
a comment warning that PyPI's `virtuoso-bridge-lite` is not the real package. Never
executed by the test suite.

## Deliverable 7 — CLI (`src/primesim_bridge/cli.py` edits)

- `run` gains `--waveforms` → `parse_waveforms`.
- `status` adds a `"companion"` KEY to the existing single JSON object (no extra
  printed lines): `{"available": bool, "version": str, "verified": bool,
  "capabilities": sorted(list), "env_file": str|null}` — capabilities converted to a
  sorted list BEFORE json.dumps (a frozenset would raise). Never raises when absent.

## Execution after implementation

> Codex runs the BASE tier only: `python -m pytest tests/ -q` in whatever environment
> already has pytest + pydantic (a fresh-venv install is the OWNER session's check —
> the sandbox has no package index; record the exact invocation used in NOTES_G1).
> The fake-primesim tests are local subprocess exec with no network and must pass in
> the sandbox. Companion/live-SSH tiers will show SKIPPED — correct; do not install
> the companion or reach any SSH host.
> Owner session afterwards: fresh-venv run, companion tier via
> `scripts/install_companion_pin.sh`, live-SSH tier against a cluster host, packaging
> check, skill doc.

## Constraints

- Do NOT modify: `.claude-plugin/plugin.json`, `pyproject.toml`,
  `skills/primesim/SKILL.md`, `README.md`, `LICENSE`, `docs/SPEC_G0_core.md`,
  `docs/NOTES_G0.md`, this spec.
- Runtime dependency stays `pydantic` only; the companion is never a declared
  dependency. Python ≥ 3.9; the pydantic/PEP-604 field rule from G0 applies.
- G0 tests keep passing UNCHANGED **except** the explicitly sanctioned amendments A/B
  (remote mkdir stage + home-relative remote dir) and additive metadata expectations.
  Weakening any other assertion is forbidden.
- ONE commit on `master`:
  `feat: G1 — companion adapter, fake-primesim behavior tests, live-SSH tier`.
  Do not push. (If the sandbox blocks `.git`, record in NOTES and stop — the owner
  session commits.)
- Blocker protocol: `docs/NOTES_G1.md`, then stop. No improvised workarounds.

## Acceptance criteria

1. `python -m pytest tests/ -q`: all non-skipped green; skips belong ONLY to the
   companion/live-SSH tiers; total collected ≥ 88 + 25.
2. `grep -rn "virtuoso_bridge" src/ | grep -v _companion.py` → nothing.
3. Scenario pair 2 vs 3 (same deck directive, only the suppression arg differs) yields
   FAILURE vs SUCCESS.
4. The suite ALSO passes with `PSB_NO_COMPANION=1` set for the whole run (adapter
   tests self-clear env per their autouse fixture); record the invocation in NOTES.
5. `primesim-bridge status` with the companion absent: exit 0, output is ONE JSON
   object, `json.loads(out)["companion"]["available"] is False`.
6. `tests/fake_primesim.py` is committed with git mode 100755.
7. `docs/NOTES_G1.md`: what was built, per-tier test counts (passed/skipped),
   deviations + reasons, the exact owner-session commands for the companion and
   live-SSH tiers, and the invocations used for criteria 1 and 4.
