# Backtest governance library: design spec (v0.1 revised)

Nov 18, 2026 · @ciaran gaffney

## Revision log (18 Nov 2026)

Four changes came out of review. Three more follow from them and are mine, so check those before building.

| # | Change | Where it lands |
| --- | --- | --- |
| 1 | Prediction sealed before the run: each run writes an `open` row at start and a `close` row at end | Core concepts; R5, R7, R10 to R13; File formats; Python API |
| 2 | Truncation stated plainly; partial v0.1 fix by recording the ledger head hash outside the repo | Scope and what v0.1 does not prove; CLI `status` |
| 3 | Rules Python cannot enforce are called `declared` or `detectable`; audit-hook detection to test first | R8; Python API dry runs; Open decisions |
| 4 | Git `user` field marked self-reported; signature status recorded as an optional upgrade | File formats (manifest); Using it across QuantSoc |

Added by me to keep the spec consistent:

- (a) `event` and `type` are separate fields. A run now has two rows, so one field cannot hold both the row kind and the run type.
- (b) New `abandon` command. Without it, a hard-killed run would fail `verify` forever.
- (c) The open manifest and the close file are hashed into the chain. Without this, edits to results after close would go undetected.

**Third review (18 Nov 2026):** the R3b refusal names each offending file and both fixes. `init` creates `scratch/`, which git ignores, as the home for throwaway files. The README gets a "Common refusals" section.

**Second review (18 Nov 2026):** `void` needs a fix commit, allows one per claim, and voided runs still count for budget and for v0.2's N. R3b refuses untracked Python files before the run. R17 moves under "While the run is open" and keeps its number.

**Review fixes (18 Nov 2026):** R3 excludes BGL's own files, and the script must be tracked. A ledger lock and `fsync` cover concurrent appends (R18). Untracked code imported during a run is refused (R17). `void` allows a bug rerun, but only for crashed or invalid runs (R6). An `abandon` from another machine needs `--force`, and a late close is flagged. Exploratory runs use `X`. Legacy IDs are accepted and their counts kept. Canonical JSON is pinned. C6 and C7 are confirmed.

**Decided 18/11/26, after review:** unclosed runs fail `verify` by default and are cleared only by `abandon` with a written reason. A run whose lock is held on this machine is reported as in progress.

**Added by me:** a lock file, not the process ID, marks a live run. The standard library cannot read a process's start time portably, and PIDs get reused, so a PID alone can mislead.

The lifecycle and roadmap diagrams from the original doc are not in this version. The original stays the design record until they are redrawn.

**Step 2 (18 Nov 2026):** config rules C1 to C9 added. C6 and C7 were added by me, not from review.

**Batch decisions (18 Nov 2026):** every open decision in the table is now decided. Rules are stated in general terms, and study names appear only in examples.

**Gate (18 Nov 2026):** a deployment gate added to v0.2 as `bgl gate STUDY`, with four stages and rules G1 to G12. Decided the same day: stage 1 has two pass levels, ledger-ordered and externally anchored, with a hand-made OpenTimestamps anchor until v0.3. The threshold lock stays at the first counted run. A late lock is logged as a deviation. Changes after the lock are refused in place; errors can be amended by deviation, and the original verdict always stands beside any amended result. The verdict is the first failing stage. Stages 2 and 3 both run and are reported, and a stage that runs after an earlier failure is labelled diagnostic. A voided holdout run counts as an opening unless its manifest proves it exposed nothing. Return series are stored as one portfolio-level daily series per counted run, in a git-ignored folder, with only the hash in the ledger. Prior trials are excluded from PBO, which states the limit, with DSR covering them. It stays inside BGL rather than becoming a separate library, because the ledger already holds the honest trial count that the gate needs. Config rules C10 and C11 added for thresholds and the holdout. The fields the statistics need are added to v0.1 now, marked *v0.1 schema change*, while no format is released. Open decisions added where the spec cannot decide alone.

## Purpose and users

BGL (Backtest Governance Library) is a Python library that makes every backtest run in a QuantSoc project **logged, budgeted, reproducible and tamper-evident**. Each run writes its prediction before it starts, and its result after it ends. Later versions correct reported results for the number of trials and register specs with an external timestamp.

- **Primary users:** QuantSoc members running research projects in Python, from first-years to experienced researchers.
- **Secondary users:** reviewers (team leads, the fund's decision makers) who check a project's claims before money is committed.
- **First user:** the regime\_consensus project, which replaces its own `runlog.py` with this library.
- **What a user gets:** one shared standard of evidence across the society, so a strategy's track record can be trusted by someone who did not watch it being built.

## The problem

Existing experiment trackers record what happened. None that we know of enforces the discipline that makes a backtest believable. The main failure in backtest research is not bad code but **unrecorded searching**: trying many variants and reporting the best (Arnott, Harvey and Markowitz, 2019).

| Need | MLflow, Weights & Biases | DVC | This library |
| --- | --- | --- | --- |
| Logs parameters and results | Yes | Partly | Yes |
| Ties each run to exact committed code | Optional | Yes | Required: refuses uncommitted changes |
| Prediction written before the run | No | No | Required |
| Run budget per study | No | No | Enforced |
| Refuses accidental duplicate runs | No | No | Yes |
| Logs crashed runs | Partly | No | Always |
| Detects edited or deleted log rows | No | No | Hash chain |
| Corrects results for the number of trials | No | No | v0.2 |
| External proof of when a spec was fixed | No | No | v0.3 |

The comparison is from general knowledge of these tools, not a fresh review. Check current features before the README makes this claim.

**Motivating case:** in regime\_consensus, a script that crashed after printing results would have left no log row, and a renamed config list caused a bug that two runs repeated before it was noticed. Both are the kind of problem this library is built to catch.

## Scope of v0.1 and non-goals

v0.1 is the smallest version that regime\_consensus can switch to and that a second QuantSoc project could adopt without changes.

**In v0.1**

- A run context manager. It checks before the run, writes an `open` row, then writes a `close` row after it, including crashes.
- An append-only ledger with a hash chain, and a `verify` command.
- A manifest per run: commit, config, data hashes, environment, prediction, result.
- Studies with separate budgets, set in one project config file.
- A command-line tool: `init`, `status`, `list`, `show`, `verify`, `note`, `abandon`, `migrate`.
- Full test coverage of every rule the library enforces.
- The fields that v0.2's statistics and gate need: the sample a run uses, its data dates, and the return-series reference. They are added now so no migration is needed (marked *v0.1 schema change* in File formats).

**Not in v0.1 (later versions)**

- Deflated Sharpe, trial counting and the deployment gate (v0.2), external registration (v0.3), a GUI (v0.4).

**Never in scope**

- **Running backtests or computing returns.** The library governs runs; it does not do the research.
- **Storing data.** It records hashes of input files, never the files, so licensed data (CRSP, IVolatility) never leaves the user's machine. The one exception is each counted run's portfolio-level daily return series, kept in a git-ignored folder (see Return series file). Per-asset returns and raw data are never stored.
- **Preventing a determined cheat.** A user can always delete the folder. The aim is that honest work is easy to prove and dishonest editing is detectable, not that it is impossible.
- **Hosting or accounts.** Everything is files in the project's git repository.

**What v0.1 does not prove**

- **When.** Open and close times are self-reported. The chain proves their order, not their date, until v0.3 anchors them externally.
- **Truncation.** Trailing rows can be deleted without breaking the chain. A partial guard: `bgl status` prints the ledger's head hash. Record it in each commit message or post it to the team channel. A later deletion then shows up as a hash that is missing. If history is rewritten and no hash was posted anywhere, the deletion is invisible. v0.3 closes this properly.
- **Undeclared reads.** Only files opened through Python are recorded, and some reads (C extensions such as pyarrow) may bypass that. See R8. The same gap limits dry-run enforcement.
- **Locks on synced or network folders.** Locks are unreliable there, so projects should not sit in them. `init` warns; see File formats.
- **Other machines.** A live run can only be seen from the machine it runs on. Another machine gets a "cannot tell" from `verify`, never a guess.

*Reason:* a library that certifies other people's honesty has to be exact about what it can and cannot show.

## Core concepts

| Term | Meaning |
| --- | --- |
| Project | One git repository with a config file at its root. |
| Study | One test design with its own design period and its own run budget (for example `equities` and `crypto` in regime\_consensus). |
| Run | One execution of an analysis script. Every run is recorded, whether it succeeds, fails or crashes. |
| Run type | `data` (checks on data, no results), `exploratory`, `diagnostic` or `confirmatory`. Only diagnostic and confirmatory runs count towards the budget. A confirmatory run is the one that decides a claim. |
| Open row | The first ledger row of a run, written before the analysis starts. It holds the prediction, inputs, commit, the host, and the lock that marks the run as live. It uses budget. |
| Close row | The second ledger row of a run, written when it ends. It holds the status, result and close file. |
| Status | `ok`, `no_result`, `crashed`, `interrupted`, `abandoned` (set by `abandon`), `invalid` (set by R17) or `refused`. |
| Unclosed run | A run with an open row and no close row, for example after a power cut. `verify` fails it unless its lock is still held on this machine. |
| Abandon | Closes an unclosed run with a written reason. The only way to clear an unclosed-run failure. |
| Prediction | What the researcher expects, written into the open row before the run starts. Required for diagnostic and confirmatory runs. |
| Budget | The maximum number of counted runs a study may open before its frozen test. |
| Ledger | The append-only log of every run event, one JSON line each, chained by hashes. |
| Manifest | The file or files holding everything needed to reproduce and audit one run. |
| Head hash | The hash of the newest ledger row. Printed by `status` so it can be recorded outside the repository. |
| Claim | A named hypothesis in a study. A confirmatory run names the claim it decides; each claim may have only one. |
| Holdout | The part of a study's data kept back for the frozen test. A run declares `sample = holdout` to read it. Holdout data starts at the study's `holdout_start`. |
| Deployment gate | The v0.2 check that decides whether a study's strategy may be deployed. Four stages, run in order, each pass or fail. |

## Functional requirements (v0.1)

Every rule below is enforced in code and has a test that fails if the rule is removed. All checks run before any user code, so a refused run has seen nothing.

**Before a run opens** (R1 to R7 and R3b)

1. **R1. Known study.** The study must be declared in the project config.
2. **R2. Budget.** If the run type counts, the study must have budget left. Budget is used at open.
3. **R3. Clean code.** The git working tree must have no uncommitted changes to tracked files, except BGL's own files: the ledger and `runs_dir`. Those change on every run, so they are excluded. The script must be tracked and unchanged. The current commit is recorded. Untracked data files are allowed. `status` warns if the ledger has uncommitted rows.

**R3b. No untracked Python files.** Before the run opens, `git ls-files --others --exclude-standard -- '*.py'` must return nothing. Any untracked, non-ignored `.py` file in the repository refuses the run, including the script itself if it is new. *Reason:* this catches almost every untracked-code case before any user code runs, so a refused run has still seen nothing. The refusal names each offending file and both fixes, for example: `Refused (R3b): untracked Python files in the repo: notebooks/try.py. Commit them, or move them to scratch/ (ignored by git).` Files in `scratch/` are git-ignored, so they pass R3b; R17 covers them if a run imports one.

4. **R4. No duplicate.** No earlier open row in the same study may have the same script and the same `what_changed`. A deliberate rerun must say why in `what_changed`.
5. **R5. Prediction present.** Diagnostic and confirmatory runs must carry a prediction.
6. **R6. One confirmatory run per claim, plus one bug rerun.** A confirmatory run must name a claim, and that claim must not already have a confirmatory open row. A claim may be voided once: after a void (`void`), the claim may have one more confirmatory run, and that rerun cannot itself be voided. A void is allowed only for a run with status `crashed` or `invalid`, and must name a fix commit made after the voided run that changes tracked code. So a result cannot be voided because the researcher dislikes it. *Reason:* a rule with no bug rerun blocks a claim forever after a bug, and regime_consensus's own spec allows one.
7. **R7. Ledger intact.** The hash chain must verify before any row is added: open, close, note or refused.
**Writing the open row** (R13 applies)

After R1 to R7 and R3b pass, the run takes its lock, the manifest is written, then the open row. The open row uses budget. From here on, every outcome except a refusal uses budget, because the researcher may have seen output before the run stopped.

**While the run is open** (R8, R9 and R17 keep their numbers for stable references)

**R8. Inputs declared.** The script declares its input files. Their SHA-256 hashes are computed when the open manifest is written and stored in it. A run whose declared input is missing is refused before the open row is written.

**R9. Config captured.** The settings the script passes are stored in the open manifest.

**R17. No untracked code (backstop).** While the run is open, the import audit event is recorded. If the run imports a `.py` file inside the repository that git does not track, the run is closed as `invalid`, with the file path as the reason. This backs up R3b: it catches imports from git-ignored folders inside the repository, which R3b does not see. It is why `scratch/` is safe: a file there passes R3b, but importing it closes the run as `invalid`. Code outside the repository is not checked. Budget is used, under R12.

**When the run ends**

10. **R10. Always recorded.** A finished run gets a close row with its status and result. A run that raises an exception is closed as `crashed`, with the error type and message, and the exception is re-raised. A run interrupted with Ctrl+C is closed as `interrupted`. If the run was already closed by `abandon`, its close is written as `late_close` instead, and `verify` flags it.
11. **R11. Result required.** A run that finishes without setting a result is closed as `no_result`, not as `ok`.
12. **R12. Counts as used.** Budget used at open stays used, whatever the status or any void. A voided run still counts as a trial in v0.2's N. *Reason:* the researcher may have seen output before the run stopped.
13. **R13. Atomic writes.** Each write goes to a temporary file that is then renamed into place. At open, the open manifest is written, then the open row. At close, the close file is written, then the close row. A crash between the two leaves an orphan file with no row, which `verify` reports. There is never a row without its file.

**Unclosed runs.** A run with an open row and no close row is unclosed. A hard kill (power cut, `kill -9`) produces one. A live run is identified by its lock, not by a process ID. Each run holds an OS lock on its lock file for its whole life. `verify` tries to take that lock without waiting:

- **Lock taken by `verify`:** the process that held it is dead. The run is unclosed, and `verify` fails it.
- **Lock refused:** a live process still holds it. The run is in progress, not a failure.
- **Another machine, or a synced or network folder:** `verify` cannot see the lock, so it cannot tell. It says so and fails, and asks the user to run `verify` on the machine where the run happened, or to abandon the run.

Clearing a failure needs `abandon RUN_ID` with a reason. It appends a close row with status `abandoned` and the reason. It is refused while the lock is held, and on another machine unless `--force` is given with a reason. The run keeps its budget, since its output may have been seen. *Reason:* a warning gets ignored, and a silent unclosed run is the gap the open and close rows exist to expose.

**Ledger rules**

14. **R14. Append only.** The library never edits or deletes a row. Corrections are new `note` rows, `abandon` rows, or `void` rows.
15. **R15. Hash chain.** Each row stores the SHA-256 of its own contents plus the previous row's hash. A close row also stores the SHA-256 of its close file, and an open row stores the SHA-256 of its open manifest, so editing either file is detected. `verify` recomputes the chain and reports the first broken row.
16. **R16. Unique IDs.** Run IDs are unique across the project: a per-study prefix, a counter, and the researcher's chain code (for example `EQ-R001-CG`, `EQ-D001-CG`). The chain code keeps IDs unique when several chains exist. Examples elsewhere in this spec omit it. *Chain code added by me; confirm.* The open and close rows of one run share its ID. Existing IDs never change. Imported rows keep their original IDs, which the ID check accepts.
18. **R18. Ledger lock.** Reads, verification and appends happen under a ledger lock, `.bgl/ledger.lock`, held with the same OS lock as run locks. Each append is flushed with `fsync` before the lock is released. Two runs appending at once take turns: neither row is lost, and both chain onto the same head.

**Lifecycle**

| Outcome | Rows written | Budget used |
| --- | --- | --- |
| Refused (R1 to R8 fail) | One `refused` row with the reason | No |
| Opened, then ends `ok`, `no_result`, `crashed` or `interrupted` | Open row, then close row | Yes |
| Opened, still running, lock held | Open row only. `verify` reports it in progress | Yes |
| Opened, then hard kill, lock free on this machine | Open row only. `verify` fails it until abandoned | Yes |
| Opened, then hard kill, checked on another machine | Open row only. `verify` fails it until abandoned | Yes |
| `abandon RUN_ID` with a reason | Close row with status `abandoned` | Already used |
| Untracked repository code imported mid-run (R17) | Open row, then close row with status `invalid` | Yes |
| `void RUN_ID --fix COMMIT`, on a crashed or invalid confirmatory run | Note row with event `void` and a fix commit | Already used; still counts as a trial |

## File formats

All files live in the project's git repository and are committed, so git history backs up the hash chain. Git history alone does not prove the chain is complete; see the truncation limit in Scope.

**Project config** (`bgl.toml` at the repository root)

```toml
[project]
name = "regime_consensus"
ledger = "Documents/ledger.jsonl"
runs_dir = "Documents/runs"

[studies.equities]
budget = 20
prefix = "EQ"
claims = ["agreement", "regime_dependence", "switching"]

[studies.crypto]
budget = 20
prefix = "CR"
claims = ["agreement", "regime_dependence", "switching"]
```

**Ledger** (`ledger.jsonl`): one JSON object per line, appended, never rewritten.

- *Reason for JSON Lines over CSV:* rows can hold nested values (a prediction with several parts), each line is independent so appending never touches earlier rows, and line-by-line git diffs show exactly what was added. A CSV export is a command (`list --csv`).
- *Reason for `event` and `type` as separate fields:* a run has an open row and a close row, so the row kind and the run type cannot share one field.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | text | Study prefix, then a letter, then a counter: `R` for counted runs, `D` for data runs, `X` for exploratory runs (`EQ-R007`). Imported rows keep their original IDs (for example `R01`) |
| `event` | text | `open`, `close`, `late_close`, `refused`, `note`, `void`, `imported` (migrate), `registration` (v0.3), or the v0.2 gate events `gate_spec`, `gate`, `incubation`, `live_return` and `deviation` |
| `study` | text | A study from the config |
| `type` | text | `data`, `exploratory`, `diagnostic` or `confirmatory`; null on note rows |
| `counts` | true/false | Whether the run uses budget |
| `claim` | text or null | The claim a confirmatory run decides |
| `script` | text | Path of the script, relative to the repository |
| `what_changed` | text | One change, in words |
| `prediction` | text or null | Written on the open row, before the run |
| `status` | text | On open rows: `open`. On close rows: `ok`, `no_result`, `crashed`, `interrupted`, `abandoned` or `invalid`. On refused rows: `refused`. A voided run keeps its status; the `void` row marks it |
| `result` | text or null | The summary the script set (close rows) |
| `commit` | text | Git commit ID the run used |
| `user` | text | Git user name and email. Self-reported unless the commit signature is good |
| `host` | text | Open rows: name of the machine that opened the run. Checked by `verify` before it looks at the lock |
| `pid` | integer | Open rows: process ID. Informational only; liveness is decided by the lock |
| `lock_file` | text | Open rows: path of the lock file. The run's process holds an OS lock on it for the run's life. Machine-local: it sits in `.bgl/locks/`, which git ignores |
| `started`, `ended` | timestamp | UTC, ISO 8601. Self-reported until v0.3 |
| `manifest` | text | Path of the open manifest |
| `manifest_sha256` | text | Open rows: SHA-256 of the open manifest |
| `close_file` | text or null | Close rows: path of the close file. Null on abandon rows |
| `close_sha256` | text or null | Close rows: SHA-256 of the close file. Null on abandon rows |
| `reason` | text or null | Refusal reason, abandon reason, void reason, or note text |
| `fix_commit` | text or null | Void rows: the commit that fixes the stated bug. It must come after the voided run and change tracked code. `verify` checks both |
| `sample` | text or null | *v0.1 schema change.* Open rows: `design` or `holdout`. Null on other rows. A holdout run may only use data from the study's `holdout_start` (C11) |
| `sample_start`, `sample_end` | date or null | *v0.1 schema change.* Open rows: the dates of data the run used. The statistics need these to check that trials share a common sample |
| `returns_sha256` | text or null | *v0.1 schema change (written from v0.2).* Close rows: SHA-256 of the run's return series file. The file itself is not in git (see Return series file) |
| `prev_hash` | text | The previous row's `hash` (64 zeros for the first row) |
| `hash` | text | SHA-256 of this row without `hash`. The bytes are `json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False)`, encoded as UTF-8. Hashed fields hold no floats: a fractional number is stored as a string, so every implementation writes the same bytes |

Refused runs get one `refused` row with the reason, and never use budget. *Reason:* a refusal is evidence that the rules worked.

Fields marked *v0.1 schema change* are added before v0.1 is released, so no existing row ever needs migrating. After release, the same change would need a `format_version` bump and a migration.

**Open manifest** (`runs/EQ-R007.json`): written at open and never rewritten.

- `config`: the settings the script passed.
- `inputs`: each declared input's path, size and SHA-256.
- `environment`: Python version, platform, and the SHA-256 of `uv.lock` (or the installed package list if there is no lock file).
- `git`: commit, branch, whether untracked files existed, user, and `signature`: git's own status code for the commit signature (`G` good, `B` bad, `U` good but unknown key, `N` unsigned).
- `files_opened`: files opened through Python during the run that were not declared as inputs. See R8.
- `costs` *(v0.1 schema change)*: the cost model the run declares: a per-trade cost, a slippage estimate and a sensitivity multiplier. Optional in v0.1 and recorded when given; the gate's stage 3 requires it.
- `stdout`, `stderr`, `holdout_load_offset` *(v0.1 schema change)*: the output a run produced, captured and hashed, and the position in it where the holdout was loaded. Captured for every run with `sample = holdout`. *Reason:* G3 has to prove what a run exposed, and only captured output can show it. *Stderr is my addition:* a warning sent through `logging` also counts as exposure.

**Close file** (`runs/EQ-R007.close.json`): written at close.

- `outputs`: each declared output's path and SHA-256.
- `notes`: remarks added with `run.note`. A note is lost if the run is hard-killed before close.
- `error`: type, message and traceback, for crashed runs.

**Return series file** (`runs_dir/returns/EQ-R007.csv`): *v0.1 schema change; written from v0.2.* Written at close for every counted run, including voided runs, because PBO needs every trial.

- *Content:* one portfolio-level daily return series for the run. Never per-asset returns, never raw data.
- *Git:* the `returns/` folder is git-ignored, so the series never enters the repository. `init` adds it to `.gitignore`. Manifests and close files stay committed; only this folder is ignored.
- *Backup:* the owner backs up `runs_dir/returns/` privately, for example to a university OneDrive. This is a copy of the folder, never the project itself, since the project must not sit in a synced folder (see Scope).
- *Format:* deterministic, so the hash can be reproduced. Fixed column order, UTF-8, LF line endings, ISO dates, and a fixed number of decimal places set by `format_version`. The same series must always give the same bytes.
- *Hash:* the file's SHA-256 goes into the close row. Nothing else about the series goes into the ledger.
- *Missing files:* on a machine without the series, `verify` reports them as not checkable rather than passing them. Abandoned runs with no series are listed in the gate report as "missing from PBO" and are never dropped silently.
- *Licence:* before the first counted run, the owner confirms that the data licence allows storing the derived series locally. Where a licence forbids it, that study is excluded from PBO, and the report states why.

The close row holds the SHA-256 of both the close file and the open manifest, so editing either after the fact is detected.

**The lock.** On Linux and macOS, the run holds `fcntl.flock` with an exclusive lock. On Windows, it holds `msvcrt.locking`. The lock is held on an open file, not inferred from the file's existence, because a crash leaves the file behind. Locks are unreliable on network drives and inside synced folders (OneDrive, Dropbox, iCloud Drive, Google Drive), where students often keep projects. `init` warns when the project sits in one, and the documentation says so. Lock files live in `.bgl/locks/` and are never committed.

## Config rules (bgl.toml, v0.1)

Enforced by the config loader. Each rule has a test that fails if the rule is removed. Every error names the file, the key and what was expected.

1. **C1. Project name.** `[project] name` is a non-empty string.
2. **C2. Paths.** `ledger` and `runs_dir` are relative paths. They must not be absolute, and must not resolve to a location outside the repository.
3. **C3. Study budget.** Each study's `budget` is a whole number, zero or more.
4. **C4. Study prefix.** Each study's `prefix` is 2 to 4 uppercase letters.
5. **C5. Claims.** Each study's `claims` is a list of unique, non-empty strings. An empty list is allowed.
6. **C6. Unique prefixes.** No two studies share a prefix. *Derived from R16:* run IDs must be unique across the project, and two studies with the same prefix would produce colliding IDs. *Added by me; confirm before building.*
7. **C7. Unknown keys.** A key the loader does not recognise is an error. *Reason:* a misspelt key such as `budgt = 20` would otherwise be ignored silently, and the study would run with no budget. *Added by me; confirm before building.*
8. **C8. Missing file.** If `bgl.toml` does not exist, the error tells the user to run `bgl init`.
9. **C9. Invalid TOML.** A syntax error reports the file and the line.
10. **C10. Gate thresholds.** If a study has a `[gate]` table, every threshold it needs is present and lies in its valid range. The loader knows these keys from v0.1, so C7 does not reject them. *Added for the v0.2 gate.*
11. **C11. Holdout start.** A study with holdout runs declares `holdout_start`, a date. Design runs must use data before it, and holdout runs must use data from it on. *Reason:* the holdout is only a test if the two samples cannot overlap. *Added for the v0.2 gate; enforced from v0.1 because the sample is recorded at open.*
12. **C12. Chain registry.** `bgl.toml` lists every expected ledger chain by chain code, one per researcher. `ledger` is a folder holding one `<chain>.jsonl` per registered chain, and C2 applies to it. The gate fails if a registered chain is missing. *Added for multi-researcher use.*

## Python API

One function opens a run; the run object records everything else. A script needs about five extra lines.

```python
from bgl import start_run

with start_run(
    study="equities",
    run_type="diagnostic",
    script=__file__,
    what_changed="Correlation pre-check on six signal scores",
    prediction="Bollinger dropped as a near-copy of z-score",
    inputs=["data/derived/signals.parquet"],
    config={"corr_threshold": 0.7},
) as run:
    table = compute(...)          # the analysis
    run.output("data/derived/correlations.parquet")
    run.result("Kept macd, rsi_50; dropped rate_of_change")
```

| Call | Does |
| --- | --- |
| `start_run(...)` | Runs checks R1 to R9, then writes the manifest and open row. Takes `sample` (`design` or `holdout`) and an optional `costs` record (v0.1 schema change). Raises `RunRefused` with the reason if any check fails, after recording the refusal. |
| `run.output(path)` | Declares an output file. Its hash is recorded when the run closes. |
| `run.result(text)` | Sets the summary. Required for status `ok`. |
| `run.note(text)` | Adds a remark, for example a warning seen during the run. Written to the close file. |
| `run.id` | The run's ID, for printing or naming outputs. |
| `run.returns(series, periods_per_year=252)` | v0.2. Writes the series to the git-ignored returns folder, puts its hash in the close row, and stores the series' statistics in the close file. |
| `run.holdout(...)` | *v0.1 schema change.* The only way a run with `sample = holdout` loads holdout data. It records where in the captured output the load happened. Without that marker, G3 cannot be checked. |
| `verify_ledger()` | Recomputes the hash chain. Returns the first broken row, or none. |
| `budget(study)` | Runs used and left in a study. |
| `add_note(run_id, text)` | Appends a `note` row correcting or annotating an earlier run (R14). |

- **On exit**, the run writes its close file, then its close row. An exception is recorded as `crashed` and re-raised. Ctrl+C is recorded as `interrupted`.
- **Confirmatory runs** also pass `claim="agreement"` (R6).
- **Data runs** use `run_type="data"` and need no prediction.
- **Dry runs:** `start_run(..., dry=True)` runs every check and prints what would be recorded. It writes nothing. *Reason:* developers need to check that a script starts, but a dry run that could see the data would be an unlogged look at it.

**Enforcement of reads.** Files opened through Python during a run are recorded by an audit hook (PEP 578, `sys.addaudithook`). Undeclared reads are listed in the open manifest. This is detection, not prevention: C-level readers such as pyarrow may bypass the hook, so the claim must be worded as "files opened through Python are recorded; others may not be". Test this before the README claims more. **Decided 18/11/26:** dry runs enforce this. They cannot read declared inputs, using the audit hook. The C-level reader gap applies, as listed under known limitations.

## Command-line tool

The command-line tool reads and checks; it never creates a run. Runs are only made from scripts, through `start_run`. *Reason:* a tool that could start runs would be a second route around the rules.

| Command | Output |
| --- | --- |
| `init` | Writes a starter `bgl.toml` and an empty ledger. Refuses if either exists. Creates `scratch/` and `runs_dir/returns/` and adds both to `.gitignore`, appending only if the entry is missing and never overwriting the file. Warns if the project sits inside a synced folder (OneDrive, Dropbox, iCloud Drive, Google Drive) or a network path, where locks are unreliable. |
| `status` | Budget used and left per study; claims decided and open; count of unclosed runs; voided runs; the ledger's head hash, to record outside the repository. Warns if the ledger has uncommitted rows. |
| `list [--study S] [--csv]` | One line per run, newest last. `--csv` exports for spreadsheets. |
| `show RUN_ID` | The run's open and close rows and manifests, readable. |
| `verify` | Checks the hash chain; that every close row has an open row; that every open row has a close row, or is in progress on this machine (see Unclosed runs); that every manifest and close file exists and matches its hash; that recorded commits exist in git; and that no manifest or close file is orphaned. It checks each return series file against its hash. On a machine without the series, it reports them as not checkable rather than passing them. On another machine it reports that it cannot tell whether a run is live, and fails. Flags any `late_close` row. Exits non-zero on any failure, so it can run in CI. |
| `note RUN_ID "text"` | Appends a `note` row. With `abandon` and `void`, these are the only writes the command line can make. |
| `void RUN_ID "reason" --fix COMMIT` | Appends a `void` row marking a crashed or invalid confirmatory run as a bug, which frees one more run for its claim (R6). `--fix` must name a commit made after the voided run that changes tracked code; `verify` checks both. One void per claim. The run keeps its budget and its trial count. |
| `abandon RUN_ID "reason"` | Closes an unclosed run with status `abandoned` and the reason. Refused while the run's lock is held on this machine. On another machine it is refused unless `--force` is given with a reason. Keeps the run's budget. |
| `migrate --from-csv PATH` | One-off import of an older log (used for regime\_consensus). Imported rows keep their original IDs and their `counts` values (crypto's 6 used runs still count), and are marked `imported`, since their commits and inputs were not recorded. |

## v0.2: trial counting and the deflated Sharpe ratio

v0.2 reports every strategy's Sharpe ratio next to its **deflated** Sharpe ratio, using a trial count the library already knows, so selection bias is corrected without the researcher having to remember how many things they tried.

**The method.** The deflated Sharpe ratio (Bailey and López de Prado, 2014, *Journal of Portfolio Management* 40(5), 94 to 107) is the probability that the true Sharpe ratio beats the best Sharpe ratio expected from N zero-skill trials, allowing for skewness and fat tails. The probabilistic Sharpe ratio against a hurdle SR\* is

```latex
\mathrm{PSR}(SR^*) = \Phi\left( \frac{(\widehat{SR} - SR^*)\sqrt{T-1}}{\sqrt{1 - \hat\gamma_3 \widehat{SR} + \frac{\hat\gamma_4 - 1}{4}\widehat{SR}^2}} \right)
```

and the hurdle is the expected maximum of N zero-skill trials:

```latex
SR^* = \sqrt{\mathrm{Var}\big[\widehat{SR}_n\big]} \left( (1-\gamma)\,\Phi^{-1}\!\left(1 - \tfrac{1}{N}\right) + \gamma\,\Phi^{-1}\!\left(1 - \tfrac{1}{N e}\right) \right)
```

Here T is the number of returns, gamma-hat-3 the skewness, gamma-hat-4 the kurtosis (not excess), Phi the normal distribution function, and gamma about 0.5772 the Euler-Mascheroni constant. **Check every symbol against the paper before implementing**; this is the standard published form, not a derivation.

**What the library adds** (the formula is already implemented elsewhere, for example R's quantstrat):

1. **N comes from the ledger**, not from memory: every counted run in the study that produced a return series is a trial, including voided runs.
2. **Prior untracked trials are declared once** in the config (`prior_trials = 38` for regime\_consensus's V1 and V2) and are always added to N. The key belongs to the study the trials were run for, never to another study. *Reason:* hiding earlier searches is the main way selection bias enters.
3. **The variance of trial Sharpe ratios** comes from the recorded trials. With fewer than 2 recorded trials, a stated default is used and flagged.
4. **New API:** `run.returns(series, periods_per_year=252)` writes the return series to a returns file, hashes it into the close row, and stores its Sharpe, length, skewness and kurtosis in the close file. The series is kept because PBO needs every trial's returns, which the earlier "statistics only" choice ruled out. `stats dsr RUN_ID` on the command line.

**Open questions for v0.2:** trials are rarely independent, so N overstates the effective number. López de Prado suggests clustering trials to estimate an effective N; v0.2 reports the raw N as the primary figure. Effective N may appear later, as a labelled sensitivity check only, never as the primary figure. Also read before building: Harvey and Liu (2015), *Backtesting*, on Sharpe ratio haircuts, as an alternative correction.

## v0.2: deployment gate (`bgl gate STUDY`)

The gate answers one question: may this study's strategy be deployed with real money? It computes no new kind of statistic. It combines governance, statistics, implementability and live evidence, and refuses when any part fails.

*Reason it sits in BGL and not in a separate library:* the statistics already exist elsewhere (factor-qc, sharpebench, pypbo, alphaengine). What none of them has is a trustworthy count of the trials actually run, and the ledger already records that count. The gate's contribution is that count plus the rules that stop it being gamed.

**Order and verdict.** The verdict is the first failing stage, and nothing later can change it. Stage 1 failing halts the gate before any statistic is computed. Stages 2 and 3 are independent of each other, so both always run and both are reported. Stage 4 (incubation) starts only when stages 1 to 3 all pass. *Reason for the order:* if governance fails, the statistics are computed on a record that may be incomplete, so their numbers are not evidence. Live incubation comes last because it costs the most calendar time.

**Stage 1: Governance**

- **G1. Pre-registered.** A `gate_spec` row exists in the ledger, written before the study's first counted open, and its threshold hash matches the thresholds in `bgl.toml`. *Reason:* the thresholds are the bar. If they can move after results are known, the gate can be passed by moving the bar. Stage 1 reports one of two pass levels:
  - **Ledger-ordered:** the row exists and the chain verifies. This proves order, not date. It passes the gate but is labelled *not deployment-grade*.
  - **Externally anchored:** the row exists, and the ledger head hash recorded just after it has been stamped with OpenTimestamps. The stamp's proof must verify against a Bitcoin attestation, and the stamped hash must still appear in the chain at the `gate_spec` row's position. *Reason:* a new stamp is pending until a Bitcoin block confirms it, so the proof must be upgraded before it counts. A deployment verdict requires this level.

  *Procedure, by hand until v0.3:* write the `gate_spec` row; record the head hash in a text file; stamp that file; commit the ledger, the text file and the proof; push the commit. *Reason for the push:* it keeps a copy outside the machine. GitHub history can be force-pushed, so the stamp carries the weight and the push is a backup.
- **G2. Every run closed.** No open row without a close row, no `late_close`, and no unclosed or in-progress run. *Reason:* a run still open may hold an outcome the researcher has not reported.
- **G3. Holdout opened once.** Enforced at run start: `start_run` refuses a second holdout run. The only exception is one bug rerun after a crash that exposed nothing, and when in doubt the run is refused. At gate time, G3 checks the record: count the holdout runs that exposed holdout-derived output, voided runs included. Holdout-derived output is anything printed, logged or written to a file after the holdout load. The count must be one or fewer. A voided run counts unless its manifest proves no exposure: it crashed before producing any output, nothing appears in its captured stdout or stderr after the holdout load, and it has no output artifacts. Where that proof is missing, the run counts. A counted void rerun is reported as diagnostic only, and G3 fails with "holdout opened twice". An uncounted crash allows one bug rerun, and G3 passes with a note. *Reason:* a holdout is a test only if it is looked at once, and any output a researcher could read counts as a look.
- **G4. Trial count consistent.** Recount trials from the ledger: counted runs, voided runs, and `prior_trials` from config. The N used in stage 2 must equal this recount. *Reason:* the count is the reason the gate exists, so stage 2 must use exactly the number the ledger shows.

**Stage 2: Statistical evidence**

- **G5. Deflated Sharpe ratio.** Compute DSR with N from G4 and the variance of trial Sharpe ratios from recorded trials. Pass if DSR is at least the threshold. All Sharpe ratios are per-period (the formula's T is a count of periods). Annualised figures are for display only. *Reason:* the formula needs its Sharpe and its T in the same units, or the result is wrong by roughly the square root of periods per year.
- **G6. Probability of backtest overfitting (CSCV).** Build a matrix with one column per recorded trial and one row per period, over one common date range. Split the rows into S equal blocks. S defaults to 16, is declared before the lock, and must be even. Each block must hold at least a declared minimum number of periods. If the sample is too short for that, use the largest even S that meets it. The report shows S and the block length. S is never chosen after seeing results. For each way of choosing half the blocks as in-sample, take the trial with the best in-sample Sharpe and record its rank out of sample. PBO is the share of splits where that trial falls below the out-of-sample median. Pass if PBO is at most the threshold. *Reason:* DSR asks whether a result is large enough given N. PBO asks whether picking the best in-sample trial tends to pick a loser out of sample. It assumes nothing about the return distribution, but it depends on the S chosen and on the trial set. Trials without a return series (`prior_trials`) cannot enter the matrix, so the output states that PBO covers recorded trials only. The gate report then names DSR as the check that still covers those trials, since N includes all of them, so the overfitting penalty still applies to them, just not through PBO. Counted runs with no series, such as abandoned runs, are listed as "missing from PBO" and never dropped silently. Where the series files are not on the machine running the gate, PBO is reported as not checkable, and stage 2 cannot pass.
- **G7. Minimum track record.** Compute the minimum track record length for the observed Sharpe to beat the hurdle at the chosen confidence level. Pass if the design sample is at least that long. *Reason:* a high Sharpe over a short sample is weak evidence, even when DSR is high.

**Stage 3: Implementability**

- **G8. Survives costs.** The net-of-cost Sharpe, using the cost model in the open manifest, is at least the threshold. The cost model must be declared and cited. It may be a flat per-trade cost or spread-plus-commission. A spread comes from a cited estimator, such as Corwin and Schultz (2012) or Abdi and Ranaldo (2017). *Reason:* the gate can check only what is declared.
- **G9. Survives slippage.** The net Sharpe at 2x the declared costs is at least the threshold. The gate always reports this result, whatever cost model was declared. *Reason:* any cost model is a guess, and a result that holds only at the guessed cost is fragile.
- **G10. Capacity.** Using the declared capital and the average daily volume from declared inputs, the largest position stays within the declared participation limit, which must cite its source. *Reason:* a result that works at a small size may not work at the size the fund would trade.

**Stage 4: Incubation**

- **G11. Frozen and recorded.** Incubation starts with an `incubation` row, written before the first live period. It records the start date, the commit (which must equal the confirmatory runs' commit), and the pass rule with its numbers. Each live period's return is written as a `live_return` row. Any change to code during incubation ends it and starts a new one.
- **G12. Pass rule.** Incubation cannot close before its declared minimum calendar length. MinTRL is computed on live periods and reported with the result. If live length is below MinTRL, stage 4 can only return "consistent with backtest, underpowered", never a plain pass. *Reason:* for realistic Sharpe ratios MinTRL is often years, so a MinTRL-only rule would never close, and a calendar-only rule would claim power it does not have. At close, live returns are compared with the backtest distribution. A stationary (block) bootstrap of the backtest series gives a two-sided 95% band on the live Sharpe, which keeps autocorrelation. Outcomes: *pass (consistent with backtest)* inside the band; *fail (below band)*, meaning the backtest overstated the edge; *flagged (above band)*, a possible bug or leak, for review; or *consistent with backtest, underpowered* when live length is below MinTRL. Each outcome is recorded with its label.

**Output rules**

1. Each stage reports PASS or FAIL, the reason, and the numbers behind it. Nothing else.
2. No blended score. One number would hide which stage failed, and the failing stage is the most useful thing a reviewer can see.
3. Stage 3 says that the cost and capacity models are assumptions. The gate checks that they were applied, not that they are right. That judgement belongs to the reviewer.
4. The result is written as a `gate` row, with the report file hashed in it. As elsewhere in the ledger, numbers are stored as strings.
5. The original verdict always comes first. Any result under amended thresholds is labelled as amended and shown beside it, never in its place.
6. A stage that runs after an earlier stage has already failed is labelled "diagnostic, verdict already fixed". It is shown for information and cannot change the verdict.

**Thresholds.** Each study's thresholds sit in `bgl.toml`: the DSR threshold (0.95), the PBO threshold, S and the minimum periods per block, the minimum track record confidence (95%), the minimum live calendar length, the cost and slippage parameters, the participation limit, capital, and the incubation rule's numbers. C10 and C11 validate them. Every literature-based value carries its citation in config. The DSR and MinTRL values follow the literature conventions. No PBO bar is set until it is verified in Bailey et al. (2017). If that paper gives no explicit cut-off, the bar is a declared value, and its reasoning is written down beside it. Thresholds are recorded in a `gate_spec` row before the study's first counted open (G1). A study whose thresholds are locked after its first counted run must log a `deviation` row giving the reason; the gate report shows it. In-place changes after the lock are refused. The gate always evaluates the locked thresholds, and that verdict is the study's verdict. If a threshold is genuinely wrong (a typo, or a bug in the cost model), it can be amended only through a `deviation` row that gives the reason and names the fix commit. The gate report then shows a second result under the amended thresholds, clearly labelled, beside the original verdict and never replacing it. *Reason:* otherwise a threshold becomes a dial that can be turned after seeing the result.

**Data each stage reads**

| Stage | Reads | Status |
| --- | --- | --- |
| 1 | Ledger rows (open, close, void, refused), `gate_spec`, the holdout sample | Recorded in v0.1, except `gate_spec` (new in v0.2, written before first open) |
| 2 | Counted run IDs, the return series files (checked against `returns_sha256`), trial Sharpe ratios, `prior_trials`, S and thresholds | `returns_sha256` and the sample dates are v0.1 schema changes |
| 3 | `costs` in the open manifest; capital, sensitivity multiplier and participation limit in config; ADV as a declared, hashed input | `costs` is a v0.1 schema change; the rest is config and existing inputs |
| 4 | `incubation` and `live_return` rows; the backtest distribution from stage 2 | New in v0.2 |

**Build.** The gate is built only after v0.1 has been used on real equities runs, and those runs are its first test case. *Reason:* a gate designed before real use tends to test the rules we imagined, not the ones the work needs.

**Implementation note.** The statistics are written in the package using only the standard library. `statistics.NormalDist` provides the normal distribution function and its inverse. Independent packages are test-only cross-checks, never runtime dependencies.

## v0.3: external proof of when a spec was fixed

v0.3 lets a study prove, with a timestamp the researcher cannot edit, that its spec existed before its results. Git dates are set by the researcher's own machine and can be rewritten, so they show order but not time. v0.3 also automates the anchoring step that the v0.2 gate's stage 1 uses by hand (see G1).

1. `register STUDY --files ...` bundles the named spec files, their SHA-256 hashes, the commit ID and the ledger's head hash into one archive, and records a `registration` row in the ledger. *Reason for the head hash:* it pins the ledger's state at that moment, so trailing rows cannot be deleted afterwards without breaking the registration. This closes the truncation gap from Scope.
2. The researcher uploads the bundle to OSF (the Open Science Framework, osf.io), which freezes it with its own timestamp. Registrations can be embargoed (kept private) for a set period before becoming public. The OSF link and identifier are then recorded with a `note` row.
3. **Rule:** a confirmatory run is refused unless its study has a registration recorded after the study's last change to its spec files. *Reason:* this is the point at which proof of timing matters most.

**Decided 18/11/26:** OpenTimestamps is automated, and is the anchor method already used in stage 1. OSF is optional, for public registration only. OpenTimestamps proves a file existed at a time, but unlike OSF, does not publish its contents.

**Not in v0.3:** any judgement of whether a spec is good. The library proves when, not whether.

## v0.4: GUI

The GUI is a **read-only local web page** (`bgl ui` command, served on localhost), not a desktop app. *Reason:* a browser page needs no installer and works on every operating system, and the React experience already exists; a desktop wrapper (for example Tauri) can be added later if there is a need.

**Read-only by design:** runs are only created by scripts, so the GUI can never be used to make or edit a run.

**Views**

1. **Project overview:** studies, budget used and left, claims decided and open, ledger status (verified or broken), and the count of unclosed runs.
2. **Run list:** filterable by study, type and status. Refused, crashed, interrupted, abandoned and unclosed runs are shown, not hidden.
3. **Run detail:** prediction next to result, commit (linked to the repository), inputs and their hashes, environment, notes, and files opened that were not declared.
4. **Timeline:** every run of a study on one time axis, as a bar from its open row to its close row. Registrations and confirmatory runs are marked, so a reviewer sees at a glance whether rules came before results.
5. **Statistics (with v0.2):** each strategy's Sharpe next to its deflated Sharpe and the N it used.
6. **Society view:** several project folders side by side, for team leads reviewing many projects.

**Built on:** a small local API from the library (Python) and a static front end. The front end reads only what the command line can already show, so the GUI adds no new rules to test.

## Using it across QuantSoc

The library works for one researcher alone and for a team sharing a repository, with no server.

- **Several researchers, one repository:** each row records the git user. **Decided 18/11/26: one chain per researcher.** Each researcher appends only to their own chain file, named by their registered chain code. Chains never merge, so git cannot reorder or break rows. The gate counts trials across every registered chain and counts each run ID once. It fails if a registered chain is missing, so leaving a chain out cannot lower N. *Reason:* a single merged chain gives git a way to reorder rows silently.
- **Who the user is:** the `user` field is self-reported. For review, the stronger evidence is a verified commit signature (`signature` in the open manifest). Git supports SSH or GPG signing, and GitHub verifies signatures, so a good signature ties a run to a key that someone controls. An unsigned or unverified run is still recorded, but reviewers should treat its author as unknown. Confirmatory runs must be signed from v0.3. Both SSH and GPG signing are accepted, since SSH keys are easier for members to set up.
- **Head hash practice:** each researcher records the ledger head hash from `bgl status` in the commit message of the ledger commit, or in the team channel. This is the v0.1 defence against truncation (see Scope).
- **Review before money:** a reviewer runs `verify` and `status`, opens the GUI timeline, and checks that every confirmatory run came after its registration. A one-page review checklist ships with the library. Its items include: every void has a fix commit that plausibly fixes the stated bug.
- **Adoption cost:** `pip install` (or `uv add`), `init`, and five lines per script. Projects that already have a log can import it with `migrate`, honestly marked as unverified.
- **Licensed data:** only hashes and paths of data files are stored, never the files, so projects using CRSP, Compustat or IVolatility can be shared without breaching their terms.
- **Teaching use:** the run budget and prediction rules are a practical way to teach new members why backtests overfit.

## Testing, packaging and quality

A library that certifies other people's honesty has to be held to a higher standard than the projects it checks.

**Testing**

- **One test per rule (R1 to R16),** written first and seen to fail before the rule is implemented.
- **Real git in tests:** each test builds a temporary repository, so the clean-code check is tested against git itself, not a mock.
- **Tamper tests:** edit a byte of an old row, delete a row, swap two rows, delete a manifest, edit a close file; `verify` must catch each.
- **Truncation test:** delete trailing rows; `verify` passes, but the head hash recorded earlier no longer matches. The test documents the limit rather than hiding it.
- **Crash tests:** an exception, a Ctrl+C and a missing result each produce the right status and still use budget.
- **Hard-kill test:** start a run in a separate process, then kill it (`kill -9` on Linux and macOS, `TerminateProcess` on Windows) between open and close. `verify` must fail the run as unclosed, and `abandon` must clear it.
- **Live-run test:** a separate process still running between open and close holds its lock. `verify` must report it in progress, not fail it.
- **Lock test after death:** once the live process is killed, its lock must be free, so the same run is then reported as unclosed.
- **Synced-folder test:** `init` inside a path under a known synced folder must print the warning.
- **Other-machine test:** a run whose `host` differs from this machine must make `verify` say it cannot tell, and fail.
- **Abandon refusal test:** `abandon` on a run whose lock is held here must be refused.
- **Cross-machine abandon test:** `abandon` for a run whose `host` differs must be refused without `--force`. If the original run later closes, its close is written as `late_close` and `verify` flags it.
- **Ledger concurrency test (R18):** two processes append to one ledger at the same moment, many times over. No row may be lost, and `verify` must pass with every row chained onto a single head.
- **Refusal message test (R3b):** the refusal names each untracked file and both fixes, in the form given in R3b.
- **Scratch folder test:** `init` creates `scratch/`, adds it to `.gitignore` without duplicating or overwriting existing entries, and a scratch file that a run imports closes the run as `invalid`.
- **Untracked-code tests (R3b and R17):** an untracked, non-ignored `.py` file in the repository must refuse the run before any user code runs. A git-ignored `.py` file that is imported must close the run as `invalid`. An untracked file outside the repository must not trigger either check.
- **Clean-tree test (R3):** after one run, the ledger and `runs_dir` are dirty, and the next run must still start. A dirty tracked script must refuse.
- **Void tests (R6):** a `void` on a crashed confirmatory run frees one more run for its claim. A void without a fix commit must be refused, and so must a fix commit that predates the voided run or changes no tracked code. A second void on the same claim must be refused, and a rerun cannot be voided. A `void` on an `ok` run must be refused. A voided run still counts against the budget.
- **Legacy ID test:** an imported row with ID `R01` must pass the ID check and keep its `counts` value.
- **Canonical hash test:** a fixed row with a known hash, including a non-ASCII value, must produce that exact hash on Linux, macOS and Windows.
- **Crash at close:** kill between the close file and the close row. `verify` must report the orphan file.
- **Audit-hook test:** a script that opens an undeclared file through Python must appear in `files_opened`. A pyarrow read is tested separately and its result recorded, whichever way it goes.
- **Signature test:** a good, bad, unsigned and unknown-key commit each produce the right `signature` value.
- **Property tests** (the Hypothesis library) for the hash chain: any sequence of valid appends verifies; any single change to any row fails.
- **Cross-platform:** CI runs the hard-kill and live-run tests on Linux, macOS and Windows. The Windows lock (`msvcrt.locking`) behaves differently from `fcntl.flock`, so a Windows failure is a blocker, not a skip.

**Gate tests (v0.2)**

- **One test per gate rule (G1 to G12),** written first and seen to fail first, on fixed ledgers built in temporary repositories.
- **Stage order test:** a stage 1 failure computes no statistic. A stage 2 failure still runs stage 3, and the verdict stays at stage 2. Stage 4 does not start unless stages 1 to 3 all pass.
- **Cross-check tests:** DSR against quantstrat on known cases; PBO against an independent implementation on one fixed matrix. Results must agree within a stated tolerance, and a disagreement fails the build.
- **Threshold lock test:** an in-place change to a locked threshold is refused, and the verdict still uses the locked values. An amendment by `deviation` row without a fix commit is refused. With a fix commit, the report shows the amended result beside the original, and the original verdict is unchanged.
- **Units test:** a DSR input given annualised returns must be refused or converted, never mixed silently.
- **Holdout tests:** a second holdout run that exposed output fails G3 with "holdout opened twice". A crashed holdout run with no output after the load allows one rerun, and G3 passes with a note. A crashed run whose proof is missing counts.
- **Migration test:** every imported row keeps the study its source log gives it. A row must never be imported under a study other than its source's.
- **Late-lock test:** a study whose thresholds are locked after its first counted run needs a `deviation` row, and the gate report shows it.
- **Series determinism test:** the same series written twice gives the same bytes and the same hash, on Linux, macOS and Windows.
- **Prior-trial scope test:** `prior_trials` declared on one study adds to that study's N only. Another study's N does not change. The gate report names DSR as the check covering prior trials, and says PBO excludes them.
- **Missing-series test:** an abandoned counted run appears in the gate report as "missing from PBO". A machine without the series files reports PBO as not checkable, and stage 2 does not pass.
- **Block-length test:** S falls to the largest even value that meets the minimum block length, and the report shows S and the block length.
- **Band test:** an above-band live result is flagged, a below-band one fails, an in-band one passes, and live length below MinTRL returns only the underpowered label.
- **Chain-registry test:** a missing registered chain fails trial counting, and a run ID found in two chains is counted once.

**Packaging**

- Python 3.11 or later, built with uv, published to PyPI under the chosen name.
- Standard library only for v0.1 (json, hashlib, subprocess for git, tomllib, and `sys.addaudithook`). *Reason:* every dependency is something that can break or be compromised in a tool whose job is trust. The statistics stay in the standard library too (`statistics.NormalDist` supplies the normal distribution function and its inverse). Independent packages used to cross-check the numbers belong in the development group, never in the package. The GUI may add dependencies as an optional extra.
- Semantic versioning. The ledger and manifest formats carry a `format_version`, and any change to them ships with a migration.

**Quality**

- Type hints throughout, checked in CI; a linter in CI.
- Documentation: a README with the five-line quick start, a "Common refusals" section (starting with R3b: new `.py` files must be committed or put in `scratch/`), one page per command, the design rationale (this spec, kept up to date), and the head-hash practice from Scope.
- An open-source licence (MIT suggested), so other societies can adopt it.

## Roadmap, build order and open decisions

Each version ships only when its gate is met, and regime\_consensus is the first project to use each one.

| Version | Gate |
| --- | --- |
| v0.1 Ledger and runs | regime\_consensus migrated; `verify` passes in CI |
| v0.2 Statistics and gate | Deflated Sharpe matches quantstrat on known cases; PBO matches an independent implementation on one fixed matrix; `bgl gate equities` runs end to end on the real study |
| v0.3 Registration | One study registered end to end |
| v0.4 GUI | A reviewer uses it on a real project |

**Build order for v0.1** (each step with its tests, seen failing first):

1. **Package skeleton:** new repository, `uv init --lib`, a `.gitignore` covering `.bgl/` (locks are machine-local and never committed), CI on three operating systems, licence, README stub. Added to regime\_consensus as an editable local dependency.
2. **Config loader:** `bgl.toml` with validation and clear errors.
3. **Ledger:** canonical JSON (pinned as in the hash field), hash chain, head hash, the ledger lock (R18) with `fsync` and its two-process test, `verify` for the chain only. *First because everything writes to it.*
4. **Git checks:** clean tree with BGL's own files excluded (R3), tracked script, the untracked-Python check (R3b), commit ID, user, signature status.
5. **Run context manager:** checks R1 to R9, takes the lock, writes open row, close row, crash and interrupt handling, atomic writes, the import audit check (R17).
6. **Audit-hook spike:** test whether `sys.addaudithook` sees pyarrow reads, and record the result. The README wording for R8 depends on it, so it comes before the README claim is written.
7. **Manifests:** open manifest, close file, their hashes, `files_opened`.
8. **Lock checks and the command-line tool:** the lock test on all three operating systems first, then `init` (with the synced-folder warning and the `scratch/` folder), `status`, `list`, `show`, `verify` (with unclosed-run, orphan and late-close checks), `note`, `abandon`, `void`, `migrate`.
9. **Migrate regime\_consensus:** import `runs.csv` (rows marked `imported`), switch the scripts to `start_run`, delete the old `runlog.py`.
10. **Gate (v0.2):** statistics first (DSR, then PBO, then minimum track record), then stages 1 to 4 in order. Built only after v0.1 has been used on real equities runs, which are the gate's first test case.

**Open decisions** (decide and date before the step that needs them):

| Decision | Options | Needed by |
| --- | --- | --- |
| Package name | Decided 18/11/26: BGL (Backtest Governance Library); package and import name `bgl`, free on PyPI on that date | Step 1 |
| Run ID format | Decided 18/11/26: per-study prefix plus counter, with the researcher's chain code added for multi-chain use (see R16) | Decided |
| Unclosed runs in `verify` | Decided 18/11/26: failure by default; cleared only by `abandon` with a written reason; a run whose lock is held on this machine is reported in progress | Decided |
| Live runs | Decided 18/11/26: a lock file held by an OS lock, not a PID. Lock taken without waiting by `verify` | Decided |
| Locks on synced or network folders | Decided 18/11/26: unsupported and documented; `init` warns when the project is inside a known synced folder | Decided |
| Dry runs | Decided 18/11/26: enforced with the audit hook. The C-level reader gap is a known limitation | Decided |
| Audit-hook scope | Decided 18/11/26: hold the R8 claim until the spike (step 6), then word it to match exactly what the hook detects | Decided |
| Concurrent writers | Decided 18/11/26: same machine uses the ledger lock (R18). Across machines, one chain per researcher, registered in config (C12). Trial counting fails on a missing chain and counts each run ID once | Decided |
| Signed commits | Decided 18/11/26: required for confirmatory runs from v0.3. SSH and GPG both accepted | Decided |
| Effective number of trials | Decided 18/11/26: raw N in v0.2. Effective N later, as a labelled sensitivity check only | Decided |
| Registration route | Decided 18/11/26: OpenTimestamps automated. OSF optional, for public registration | Decided |
| Gate stage 1 pre-registration | Decided 18/11/26: `gate_spec` row now, plus a hand-made OpenTimestamps anchor of the head hash. Stage 1 has two pass levels (see G1). v0.3 automates the anchoring step | Decided |
| Minimum level for deployment | Decided 18/11/26: a deployment verdict requires externally anchored. Ledger-ordered passes but is labelled not deployment-grade | Decided |
| Late threshold lock | Decided 18/11/26: a late lock is recorded by a `deviation` row giving the reason and the runs made before the lock, shown in the gate report | Decided |
| Changing thresholds after first open | Decided 18/11/26: in-place changes refused. The verdict always uses the locked thresholds. Errors (typo, cost-model bug) can be amended only by a `deviation` row with a fix commit, and the gate shows the amended result beside the original | Decided |
| Stopping at a failed stage | Decided 18/11/26: the verdict is the first failure. Stage 1 failure halts all. Stages 2 and 3 both run and both are reported. Stage 4 runs only if stages 1 to 3 pass | Decided |
| Voided holdout runs | Decided 18/11/26: a voided holdout run counts unless its manifest proves it exposed no holdout-derived output. Doubt counts (see G3) | Decided |
| Holdout enforcement | Decided 18/11/26: start_run refuses a second holdout run. One bug rerun is allowed after a crash that exposed nothing. When in doubt, refuse | Decided |
| Return series storage | Decided 18/11/26: one portfolio-level daily series per counted run, never per-asset, kept in a git-ignored folder with only its hash in the ledger. Owner backs it up privately. Open detail: the series may still be restricted by data licences, so check before any sharing | Decided |
| Prior trials in PBO | Decided 18/11/26: PBO covers recorded trials only, and the gate report states the limit. DSR still counts all prior trials in N, and the report names DSR as the check covering them. The 38 V1 and V2 trials belong to the crypto study only | Decided |
| CSCV block count S | Decided 18/11/26: S = 16 by default, declared before the lock. Each block holds a declared minimum number of periods; if the sample is too short, use the largest even S that meets it. S and block length are shown in the report | Decided |
| Incubation pass rule | Decided 18/11/26: stationary (block) bootstrap with a two-sided 95% band. Results above the band are flagged as a possible bug or leak; results below it fail | Decided |
| Minimum incubation length | Decided 18/11/26: a declared minimum calendar length, with MinTRL reported on live periods. Below MinTRL, stage 4 returns only "consistent with backtest, underpowered" | Decided |
| Cost and capacity models | Decided 18/11/26: cost model declared and cited (flat, or spread-plus-commission with the spread from a cited estimator). Results at 2x costs always reported. Participation limit cited | Decided |
| Gate threshold values | Decided 18/11/26: literature conventions, each cited in config. DSR at least 0.95; MinTRL at 95%. No PBO bar until verified in Bailey et al. (2017); if that paper gives no cut-off, a declared value with written reasoning | Decided |
| Return series licences | Decided 18/11/26: confirm the licence before the first counted run. If a licence forbids local derived storage, the study is excluded from PBO and the report says why | Decided |

## References

- Arnott, R., Harvey, C. R., and Markowitz, H. (2019). A Backtesting Protocol in the Era of Machine Learning. *Journal of Financial Data Science*, 1(1), 64 to 74.
- Abdi, F., and Ranaldo, A. (2017). A Simple Estimation of Bid-Ask Spreads from Daily Close, High, and Low Prices. *Review of Financial Studies*, 30(12). Spread estimator for G8. *Check page numbers before citing.*
- Corwin, S. A., and Schultz, P. (2012). A Simple Way to Estimate Bid-Ask Spreads from Daily High and Low Prices. *Journal of Finance*, 67(2). Spread estimator for G8. *Check page numbers before citing.*
- Politis, D. N., and Romano, J. P. (1994). The Stationary Bootstrap. *Journal of the American Statistical Association*, 89(428). Resampling method for G12. *Check page numbers before citing.*
- Bailey, D. H., and López de Prado, M. (2012). The Sharpe Ratio Efficient Frontier. *Journal of Risk*, 15(2). Source of PSR and minimum track record length (G5, G7). *Check page numbers before citing.*
- Bailey, D. H., Borwein, J., López de Prado, M., and Zhu, Q. J. (2017). The Probability of Backtest Overfitting. *Journal of Computational Finance*, 20(4), 39 to 69. Source of CSCV and PBO (G6).
- Bailey, D. H., and López de Prado, M. (2014). The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality. *Journal of Portfolio Management*, 40(5), 94 to 107. [SSRN](https://papers.ssrn.com/abstract=2460551)
- Harvey, C. R., Liu, Y., and Zhu, H. (2016). ...and the Cross-Section of Expected Returns. *Review of Financial Studies*, 29(1). Source of the multiple-testing hurdle, an optional extra check beside DSR. *Check page numbers before citing.*
- Harvey, C. R., and Liu, Y. (2015). Backtesting. *Journal of Portfolio Management*, 42(1). *From memory; check before citing.*
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley, chapter 14.
- Python PEP 578, Python Runtime Audit Hooks, for `sys.addaudithook` in R8. *Confirm against the current Python documentation before relying on it.*
- R quantstrat's `deflatedSharpe`, an existing implementation to test against: [documentation](https://www.rdocumentation.org/packages/quantstrat/versions/0.16.7/topics/deflatedSharpe)
- Open Science Framework: osf.io