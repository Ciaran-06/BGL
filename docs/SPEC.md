# Backtest governance library: design spec (v0.1, full library)

Nov 18, 2026 · @ciaran gaffney

## Revision log

### Independent review and decision batch (18 Nov 2026)

An independent review found 8 critical and 16 major problems. Most had one root: the spec called rules "enforced" when BGL is only told the facts behind them. Every rule now carries a class (see Rule classes), and the decisions below close the findings.

| # | Decision | Where it lands |
| --- | --- | --- |
| 1 | The first counted open of a study locks the whole study table and the writer registry automatically, values included. Changes after that are typed amendments, each of which can only weaken the evidence on paper | Study lock and amendments; R22; C19 |
| 2 | One chain per writer (one person on one machine), with a local writer file and a signing key | Writers, chains and keys; R19; C12 |
| 3 | Holdout data lives in declared files. A holdout custodian seals them and runs the one holdout test. Outputs derived from holdout data are tracked by hash and refused as design inputs | Holdout, custodian and sealing; R20; C11, C15 |
| 4 | Each study declares its PBO bar with a basis, capped at 0.5 | G8; C10 |
| 5 | BGL applies the locked cost model itself, from each variant's gross returns and turnover | Statistics; G10; `run.returns` |
| 6 | Stage 4 is a replay check. The bootstrap band is reported but does not decide | G12, G13 |
| 7 | `periods_per_year` is required and locked per study. Return series may carry timestamps. Each study declares its return basis. Studies without a gate table are allowed | C16; Return series file |
| 8 | Every gate value carries a basis (a citation or written reasoning). BGL's own placeholder text is rejected | C10, C20 |
| 9 | Each run declares how many variants it evaluated. N is the sum of those counts plus `prior_trials`. Counted runs store every variant's series | Trials and N; R21 |
| 10 | Open rows record the other chains' heads. The gate recomputes every per-study rule over all chains | R24; G5 |
| 11 | Correction rows go in the writer's own chain. A late close is derived, never written | R10, R14 |
| 12 | Witnessing (a protected remote branch plus OpenTimestamps) is automated from v0.1 | Witness; R23; G2 |
| 13 | One holdout look per study, and one confirmatory run may decide several claims. A void depends on what the run exposed, not on its status | R6, R20, G4 |
| 14 | Imported rows are display-only for N and budget, but binding for claims and the holdout | `migrate` |
| 15 | A gate table added after the lock is never deployment-grade | G1 |
| 16 | Dry runs run the script on fixtures, block every other read by default, and leave a `dry` row | R25 |
| 17 | The gate deflates the selected design variant and tests the holdout run against zero. V is floored at the null variance (BGL's choice). N = 1 gives a hurdle of zero. Missing series are reported, and N keeps the penalty | Statistics |
| 18 | MinTRL report-only; R17 by module scan; torn-tail repair; orphan IDs never reused; duplicates judged on recorded facts; `counts` derived from `type`; declared claim outcomes; rule classes | R4, R14, R16, R17; Statistics |
| A | A small set of pinned dependencies (`cryptography`, `opentimestamps`, `numpy`), and Ed25519 signatures on ledger rows | Packaging; R15 |
| B | Solo projects may run without a custodian. Team projects must declare one. Deployment-grade requires one | C14, C15; G2 |
| C | The whole library is v0.1; the GUI is v0.2 | Roadmap |
| D | Estimators: BGL subtracts a locked risk-free series; standard deviation over $T-1$; population moments with kurtosis not excess; Lo (2002) adjustment reported only | Statistics |
| E | DSR and PBO are cross-checked against fixed test vectors, not an outside package | Testing |

**Added by me to make the decisions fit together. Check these before building:**

- (a) Deployment-grade also requires a holdout. A study with `holdout = false` can run the gate, but is labelled not deployment-grade. *Reason:* the custodian exists to guard the holdout.
- (b) A `replay` run type and a `live` sample, for stage 4.
- (c) If PBO cannot be computed (too few trials, or too few periods for 4 blocks), stage 2 fails. *Reason:* a check that only fails cannot show the absence of overfitting if it never runs.
- (d) The holdout run's PSR against zero gets its own threshold key, `psr_threshold`.
- (e) `spec_files`: research design documents are hashed into the study lock. This replaces the old v0.3 rule that a confirmatory run needs a registration. OSF publication stays optional (`register`).
- (f) Witness status is worked out from the remote branch and the stamps. The writer never records it.
- (g) Each `live_return` row must be witnessed within a declared lag of its period (`live_witness_lag_days`).
- (h) Witness branches are named `bgl-witness/<CODE>` and pushed with git plumbing commands, so the working branch is never touched.
- (i) Private keys and the machine ID live in the user's home config folder, so `git clean -x` cannot delete them.
- (j) Refused rows, dry rows and imported rows get IDs with the letters F, T and I. An imported row keeps its original ID in `legacy_id`.
- (k) The bootstrap band uses a fixed number of replications and a seed taken from the lock hash, so it needs no config keys.
- (l) `capital`, `participation_limit` and `mintrl_confidence` leave the gate table. Capacity moves to the reviewer checklist.
- (m) Output is captured for every counted run, not just holdout runs.
- (n) Confirmatory holdout runs and replay runs are not trials.
- (o) R4 applies to counted runs only, so an exact rerun is possible as an exploratory run.
- (p) Holdout keys, frequency, return basis, risk-free series, mode and witness settings can never be amended. The custodian can be handed over (see Study lock and amendments). *Reason:* a custodian is often a role that changes hands every year, and under "never" a study whose custodian leaves would be dead.

**Signed off 18 Nov 2026:** items (a) to (p), with (p) as written above.

**Numbering.** R1 to R18 and R3b keep their numbers. New rules are R19 to R25. Gate rules are renumbered G1 to G14; nothing depended on the old numbers.

### Stage 4 and witness redesign (18 Nov 2026, evening)

| # | Decision | Where it lands |
| --- | --- | --- |
| W1 | Stage 4's verdict is a position-hash replay (exact match, no tolerance) plus an implementation-shortfall cost check. The bootstrap band stays, reported only. `replay_tolerance` is removed | G11 to G14; C10; `live_return` rows; Python API |
| W2 | Witnessing uses a public transparency log (Sigstore Rekor) to detect discarded runs, and OpenTimestamps for timing. The `bgl-witness/<CODE>` branches and the branch-protection checklist item are removed | Witness; R23; G2; C17 |

*Reason for W1:* a return replay needs a tolerance wide enough to absorb fill noise, and a small bug can hide inside it. Positions computed from the same recorded inputs must match exactly, so any difference between the live system and the backtested code fails. The cost check tests the one assumption stage 3 cannot: whether the cost model holds in live trading.

*Reason for W2:* BGL cannot read a git host's branch-protection settings, so a protected branch was only ever a checklist item. Rekor entries can be listed by the writer's public key, so a reviewer can check that every head a writer ever submitted is still in the chain. A discarded run then shows up as a missing head, and BGL checks this itself.

**Added by me; check these:**

- (q) The position-hash rounding (8 decimal places) and the canonical position text are fixed by `format_version`, not by a config key.
- (r) The live cost check passes when realised costs over the incubation are at most twice the modelled costs: the same factor G10 already showed the strategy survives.
- (s) No new dependency for Rekor: BGL calls its HTTP API with the standard library, and verifies proofs with `hashlib` and `cryptography`.
- Items (f) and (h) in the batch above are superseded by W2 wherever they mention witness branches.

### Config numbers and the empty file (18 Nov 2026, late)

| # | Decision | Where it lands |
| --- | --- | --- |
| N1 | Config decimals are floats, not `Decimal`. Integers are accepted for decimal keys and converted. The lock stores each float's shortest round-trip text as a string | Shared checks; C10; C19; Study lock |
| N2 | C1 also requires a `[project]` table and a `[studies]` table, which may be empty. An empty file gives two C1 errors | C1 |
| N3 | Every placeholder in BGL's templates and documentation contains the marker `TODO(bgl)`. C20 rejects any checked value containing it. Angle brackets are allowed, so basis text may contain inequalities such as `PBO < 0.5` | C20; File formats |
| N4 | No public key appears twice in the registry. Study names are snake_case, 1 to 32 characters. `risk_free_reasoning` is an error when `risk_free` is a path. C20 covers every free-text value: gate basis texts, the returns `basis` and `risk_free_reasoning` | C1; C12; C16; C20 |

*Reason for N1:* the statistics are floats, so keeping config numbers as floats avoids converting between types, and a harmless reformat (`0.95` to `0.950`) no longer looks like a change to a locked value.

*Reason for N3:* the rule exists so that an unedited template does not load. BGL writes the templates, so it can mark them in a way that is easy to detect: a half-edited template is still caught, and ordinary reasoning text never is. The earlier wording also referred to a list of placeholder strings that the spec never gave.

*Reason for N4:* discard detection lists Rekor entries by public key, so two chains sharing a key would each look as if heads were missing from the other, and a signature would no longer say which writer wrote a row. Study names appear in commands, rows and key paths, so they must need no shell quoting and contain no dots. A reasoning left behind after switching to a file would describe a choice the study no longer makes.

### Earlier revisions (18 Nov 2026; superseded where they conflict with the batch above)

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

**Step 2 (18 Nov 2026):** config rules C1 to C9 added. C6 and C7 were added by me, not from review.

**Config decisions (18 Nov 2026):** no config key has a default. Every study declares `holdout_start` or `holdout = false`, and `prior_trials` (new C13). The cost model moves from each run to the study's `[gate.costs]` table and is locked with the thresholds. `cscv_blocks` is at least 4 and even. Errors carry a rule ID, which tests match on. One shared whole-number check covers every count field. The run-ID chain code is confirmed. Timestamp verification may use any independent source of Bitcoin block data.

**Generalised (18 Nov 2026):** BGL no longer names or depends on any user project. Examples use a made-up project (`example_project`, studies `alpha` and `beta`). Release gates run against a new example project in `examples/`, which uses only synthetic data from a fixed seed. `migrate` reads a documented BGL import format, keeps each row's declared study, and refuses any import that would change one. Dogfooding feeds back one way only, as general issues with example-project tests. Adopting BGL is each project's own task, not a BGL build step.

**Batch decisions (18 Nov 2026):** every open decision in the table is now decided. Rules are stated in general terms, and study names appear only in examples.

**Gate (18 Nov 2026):** a deployment gate added as `bgl gate STUDY`, with four stages. Stage 1 has two pass levels. The threshold lock stays at the first counted run. Changes after the lock are refused in place; errors can be amended by deviation, and the original verdict always stands beside any amended result. The verdict is the first failing stage. Return series are stored as one series per counted run, in a git-ignored folder, with only the hash in the ledger. Prior trials are excluded from PBO, with DSR covering them.

## Purpose and users

BGL (Backtest Governance Library) is a Python library that applies the research discipline a fund imposes on an alpha researcher to every backtest run in a project. Each run is:

- logged with its prediction before it starts and its result after it ends;
- counted against a budget;
- signed by the person and machine that ran it;
- witnessed outside the researcher's control.

A deployment gate then corrects the reported result for the number of variants tried, and tests it on a holdout the researcher cannot open alone.

- **Researchers:** individuals or teams running research in Python.
- **Reviewers:** people who check a study's claims before money is committed.
- **Holdout custodian:** a person who is not one of the study's researchers. The custodian holds the holdout data and runs the one holdout test. Team projects must have one; solo projects may.
- **First adopters:** real research projects use BGL before each release. They test it; they do not shape it. Problems they find come back as general issues (see Example project and dogfooding).
- **What a user gets:** a record of a strategy's research that someone who did not watch it being built can check.

## The problem

Existing experiment trackers record what happened. None that we know of enforces the discipline that makes a backtest believable. The main failure in backtest research is not bad code but **unrecorded searching**: trying many variants and reporting the best (Arnott, Harvey and Markowitz, 2019).

| Need | MLflow, Weights & Biases | DVC | This library |
| --- | --- | --- | --- |
| Logs parameters and results | Yes | Partly | Yes |
| Ties each run to committed code | Optional | Yes | Required: refuses uncommitted changes to tracked code |
| Prediction written before the run | No | No | Required, and witnessed |
| Run budget per study | No | No | Enforced per writer; recounted across writers at the gate |
| Refuses accidental duplicate runs | No | No | Yes |
| Logs crashed runs | Partly | No | Yes; a hard-killed run is reported as unclosed |
| Detects edited or deleted log rows | No | No | Hash chain and signatures. A deleted, discarded or rebuilt chain is detected through the public transparency log |
| Corrects results for the number of variants tried | No | No | Yes. N is a lower bound, because work outside runs is invisible |
| Holdout held by someone else | No | No | Holdout custodian |
| External proof of timing | No | No | OpenTimestamps |

The comparison is from general knowledge of these tools, not a fresh review. Check current features before the README makes this claim.

**Typical failures:** a script that crashes after printing its results leaves no log row, so the look at the results goes unrecorded. A renamed config setting causes a bug that several runs repeat before anyone notices. Both are the kind of problem this library is built to catch.

## Scope and non-goals

v0.1 is the whole library: runs, ledger, writers, witness, custodian sealing, statistics, the deployment gate, import, and optional public registration. v0.2 adds the GUI. v0.1 must be adoptable by any Python research project whose analyses are scripts that read files.

**Not in v0.1**

- The GUI (v0.2).
- Capacity analysis. It needs per-asset volume, which BGL never stores, so it is on the reviewer checklist.
- Cost models other than flat. A spread estimator needs per-asset prices.
- Array jobs spread across cluster nodes. Each node would be a separate writer, and locks are unreliable on shared network storage.
- Notebooks and database inputs. A run is a committed script, and only file inputs are hashed.

**Never in scope**

- **Running backtests or computing strategy returns.** The library governs runs; it does not do the research.
- **Storing data.** BGL records hashes of input files, never the files, so licensed data never leaves the user's machine. There are three exceptions, all git-ignored and never committed:
  - each counted run's portfolio-level return and turnover series (see Return series file);
  - each counted run's captured output;
  - the custodian's sealed holdout files.
  
  Per-asset returns and raw data are never stored.
- **Preventing a determined cheat.** A user can always delete the folder, or modify BGL itself. The aim is that honest work is easy to prove and dishonest editing is detectable, not that it is impossible.
- **Hosting or accounts.** Witnessing uses two public services that need no account: the Sigstore Rekor transparency log and OpenTimestamps calendars.

**What BGL does not prove**

- **Work outside runs.** N counts the variants that runs declare. A loop inside a run that declares fewer variants, analysis above `start_run`, or code run without BGL is all invisible, so N is a lower bound. The reviewer checklist compares each declared variant count with the recorded code.
- **Declared values.** Variant counts, sample dates, the return basis, turnover, the content of the risk-free file, claim outcomes and prediction content are what the researcher says. Every report labels them as declared.
- **When, without a witness.** Times in rows are self-reported. A stamped head proves the rows existed by a Bitcoin block's time.
- **Discards before witnessing.** A run whose open was never witnessed (because it ran offline) could have been thrown away without trace. Such runs are marked unwitnessed, and a study containing one is not deployment-grade.
- **Anything, if the transparency log is unavailable or dishonest.** BGL relies on the public Rekor instance being available. Its entries carry inclusion proofs, and its history carries consistency proofs, so a log that rewrote its history would be detectable, but a log that refused submissions would leave runs unwitnessed.
- **Reads outside Python.** Files opened by C extensions such as pyarrow may not be seen (see the audit-hook spike in the build order).
- **Public data.** Sealing a holdout file does not stop a researcher downloading public data for the holdout period again.
- **Who has held the raw data.** If the researcher held the full history before the split, sealing proves only that BGL-governed runs did not read the holdout. The gate report says who did the split.
- **Locks on synced or network folders.** Locks are unreliable there, so projects should not sit in them. `init` warns.
- **Live runs on other machines.** A live run can only be seen from the machine it runs on. Another machine gets a "cannot tell" from `verify`, never a guess.

*Reason:* a library that certifies other people's honesty has to be exact about what it can and cannot show.

## Rule classes

| Class | Meaning | How it shows |
| --- | --- | --- |
| Enforced | BGL refuses or records it itself. Skipping it means changing BGL | A refusal or a recorded status |
| Detectable | BGL cannot stop it, but `verify` or the gate finds it afterwards | A `verify` failure or a gate finding |
| Declared | BGL records what it is told and cannot check it | Labelled "declared" in every report |

Every rule below states its class. A rule with parts in different classes says which part is which. *Reason:* the earlier spec called rules "enforced" when BGL was only told the facts behind them.

## Core concepts

| Term | Meaning |
| --- | --- |
| Project | One git repository with `bgl.toml` at its root. Its mode is `solo` or `team` (C14). |
| Study | One test design: a design period, a holdout, a budget, claims and thresholds. Locked at its first counted open. |
| Writer | One person on one machine. Each writer has a chain code, a signing key and its own ledger chain. |
| Person | The label that groups one person's writers in the registry. Used to tell researchers apart from the custodian. |
| Chain | The append-only file of one writer's rows, hash-chained and signed. |
| Run | One execution of an analysis script through `start_run`. Every run is recorded, whether it succeeds, fails or crashes. |
| Run type | `data` prepares or checks data and tests no strategy. `exploratory` looks informally at data or strategies, and may test variants. `diagnostic` is a planned test of one or more strategy variants. `confirmatory` decides one or more claims. `replay` reruns the incubation commit on the recorded live input files during incubation, to recompute each period's positions. |
| Counted run | A diagnostic or confirmatory run. It uses one unit of budget at open. |
| Variant | One strategy configuration whose performance a run computed. Each run declares how many it evaluated. |
| Trial | One variant evaluated on the design sample, in a non-data run of any status (voided runs included), plus each declared prior trial. Confirmatory holdout runs and replay runs are not trials. |
| N | The number of trials in a study (see Trials and N). |
| Budget | The maximum number of counted runs a study may open. Budget limits looks; N measures search. |
| Sample | `design`, `holdout` or `live`. |
| Holdout | The study's data from `holdout_start` on, kept in declared holdout files. Looked at once, by the confirmatory holdout run. |
| Restricted file | A raw file holding both design and holdout data. Only data runs before the study lock may read it, to do the split. |
| Holdout custodian | The person who holds and seals the holdout files, and runs the confirmatory holdout run and the replay runs. Never a researcher on the study. |
| Study lock | The `lock` row written by a study's first counted open. It holds the canonical study table, the chain registry and the spec-file hashes. |
| Amendment | An `amend` row changing a locked value within the typed rules. |
| Open row | The first ledger row of a run, written before the run's code starts. It holds the prediction, inputs, commit and variant count. |
| Close row | The second ledger row of a run, written when it ends. It holds the status, result and file hashes. |
| Status | `ok`, `no_result`, `crashed`, `interrupted`, `abandoned`, `invalid` or `refused`. |
| Unclosed run | A run with an open row and no close row, for example after a power cut. |
| Abandon | Closes an unclosed run with a written reason. |
| Late close | Worked out by `verify`, never written: a normal close row for a run that already has an `abandoned` close. |
| Void | Marks a confirmatory run as a bug, which frees one rerun (R6). |
| Prediction | What the researcher expects, written into the open row before the run starts. Required for diagnostic and confirmatory runs. |
| Claim | A named hypothesis within a study. One confirmatory run decides it, and that run declares the outcome at close. |
| Head hash | The hash of a chain's newest row. |
| Witness | Evidence held outside the researcher's control that a chain had a given head: a signed entry in the public Rekor transparency log, and an OpenTimestamps stamp of the head. |
| Position hash | The SHA-256 of one period's target positions in canonical form, computed by `bgl.position_hash`. Only the hash is stored, never the positions. |
| Deployment gate | `bgl gate STUDY`: four stages that decide whether a study's strategy may be deployed. |
| Deployment-grade | The gate's top level. It requires a custodian, a holdout, a witnessed record and a gate table written before the lock (G1, G2). |
| Manifest | The files holding everything needed to audit one run: the open manifest and the close file. |

## Writers, chains and keys

- **Setting up a writer.** `bgl init` sets up the first writer, and `bgl join CODE PERSON` each later one. Either command:
  - creates an Ed25519 key pair and a machine ID in the user's home config folder;
  - writes `.bgl/writer` (git-ignored), naming the chain code;
  - prints the registry entry to add to `bgl.toml`.
  
  The registry change must be committed before the writer's first row.
- **One writer per chain.** Each chain is `<ledger>/<CODE>.jsonl`. Only its writer appends to it, and every other chain on a machine is read-only there.
- **Signatures.** Every row is signed with its writer's key (R15). The `user` field from git is self-reported; the signature says which registered writer wrote the row.
- **The registry only grows.** After a study's first lock, writers can be added to the registry, but existing entries never change or disappear. A lost or replaced key means a new chain code. The documentation tells every writer to back up their key, and a custodian especially, because a lost custodian key blocks a handover after sealing.
- **Machine identity.** Lock checks and the "another machine" test use the machine ID, never the hostname. `verify` fails a chain that has rows from two machine IDs.

*Reason:* a laptop and a server appending to one chain would need a hand merge, which is the failure separate chains exist to remove. Signing the rows ties each row to a writer, which a signature on the code commit does not do.

## Study lock and amendments

**The lock.** A study's first counted open writes a `lock` row immediately before its open row (R22). The lock row holds a canonical copy of:

- the study table, including the gate table if one exists;
- the chain registry at that moment;
- the SHA-256 of each file in `spec_files`.

Numbers are kept in their canonical text (C19). After the lock, every open compares the current config with the locked values plus any accepted amendments. Any other difference refuses the run, and `verify` reports it.

**Amendments.** `bgl amend STUDY KEY VALUE "reason"` appends an `amend` row. Only these changes are allowed:

| Key | Allowed change | Effect |
| --- | --- | --- |
| `budget` | Increase only | Reported beside the original |
| `prior_trials` | Increase only | N rises |
| `claims` | Add a claim | A claim added after the lock cannot reach a deployment-grade verdict |
| `[gate]` table | Add, if it was absent at the lock | A late gate table is never deployment-grade (G1) |
| Any gate value or the cost model | Correction only, with `--fix COMMIT` naming the bug fix | The gate shows the original verdict first, and the amended result beside it |
| `spec_files` content | Any change, with a reason | Reported beside the verdict |
| Chain registry | Add writers | None |
| `custodian` | Handover (see below) | Reported. The new custodian must also meet G2 |
| Holdout keys, `[returns]`, `prefix`, `mode`, witness settings | Never | None |

*Reason:* every allowed change makes the evidence weaker on paper, never stronger, so a researcher cannot amend their way to a pass.

**Custodian handover.** `bgl amend STUDY custodian PERSON "reason"` names a person in the registry who is not a researcher on the study.

- **Before any `seal` row:** any writer may write the amendment. No key exists yet, so nothing is lost.
- **After a `seal` row, before the holdout run:** only a writer of the outgoing custodian may write the amendment, so it carries their signature. The new custodian then runs `bgl seal STUDY --reseal` with their own key. The new `seal` row must record the same plaintext hashes as the original, and `verify` checks this. Until the reseal, holdout runs are refused.
- **After the holdout run:** the holdout has been looked at, so only the custodian's remaining job (incubation and replay runs) moves. The amendment must still be signed by the outgoing custodian.

If the outgoing custodian cannot sign (for example, they have left and their key is lost), no handover is possible after sealing, and the study cannot reach deployment-grade. *Reason:* an unsigned handover would let a researcher name a friendly custodian after the fact.

**New studies on the same data** cannot be prevented. The gate report lists every other study whose declared inputs share a hash with this one.

## Holdout, custodian and sealing

**Files.** A study with a holdout declares `holdout_files`: the files containing holdout-period data. It may also declare `restricted_files`: raw files containing both periods.

**The split.** Before the study lock, a data run reads the restricted files and writes the design and holdout files as declared outputs. Whoever ran it is recorded as the split's provenance:

- *custodian split:* the split run is in the custodian's chain;
- *researcher split:* the split run is in a researcher's chain;
- *external:* the files arrived already split. This is declared.

**Sealing.** `bgl seal STUDY` (custodian only) encrypts each holdout file with authenticated encryption from `cryptography` into `<path>.sealed`, which is git-ignored. It records a `seal` row holding the sealed and plaintext hashes. The key stays in the custodian's home config folder. BGL never deletes the plaintext. The custodian deletes it, and `status` and `verify` warn on any machine where a plaintext holdout file still exists.

**Taint.** Any declared output of a run that read a holdout file, or a tainted file, is holdout-derived. Its hash is recorded as tainted. A design run that declares a tainted input is refused (R20). The exception is outputs of split runs before the lock, other than the files listed in `holdout_files`.

**Loading the holdout.** `run.holdout(path)` is the only way a holdout run loads a holdout file. With a seal, it decrypts the file to `.bgl/tmp/`, which is removed at close. It records the position in each captured output stream where the load happened.

**Who may look.** The holdout is looked at once, by the study's confirmatory holdout run. When a custodian is declared, only a custodian writer may open holdout runs and replay runs.

**Solo projects without a custodian** follow the same rules, without sealing or the custodian check. Their studies are not deployment-grade.

*Classes:* checks on declared files and samples are enforced. Reads through Python are detectable. The split script's behaviour is declared; the reviewer checks that it uses no holdout-period information to build the design outputs.

## Witness

Witnessing answers two questions with two services. Rekor, a public transparency log run by Sigstore, shows that no head was thrown away. OpenTimestamps shows when each head existed.

**Submitting.** After every append, BGL submits the writer's new head hash to two places:

- **Rekor:** an entry holding the head hash, signed with the writer's Ed25519 key and carrying the public key. BGL saves the entry's ID and inclusion proof in `<ledger>/witness/<CODE>/`.
- **OpenTimestamps:** the head hash goes to the declared calendars, and the pending proof is saved in `<ledger>/proofs/<CODE>/`.

Both folders are committed with the ledger.

**At open.** The open row is written, then both submissions are made, and only then does the run's code start (R23). If either fails, for example offline, the run continues and its open is unwitnessed. `bgl witness` retries pending submissions and upgrades pending stamps once Bitcoin confirms them, which takes hours.

**Witness status is worked out, never recorded.** An open is witnessed if:

- a Rekor entry signed by the writer's key covers a head that contains the open row but not its close row, and its inclusion proof verifies against the log's signed checkpoint; and
- an OpenTimestamps stamp covers that head.

A modified client could delay its submissions, but that is a deliberate cheat and out of scope.

**Detecting discards.** At gate time, BGL lists every Rekor entry made with each registered writer's public key. Every head hash in those entries must appear in that writer's current chain. A head that is missing means the chain was rewritten or a run was thrown away after it was submitted, and G2 fails. `verify --contains HASH` reports whether one recorded head appears in the current chain.

**Checking stamps.** At gate time, each OpenTimestamps proof is checked against Bitcoin block headers from a local node or from at least two independent block explorers that agree. The report records which source was used. The gate needs network access for both checks.

**Public activity.** Rekor entries are public. They contain only hashes and public keys, but anyone can see how often and when each writer opens runs. The documentation says so before a project turns witnessing on.

**Modes.** Team projects must witness (C17). A solo project may set `witness = false`, and its studies are then not deployment-grade.

*Classes:* the submission attempt is enforced. Witness status and discards are detectable.

*Check before building:* whether Rekor's newer API (v2) still supports listing entries by public key, which this design depends on; whether Rekor accepts plain Ed25519-signed hash entries; Rekor's rate limits; the OpenTimestamps calendar submission API; and the verification interface of `opentimestamps`. If listing by public key is unavailable, the fallback is a mirror controlled by the custodian plus a second remote with a different owner. Decide at step 8.

## Functional requirements

Every rule has a test that fails if the rule is removed. Checks before a run opens happen before any code in the `with` block. Code above `start_run` is outside BGL's view (see Scope).

**Rule index** (the canonical list)

| Rule | Name | When | Class |
| --- | --- | --- | --- |
| R19 | Writer identity | Before open | Enforced |
| R7 | Chain intact | Before every append | Enforced |
| R1 | Known study | Before open | Enforced |
| R22 | Study lock | Before open | Enforced |
| R3 | Clean code | Before open | Enforced |
| R3b | No untracked Python files | Before open | Enforced |
| R17 | No untracked code loaded | At open and close | Detectable |
| R8 | Inputs declared | Before open; during the run | Enforced for missing inputs; detectable for undeclared reads |
| R4 | No duplicate | Before open | Enforced per chain; detectable across chains |
| R5 | Prediction present | Before open | Enforced presence; declared content |
| R21 | Variants declared | Before open | Enforced presence; declared value |
| R6 | Confirmatory runs and void | Before open; `void` | Enforced per chain; detectable across chains; plausibility judged by the reviewer |
| R20 | Holdout | Before open; during the run | Enforced for declared files and samples; detectable for reads |
| R2 | Budget | Before open | Enforced per chain; detectable across chains |
| R9 | Config captured | At open | Enforced capture; declared use |
| R24 | Other heads recorded | At open | Enforced |
| R23 | Witness at open | After the open row | Enforced attempt; detectable status |
| R10 | Always recorded | At close | Enforced; a hard kill is detectable |
| R11 | Result required | At close | Enforced |
| R12 | Counts as used | Always | Enforced |
| R13 | Atomic writes | Every write | Enforced |
| R14 | Append only | Always | Enforced for BGL; others' edits detectable |
| R15 | Hash chain and signatures | Every row | Detectable |
| R16 | Unique IDs | Every row | Enforced |
| R18 | Ledger lock | Every read and append | Enforced |
| R25 | Dry runs | `dry=True` | Enforced for reads through Python; C-level reads not covered |

Pre-open checks run in the order of the table. A failure writes one `refused` row and raises `RunRefused`. A refused run uses no budget.

**Before a run opens**

- **R19. Writer identity.** `.bgl/writer` names a registered chain code. The key in the home config folder matches that code's public key in the registry. The machine ID matches the chain's earlier rows. Otherwise the run is refused, and the message names `bgl join`.
- **R7. Chain intact.** The writer's own chain must verify (hashes, signatures, no torn tail) before any row is added: open, close, note, refused or any other. A broken chain belonging to another writer does not block this writer. `status` and `verify` report it, and it fails the gate (G5).
- **R1. Known study.** The study is declared in `bgl.toml`.
- **R22. Study lock.** If the study has no lock row yet and the run is counted, the run writes the lock row before its open row. If a lock exists, the current study table and registry must equal the locked values plus any accepted amendments. Changed spec files need an `amend` row, or a confirmatory run is refused.
- **R3. Clean code.** The git working tree has no uncommitted changes to tracked files, except BGL's own files: chains, proofs and `runs_dir`. The script must be tracked and unchanged. The current commit is recorded. Untracked data files are allowed. `status` warns if the ledger has uncommitted rows.
- **R3b. No untracked Python files.** `git ls-files --others --exclude-standard -- '*.py'` must return nothing. The refusal names each offending file and both fixes, for example: `Refused (R3b): untracked Python files in the repo: notebooks/try.py. Commit them, or move them to scratch/ (ignored by git).`
- **R17 at open.** Every loaded module whose file is inside the repository must be tracked by git. This scan also catches modules imported above the `with` block, and files in ignored folders such as `scratch/`.
- **R8. Inputs declared.** The run declares its input files. Each must exist, and its size and SHA-256 are stored in the open manifest. Paths are stored with forward slashes. A missing input refuses the run.
- **R4. No duplicate (counted runs only).** An earlier counted open in the same study with the same script hash, code fingerprint, config hash and input hashes refuses the run.
  - The code fingerprint is the SHA-256 over the sorted paths and blob hashes of every tracked `.py` file, excluding BGL's files.
  - `what_changed` must be non-empty, but is not compared.
  - An exact rerun, for example to check reproducibility, is an exploratory run.
  
  *Reason:* one changed word used to defeat the free-text check. The commit cannot be the key, because it changes every time the ledger is committed.
- **R5. Prediction present.** Diagnostic and confirmatory runs carry a non-empty prediction.
- **R21. Variants declared.** Non-data runs declare `variants`:
  - exploratory: 0 or more;
  - diagnostic: at least 1;
  - confirmatory and replay: exactly 1.
  
  Data runs declare none.
- **R6. Confirmatory runs and void.**
  - A confirmatory run lists one or more of its study's claims. Each claim may be decided by only one confirmatory run.
  - In a study with a holdout, confirmatory runs use `sample = holdout`, and the single holdout look (R20) decides every claim it lists.
  - It names the design run and variant it confirms (`selects`). In a study without a holdout, the confirmatory run is a design run, and the selected trial is itself.
  - **Void.** `void RUN_ID "reason" --fix COMMIT` is allowed for a confirmatory run of any status, when its record shows it exposed nothing:
    - no captured output after the exposure point (the holdout load for holdout runs; the open for other runs); and
    - no declared outputs written.
  - The fix commit must descend from the run's commit and change tracked code. A claim may be voided once, and the rerun cannot be voided. The void row goes in the voiding writer's own chain.
  - *Reason:* basing the void on what the run could have shown closes the crash-on-demand route.
- **R20. Holdout.** A run with `sample = holdout` is refused if:
  - the study has `holdout = false`;
  - the run is not confirmatory;
  - a custodian is declared and the writer's person is not the custodian;
  - an earlier holdout open exists in any chain on this machine, unless a void freed it.
  
  Also:
  - A design run is refused if any declared input is a holdout file, a restricted file or a tainted file.
  - After the lock, any run declaring a restricted file is refused.
  - `sample = live` is only for replay runs during incubation, by the custodian when one is declared.
  - The audit hook closes a run as `invalid` if it opens a holdout or restricted file it may not read.
  - Sample dates are declared: design runs must end before `holdout_start`, and holdout runs must start on or after it.
- **R2. Budget.** A counted run is allowed only while the study's counted opens are fewer than its budget plus accepted increases. Budget counts counted opens in every chain on this machine. Exploratory and data runs use no budget.

**Writing the open row**

After the checks pass, the run takes its lock, writes the lock row if R22 requires one, writes the open manifest, and then writes the open row.

- **R9. Config captured.** The settings the script passes are stored in the open manifest, and their canonical hash in the open row.
- **R24. Other heads recorded.** The open row records the head hash of every other registered chain as it is on this machine, or null if that chain is absent. The gate uses these to order chains and to detect a truncated chain.
- **R23. Witness at open.** After the open row is written and before the run's code starts, BGL submits the new head to Rekor and to the OpenTimestamps calendars (see Witness). A failure does not stop the run.

From the open row on, every outcome except a refusal uses budget, because the researcher may have seen output.

**While the run is open**

- Output is captured for every counted run: stdout and stderr, each to its own git-ignored file.
- The audit hook records files opened through Python that were not declared (R8).

**When the run ends**

- **R17 at close.** The loaded-module scan runs again. An untracked module inside the repository closes the run as `invalid`, with the file path as the reason. Code outside the repository and worker processes are not checked.
- **R10. Always recorded.** A finished run gets a close row. An exception closes it as `crashed`, with the error, and is re-raised. Ctrl+C closes it as `interrupted`. If the run was already closed by `abandon`, the normal close is still written, and `verify` reports it as a late close.
- **R11. Result required.** A run that finishes without a result is closed as `no_result`, not `ok`.
- **R12. Counts as used.** Budget used at open stays used, whatever the status or any void. A voided run's variants stay in N.

**Every write**

- **R13. Atomic writes.** Each file goes to a temporary file, which is flushed with `fsync`, renamed into place, and followed by an `fsync` of its folder. The manifest is written before the open row, and the close file before the close row. A crash between the two leaves an orphan file with no row, which `verify` reports. An orphan's run ID is never reused.
- **R14. Append only.** BGL never edits or deletes a complete row.
  - **Torn tail.** A final line without a newline is a torn tail. Every append is refused (R7) until `bgl repair` removes it and appends a `repair` row recording the removed bytes' length and SHA-256.
  - **Corrections** are `note`, `ack`, `void`, `amend` and `abandon` rows. An abandon is a close row with status `abandoned`.
- **R15. Hash chain and signatures.** Each row stores:
  - `prev_hash`, the previous row's hash in the same chain;
  - `hash`, the SHA-256 of the row's canonical JSON without `hash` and `sig`;
  - `sig`, an Ed25519 signature of the hash by the writer's key.
  
  An open row stores its manifest's hash. A close row stores the hashes of its close file, return series file and captured output. `verify` recomputes every hash, checks every signature against the registry, and reports the first broken row in each chain.
- **R16. Unique IDs.** A run ID is `<prefix>-<letter><counter>-<CODE>`, for example `AL-R007-CG1`.
  - The letters are R (counted), X (exploratory), D (data), P (replay), F (refused), T (dry) and I (imported).
  - Counters run separately per study, letter and chain.
  - The open and close rows of one run share its ID.
  - IDs are never reused, including an orphan's.
  - Imported rows keep their original ID in `legacy_id`.
  - Rows that are not runs (lock, amend, seal, gate and the like) carry the ID of the run they concern, or null.
- **R18. Ledger lock.** Reads, verification and appends happen under a ledger lock, `.bgl/ledger.lock`, held with the same OS lock as run locks. Two processes on one machine take turns, and both rows chain onto the right head.

**R25. Dry runs.** `start_run(..., dry=True, dry_inputs={declared_path: fixture_path})` runs every check, then runs the script body with each declared input replaced by its fixture.

- While it runs, the audit hook blocks any file open except Python source and compiled files, files inside the Python installation and site-packages, and the declared fixtures.
- It writes a `dry` row, which uses no budget, and nothing else.
- C-level readers may get past the hook. The `dry` row keeps the attempt on record.

*Reason:* developers need to test a script's body, but a dry run that could read real data would be an unlogged look at it. Testing analysis functions with ordinary pytest fixtures, outside BGL, is the recommended pattern.

**Unclosed runs.** A run with an open row and no close row is unclosed. A hard kill (power cut, `kill -9`) produces one. A live run is identified by its lock, not by a process ID. Each run holds an OS lock on its lock file for its whole life. `verify` tries to take that lock without waiting:

- **Lock taken by `verify`:** the process that held it is dead. The run is unclosed, and `verify` fails it.
- **Lock refused:** a live process still holds it. The run is in progress, not a failure.
- **Another machine (by machine ID), or a synced or network folder:** `verify` cannot tell. It says so, fails, and asks the user to run `verify` on the machine where the run happened, or to abandon the run.

Clearing a failure needs `abandon RUN_ID "reason"`, which appends a close row with status `abandoned` to the abandoning writer's own chain. It is refused while the lock is held on this machine, and on another machine unless `--force` is given. The run keeps its budget and its variants.

**Lifecycle**

| Outcome | Rows written | Budget used |
| --- | --- | --- |
| Refused (any pre-open check fails) | One `refused` row with the rule ID and reason | No |
| First counted open of a study | `lock` row, then open row, then close row | Yes |
| Opened, then ends `ok`, `no_result`, `crashed` or `interrupted` | Open row, then close row | Yes, if counted |
| Opened, still running, lock held | Open row only. `verify` reports it in progress | Yes, if counted |
| Opened, then hard-killed | Open row only. `verify` fails it until abandoned | Yes, if counted |
| `abandon RUN_ID` | Close row with status `abandoned`, in the abandoning writer's chain | Already used |
| Untracked repository code loaded (R17) | Close row with status `invalid` | Yes, if counted |
| `void RUN_ID --fix COMMIT` | `void` row in the voiding writer's chain | Already used; variants stay in N |
| Dry run | One `dry` row | No |

## File formats

All committed files live in the project's git repository, so git history backs up each chain. Git history alone does not prove a chain is complete; the witness does that (see Witness).

**Project config** (`bgl.toml` at the repository root)

```toml
[project]
name = "example_project"
mode = "team"
ledger = "bgl/ledger"
runs_dir = "bgl/runs"

[project.chains.CG1]
person = "researcher_a"
key = "ed25519:TODO(bgl): paste the public key printed by bgl join"

[project.chains.CG2]
person = "researcher_a"
key = "ed25519:TODO(bgl): paste the public key printed by bgl join"

[project.chains.HC1]
person = "custodian_a"
key = "ed25519:TODO(bgl): paste the public key printed by bgl join"

[project.witness]
rekor_url = "https://rekor.example"
ots_calendars = ["https://calendar-a.example", "https://calendar-b.example"]

[studies.alpha]
budget = 20
prefix = "AL"
claims = ["effect_exists", "effect_persists"]
prior_trials = 0
holdout_start = 2025-01-01
holdout_files = ["data/holdout/alpha_holdout.parquet"]
restricted_files = ["data/raw/prices.parquet"]
custodian = "custodian_a"
spec_files = ["docs/studies/alpha.md"]

[studies.alpha.returns]
periods_per_year = 252
basis = "Daily close-to-close portfolio return on declared capital"
risk_free = "data/rates/rf_daily.csv"

[studies.alpha.gate]
dsr_threshold = 0.95
psr_threshold = 0.95
pbo_threshold = 0.05
pbo_min_trials = 10
cscv_blocks = 16
min_block_periods = 20
min_live_days = 90
live_witness_lag_days = 3

[studies.alpha.gate.costs]
model = "flat"
cost_bps = 5

[studies.alpha.gate.basis]
dsr_threshold = { kind = "citation", text = "Bailey and López de Prado (2014)" }
psr_threshold = { kind = "reasoning", text = "Same confidence as the DSR bar, applied to one holdout look" }
pbo_threshold = { kind = "citation", text = "Bailey et al. (2017), section 3.1" }
pbo_min_trials = { kind = "reasoning", text = "Below ten trials the rank statistic is too coarse to read" }
cscv_blocks = { kind = "citation", text = "Bailey et al. (2017)" }
min_block_periods = { kind = "reasoning", text = "About one trading month per block" }
min_live_days = { kind = "reasoning", text = "One quarter of live trading before deployment" }
live_witness_lag_days = { kind = "reasoning", text = "Calendar days; a Friday return written on Monday still passes" }
costs = { kind = "reasoning", text = "Typical all-in cost for liquid large caps" }

[studies.beta]
budget = 10
prefix = "BE"
claims = ["effect_exists"]
prior_trials = 12
holdout = false
spec_files = []

[studies.beta.returns]
periods_per_year = 365
basis = "Daily portfolio return on declared capital, UTC day boundaries"
risk_free = "none"
risk_free_reasoning = "Self-financing long-short book; financing is ignored"
```

The numbers and texts in this example show the format. They are not recommended values.

**Ledger** (`<ledger>/<CODE>.jsonl`, one chain per writer): one JSON object per line, appended, never rewritten.

- *Reason for JSON Lines over CSV:* rows can hold nested values, each line is independent so appending never touches earlier rows, and line-by-line git diffs show exactly what was added. A CSV export is a command (`list --csv`).
- *Reason for `event` and `type` as separate fields:* a run has an open row and a close row, so the row kind and the run type cannot share one field.
- **Canonical bytes.** The bytes hashed are `json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False)` without `hash` and `sig`, encoded as UTF-8. Hashed fields hold no JSON floats: every non-integer number is stored as a string in its canonical text (C19), so every implementation writes the same bytes.

**Events:** `open`, `close`, `refused`, `dry`, `note`, `ack`, `void`, `lock`, `amend`, `repair`, `seal`, `imported`, `incubation`, `live_return`, `gate`, `registration`.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | text or null | Run ID (R16), or the run a non-run row concerns |
| `legacy_id` | text or null | Imported rows: the original ID |
| `event` | text | One of the events above |
| `chain` | text | The writer's chain code |
| `study` | text or null | A study from the config |
| `type` | text or null | `data`, `exploratory`, `diagnostic`, `confirmatory` or `replay`. Null on non-run rows. Whether a run is counted is worked out from this; imported rows also keep their declared `counts`, for display only |
| `claims` | list or null | Confirmatory open rows: the claims the run decides |
| `selects` | object or null | Confirmatory open rows: `{run, variant}`, the design trial being confirmed |
| `variants` | integer or null | Open rows of non-data runs: the number of variants evaluated (declared) |
| `sample` | text or null | Open rows: `design`, `holdout` or `live` |
| `sample_start`, `sample_end` | date or null | Open rows: the data dates the run used (declared) |
| `script` | text | Path of the script, relative to the repository, with forward slashes |
| `script_sha256`, `code_sha256`, `config_sha256`, `inputs_sha256` | text | Open rows: the facts R4 compares |
| `what_changed` | text | One change, in words |
| `prediction` | text or null | Open rows, written before the run |
| `status` | text | Open rows: `open`. Close rows: a final status. Refused rows: `refused` |
| `outcome` | object or null | Confirmatory close rows: each claim mapped to `supported`, `not_supported` or `inconclusive` (declared) |
| `result` | text or null | Close rows: the summary the script set |
| `commit` | text | Git commit the run used |
| `user` | text | Git user name and email. Self-reported; the row signature says which writer wrote the row |
| `commit_signature` | text | Git's status code for the commit signature (`G`, `B`, `U`, `N`). For information only |
| `machine_id` | text | The writer's machine ID. Used for lock checks |
| `host`, `pid` | text, integer | For information only |
| `lock_file` | text | Open rows: path of the run's lock file in `.bgl/locks/` |
| `started`, `ended` | timestamp | UTC, ISO 8601. Self-reported; stamps prove existence by a block time |
| `manifest`, `manifest_sha256` | text | Open rows: the open manifest and its hash |
| `close_file`, `close_sha256` | text or null | Close rows: the close file and its hash. Null on abandoned closes |
| `returns_sha256` | text or null | Close rows: hash of the return series file |
| `output_sha256` | object or null | Close rows: hash of each captured output stream |
| `other_heads` | object or null | Open rows: each other registered chain mapped to its head hash on this machine, or null (R24) |
| `reason` | text or null | Refusal reason and rule ID, abandon reason, void reason, note text, or amendment reason |
| `fix_commit` | text or null | Void and correction-amendment rows: the fixing commit |
| `amend` | object or null | Amend rows: `{key, old, new}` |
| `lock` | object or null | Lock rows: canonical study table, chain registry and spec-file hashes |
| `seal` | object or null | Seal rows: each holdout file's plaintext and sealed hashes |
| `removed` | object or null | Repair rows: `{length, sha256}` of the removed bytes |
| `period`, `value`, `turnover`, `cost` | text or null | Live-return rows: the period, its portfolio return, its turnover and its realised trading cost as a return drag, all as strings (declared) |
| `position_sha256` | text or null | Live-return rows: the position hash of the period's target positions |
| `live_inputs` | object or null | Live-return rows: each recorded live input file's path and SHA-256, saved by the live system for the replay |
| `report_sha256` | text or null | Gate rows: hash of the gate report |
| `format_version` | integer | Format version of this row |
| `prev_hash` | text | The previous row's `hash` in this chain (64 zeros for the first row) |
| `hash` | text | SHA-256 of the canonical bytes |
| `sig` | text | Ed25519 signature of `hash` by the writer's key |

Refused runs get one `refused` row and never use budget. *Reason:* a refusal is evidence that the rules worked.

**Open manifest** (`<runs_dir>/<ID>.json`): written at open, never rewritten.

- `config`: the settings the script passed.
- `inputs`: each declared input's path, size and SHA-256.
- `environment`: Python version, platform, and the SHA-256 of `uv.lock` (or the installed package list if there is no lock file).
- `git`: commit, branch, whether untracked files existed, user, commit signature status.
- `costs`: a copy of the study's locked cost model. *Reason:* the gate checks that every counted run used the locked model.

**Close file** (`<runs_dir>/<ID>.close.json`): written at close.

- `outputs`: each declared output's path and SHA-256.
- `files_opened`: files opened through Python during the run that were not declared (R8).
- `untracked_modules`: the result of the R17 scan at close.
- `notes`: remarks added with `run.note`. A note is lost if the run is hard-killed before close.
- `error`: type, message and traceback, for crashed runs.
- `output`: the captured stdout and stderr files, their SHA-256, and the holdout load position in each stream.
- `series`: per variant, the statistics BGL computed from the return series (T, mean, standard deviation, Sharpe, skewness, kurtosis), as strings.
- `positions`: replay runs only, each period mapped to its position hash.
- `outcome`: as in the close row.

**Return series file** (`<runs_dir>/returns/<ID>.csv`): written at close for every counted run that called `run.returns`, voided runs included, because PBO needs every trial.

- *Content:* long format with columns `period,variant,gross,turnover`, sorted by variant and then period.
  - Gross return is portfolio-level and before costs.
  - Turnover is $\tau_t = \sum_i |w_{i,t} - w_{i,t-1}|$, as computed by the script (declared).
  - Never per-asset values, never raw data.
- *Periods:* ISO 8601 dates, or UTC datetimes ending in `Z` for intraday studies. Every variant is on the study's period grid.
- *Format:* deterministic. UTF-8, LF line endings, and a fixed number of decimal places set by `format_version` (10 in version 1). The same series always gives the same bytes.
- *Git:* the `returns/` folder is git-ignored. `init` adds it to `.gitignore`.
- *Backup:* the owner backs up `returns/` privately, as a copy of the folder, never the project itself, since the project must not sit in a synced folder.
- *Missing files:* on a machine without the series, `verify` reports them as not checkable rather than passing them.
- *Licence:* before the first counted run, the owner confirms that the data licence allows storing the derived series locally. Where it does not, the study cannot compute PBO, and stage 2 fails with that reason.

**Captured output** (`<runs_dir>/output/<ID>.stdout` and `.stderr`): git-ignored, because output may contain licensed data. Only their hashes are committed.

**Proofs** (`<ledger>/proofs/<CODE>/<head>.ots`): committed.

**Local files.** `.bgl/` holds the writer file, run locks, the ledger lock and temporary decrypted holdout files. It is git-ignored and machine-local. Keys and the machine ID are in the user's home config folder.

**Line endings and paths.** `init` adds entries to `.gitattributes` that turn off line-ending conversion for the ledger folder and `runs_dir`, so hashed files never change bytes in git. Every stored path uses forward slashes.

**Supported git workflow.** Commits that runs used must stay reachable: branches carrying them are merged or fast-forwarded, never squashed or rebased, and CI checks out full history. `verify` reports an unreachable commit as its own failure ("commit not reachable"), with this explanation.

**The lock file.** On Linux and macOS, a run holds `fcntl.flock` with an exclusive lock; on Windows, `msvcrt.locking`. The lock is held on an open file, not inferred from the file's existence, because a crash leaves the file behind. Locks are unreliable on network drives and inside synced folders (OneDrive, Dropbox, iCloud Drive, Google Drive). `init` warns when the project sits in one.

## Config rules (bgl.toml)

The config loader enforces these. Each rule has a test that fails if the rule is removed. Every error carries:

- its rule ID (for example `C3`);
- the file;
- the key path (for example `studies.alpha.budget`);
- the value found and what was expected.

Loading runs in two phases.

- **Phase 1** (reading the file: C8, C9) fails fast with a single `ConfigError`, because there is nothing to check without a parsed file.
- **Phase 2** (every other rule) collects every error. A check on a field whose parent table is missing or invalid is skipped, so one mistake never produces follow-on errors. For example, C15 is skipped when the chain registry is invalid, and C6 compares only prefixes that passed C4.

At the end, the loader raises one `ConfigErrorGroup` (a subclass of `ExceptionGroup`) if any error was collected. Otherwise it builds the config records, which use read-only mappings. Tests match on the rule ID and key path, not on message wording. No key has a default: a missing key is always an error. *Reason:* a forgotten key that silently takes a default (a zero `prior_trials`, say) understates N without anyone noticing.

**Shared checks**

- *Whole numbers* (`budget`, `prior_trials`, `cscv_blocks`, `periods_per_year` and every other count) use one check: an integer, not a boolean, at least the stated minimum. *Reason:* in Python a boolean counts as an integer, so `budget = true` would otherwise pass as 1.
- *Decimals* are Python floats, `tomllib`'s default. A key that takes a decimal also accepts a TOML integer, which is converted to a float, so `cost_bps = 5` and `cost_bps = 5.0` are the same value. Booleans are refused. *Reason:* the statistics are computed in floats, so config values never have to be converted between number types, and a harmless reformat such as `0.95` to `0.950` gives the same value and the same lock.
- *Paths* use C2's checks.
- *Non-finite numbers:* TOML allows `nan` and `inf`. Both are rejected wherever a number is expected. *Reason:* every comparison with nan is false, so a nan threshold would silently pass or fail everything.
- *Boundaries:* every range in these rules states whether each end is included. Each range has a test at its exact boundary.

**Rules**

1. **C1. Project table.** A `[project]` table exists, and its `name` is a non-empty string. A `[studies]` table exists; it may be empty. Each study name (a key under `[studies]`) is a lowercase letter followed by lowercase letters, digits or underscores, 1 to 32 characters in all. *Reason:* no key has a default, so a missing table is an error, but a project may be set up before its first study. A run against no study is refused by R1.
2. **C2. Paths.** Every path in the config (`ledger`, `runs_dir`, `holdout_files`, `restricted_files`, `spec_files`, `risk_free`) is relative, uses forward slashes, and does not resolve outside the repository.
3. **C3. Study budget.** Each study's `budget` is a whole number, zero or more.
4. **C4. Study prefix.** Each study's `prefix` is 2 to 4 uppercase letters.
5. **C5. Claims.** Each study's `claims` is a list of unique, non-empty strings. An empty list is allowed. Claim names are scoped to their study.
6. **C6. Unique prefixes.** No two studies share a prefix. *Reason:* run IDs must be unique across the project (R16).
7. **C7. Unknown keys.** A key the loader does not recognise is an error. *Reason:* a misspelt key such as `budgt = 20` would otherwise be ignored silently.
8. **C8. Missing or unreadable file.** If `bgl.toml` does not exist, the error tells the user to run `bgl init`. A folder at that path, or a file that cannot be read, is also C8.
9. **C9. Invalid TOML.** A syntax error, or a file that is not UTF-8, reports the file and, where known, the line.
10. **C10. Gate table.** A study's `[gate]` table is optional. If present, it holds exactly these keys, each in range:
    - `dsr_threshold` and `psr_threshold`: numbers strictly between 0 and 1;
    - `pbo_threshold`: a number above 0 and at most 0.5;
    - `pbo_min_trials`: a whole number, at least 2;
    - `cscv_blocks`: a whole number, at least 4 and even;
    - `min_block_periods`, `min_live_days` and `live_witness_lag_days`: whole numbers, at least 1.
    
    A `costs` table holds `model = "flat"` and `cost_bps` (a number, zero or more), the cost per unit of turnover in basis points. A `basis` table holds one entry for each gate key and one for `costs`, and nothing else. Each entry has `kind` (`citation` or `reasoning`) and non-empty `text`.
    
    *Reason for the 0.5 cap:* above 0.5, a study would pass even when picking its best trial does worse than chance.
11. **C11. Holdout.** Every study declares exactly one of `holdout_start` (an unquoted TOML date, no time) or `holdout = false`.
    - With `holdout_start`, `holdout_files` is a non-empty list of paths, and `restricted_files` is a list of paths, which may be empty. The two lists share no path.
    - With `holdout = false`, `holdout_files`, `restricted_files` and `custodian` are errors.
    
    *Reason:* the holdout is only a test if the two samples cannot overlap, and a missing key must never mean "no holdout" by accident.
12. **C12. Chain registry.** `[project.chains]` holds one table per writer. Its code is an uppercase letter followed by uppercase letters or digits, 2 to 6 characters long, with no code appearing twice. Each entry has `person` (non-empty) and `key`: the text `ed25519:` followed by the standard base64 encoding of the 32-byte raw public key, which must decode to exactly 32 bytes and be exactly the encoding of those bytes. No public key appears in two entries; the later entry in the file is the error. The registry is non-empty. Each registered chain's file lives in `ledger`.
13. **C13. Prior trials.** Every study declares `prior_trials`, a whole number, zero or more, even when it is 0.
14. **C14. Mode.** `[project] mode` is `solo` or `team`. In `team` mode, every study with a holdout declares a `custodian`, and witnessing is on (C17).
15. **C15. Custodian.** `custodian`, when present, names a `person` in the registry. In `solo` mode it may be absent. The gate checks that the custodian ran none of the study's design runs (G2).
16. **C16. Returns.** Every study has a `[returns]` table with:
    - `periods_per_year`: a whole number, at least 1;
    - `basis`: a non-empty description of what a return is measured on;
    - `risk_free`: a path to a file of per-period risk-free returns on the study's grid, or `"none"`. With `"none"`, `risk_free_reasoning` is a required non-empty string. With a path, `risk_free_reasoning` is an error.
17. **C17. Witness.** `[project.witness]` holds `rekor_url` (an HTTPS URL) and `ots_calendars` (a non-empty list of HTTPS URLs). A `solo` project may instead set `witness = false` under `[project]`. A `team` project may not.
18. **C18. Spec files.** Every study declares `spec_files`, a list of paths, which may be empty.
19. **C19. Canonical values.** A decimal key's value is converted to a float and written as Python's shortest round-trip text for that float (its `repr`), stored as a string. Integers are written as integers. Dates are ISO 8601 text. The canonical study table used by the lock contains no JSON floats. *Reason:* the shortest round-trip text of a float is the same on every platform, so the lock reproduces the same bytes on every machine.
20. **C20. Placeholders.** A free-text value (a gate basis `text`, a returns `basis` or `risk_free_reasoning`), a `key`, a calendar URL or the Rekor URL that contains the placeholder marker `TODO(bgl)` (case-sensitive) is an error. Every placeholder in BGL's templates and documentation contains this marker. A value that fails C20 gets no further check for that key, so a template key reports C20 rather than C12. *Reason:* an unedited template must not load, and angle brackets alone would reject ordinary reasoning such as `PBO < 0.5`.

## Python API

One function opens a run; the run object records everything else.

```python
from bgl import start_run

with start_run(
    study="alpha",
    run_type="diagnostic",
    script=__file__,
    what_changed="Lookback grid 10 to 60 days in steps of 10",
    prediction="Sharpe rises with lookback up to about 40 days, then flattens",
    inputs=["data/design/alpha_design.parquet"],
    config={"lookbacks": [10, 20, 30, 40, 50, 60]},
    sample="design",
    sample_start="2015-01-02",
    sample_end="2024-12-31",
    variants=6,
) as run:
    gross, turnover = run_grid(...)  # the research code
    run.returns(gross=gross, turnover=turnover)
    run.result("Best lookback 40; flat above it")
```

| Call | Does |
| --- | --- |
| `start_run(...)` | Runs the pre-open checks, then writes the lock row if needed, the manifest and the open row, then witnesses. Also takes `claims` and `selects` for confirmatory runs, and `dry` and `dry_inputs` for dry runs. Raises `RunRefused` with the rule ID and reason after recording the refusal. |
| `run.returns(gross, turnover)` | Takes, per variant, a gross return series and a turnover series on the study's period grid. Writes the return series file, puts its hash in the close row, and stores per-variant statistics in the close file. Refuses series off the grid, a variant count different from the declared one, and dates outside the declared sample. |
| `run.holdout(path)` | The only way a holdout run loads a holdout file. Decrypts sealed files to a temporary location and records the load position in the captured output. |
| `run.positions(period, weights)` | Replay runs only: takes one period's target weights (asset identifier to weight), computes the position hash, and records only the hash. |
| `position_hash(weights)` | The function the live system calls to compute the same hash for its `live_return` rows. Weights are rounded to the number of decimal places fixed by `format_version` (8 in version 1), sorted by asset identifier, written as canonical text and hashed with SHA-256. |
| `run.output(path)` | Declares an output file. Its hash is recorded at close, and it is marked tainted if the run read holdout data. |
| `run.result(text)` | Sets the summary. Required for status `ok`. |
| `run.outcome(claim, value)` | Confirmatory runs: declares a claim's outcome (`supported`, `not_supported`, `inconclusive`). |
| `run.note(text)` | Adds a remark to the close file. |
| `run.id` | The run's ID. |
| `verify_ledger()` | Verifies every chain on this machine. Returns the first broken row per chain. |
| `budget(study)` | Counted runs used and left in a study, over the chains on this machine. |
| `add_note(run_id, text)` | Appends a `note` row (R14). |

- **On exit**, the run writes its close file, then its close row. An exception is recorded as `crashed` and re-raised. Ctrl+C is recorded as `interrupted`.
- **Data runs** use `run_type="data"` and need no prediction, variants or returns.
- **Confirmatory holdout runs** pass `claims=[...]`, `selects={"run": "AL-R012-CG1", "variant": 4}`, `sample="holdout"` and `variants=1`.

## Command-line tool

The command-line tool never creates a run; runs are made only by scripts through `start_run`. *Reason:* a tool that could start runs would be a second route around the rules.

| Command | Writes | Does |
| --- | --- | --- |
| `init` | Config, first writer | Writes a starter `bgl.toml` (with no gate table), the first writer's key and registry entry, an empty chain, `.gitignore` entries (`.bgl/`, `scratch/`, `<runs_dir>/returns/`, `<runs_dir>/output/`, `*.sealed`), and `.gitattributes` entries. Never overwrites a file, and appends only missing entries. Warns inside a synced folder or on a network path. |
| `join CODE PERSON` | Local files | Sets up a new writer on this machine and prints its registry entry. |
| `status` | No | Per study: budget used and left, N so far, claims decided and open, unclosed runs, voided runs, plaintext holdout files present, unwitnessed opens. Per chain: head hash and witness state. Warns about uncommitted ledger rows. |
| `list [--study S] [--csv]` | No | One line per run, newest last. |
| `show RUN_ID` | No | The run's rows and manifests, readable. |
| `verify [--contains HASH]` | No | Checks every chain and file (see below). Exits non-zero on any failure, so it can run in CI. |
| `note RUN_ID "text"` | `note` | Annotates a run. |
| `ack RUN_ID "reason"` | `ack` | Acknowledges a late close after review, which lets G3 pass with a note. |
| `abandon RUN_ID "reason" [--force]` | Close row | Closes an unclosed run (see Unclosed runs). |
| `void RUN_ID "reason" --fix COMMIT` | `void` | Voids a confirmatory run (R6). |
| `amend STUDY KEY VALUE "reason" [--fix COMMIT]` | `amend` | Applies a typed amendment (see Study lock and amendments). |
| `repair` | `repair` | Removes a torn tail from this writer's chain (R14). |
| `seal STUDY [--reseal]` | `seal` | Custodian only: seals the holdout files, or reseals them with a new custodian's key after a handover. |
| `witness` | No | Retries pending Rekor and OpenTimestamps submissions, and upgrades pending stamps. |
| `incubate STUDY` | `incubation` | Custodian (or the researcher in solo mode): starts incubation for the confirmatory commit. |
| `live STUDY --from FILE` | `live_return` | Appends one row per live period, each witnessed: the return, turnover, realised cost, position hash and recorded input-file hashes, written by the live system. |
| `gate STUDY [--bitcoin-node URL \| --explorers A,B]` | `gate` | Runs the deployment gate and writes its report. |
| `stats dsr RUN_ID` | No | Shows the DSR inputs and result for one trial. |
| `register STUDY` | `registration` | Optional: bundles the spec files, their hashes and the head hashes for public registration on OSF. |
| `migrate --from-csv PATH` | `imported` | One-off import of a log kept before adoption (see below). |

**`verify` checks:**

- every chain's hashes and signatures, and that no torn tail exists;
- that every close row has an open row, and every open row has a close row or is in progress on this machine;
- late closes, each flagged until acknowledged;
- that every manifest, close file, return series and captured output matches its hash (or is reported not checkable where missing);
- orphan files;
- that recorded commits are reachable;
- that the config matches each lock plus its amendments;
- that no chain has rows from two machines;
- that no plaintext holdout file is present;
- `--contains` membership of a given hash.

**`migrate`.** The import file is in BGL's documented import format: one row per past run, with ID, study, run type, counts, claim, sample, what changed, start and end times, status and result. Each project converts its own old log into this format. Imported rows:

- keep their original ID in `legacy_id`, and get an I-letter ID;
- keep their declared study;
- are marked `imported`, since their commits and inputs were never recorded.

They are display-only for budget and N: legacy trials enter N only through `prior_trials`, which must be at least the number of imported non-data rows in the study. They are binding for claims and the holdout: an imported confirmatory row decides its claims, and an imported holdout row uses up the holdout. A row whose study is missing or not in the config is refused, and so is any import that would change an existing row's study. The import is all or nothing.

## Trials and N

$$N = \text{prior\_trials} + \sum_{\text{non-data design runs}} \text{variants}$$

- The sum covers every non-data run with `sample = design`, in every registered chain, of any status, voided runs included. Exploratory runs are included.
- Confirmatory holdout runs, replay runs, data runs and imported rows are not included.
- N is a lower bound (see Scope). The gate report shows N broken down by prior trials, run type and chain.

*Reason:* N measures the search. A run that tested 500 variants searched 500 times, whatever its budget cost.

## Statistics

All statistics are per period, on net excess returns. Annualised figures are for display only. Implemented with `numpy`. Every formula is checked against its source before building.

**Net excess returns.** For variant $v$ in period $t$, with gross return $g_t$, turnover $\tau_t$, locked cost $c$ (the `cost_bps` value divided by $10^4$) and risk-free return $f_t$ from the locked file (zero when `risk_free = "none"`):

$$r_t = g_t - \tau_t\, c - f_t$$

**Estimators.** For a series of $T$ returns:

- $\widehat{SR} = \bar r / s$, where $s$ is the standard deviation with divisor $T-1$;
- $\hat\gamma_3$ and $\hat\gamma_4$ are the population skewness and kurtosis, with divisor $T$. The kurtosis is not excess, so a normal distribution gives 3.

**PSR** against a hurdle $SR^*$ (Bailey and López de Prado, 2012):

$$\mathrm{PSR}(SR^*) = \Phi\left( \frac{(\widehat{SR} - SR^*)\sqrt{T-1}}{\sqrt{1 - \hat\gamma_3 \widehat{SR} + \frac{\hat\gamma_4 - 1}{4}\widehat{SR}^2}} \right)$$

**DSR hurdle** (Bailey and López de Prado, 2014), with $\gamma \approx 0.5772$ the Euler–Mascheroni constant:

$$SR^* = \sqrt{V}\left( (1-\gamma)\,\Phi^{-1}\!\left(1 - \tfrac{1}{N}\right) + \gamma\,\Phi^{-1}\!\left(1 - \tfrac{1}{N e}\right) \right) \quad (N \ge 2), \qquad SR^* = 0 \quad (N = 1)$$

**Variance of trial Sharpe ratios (BGL's choice, not the paper's):**

$$V = \max\left(\hat V,\ \frac{1}{T-1}\right)$$

- $\hat V$ is the sample variance of $\widehat{SR}$ across every recorded design variant with a series, and $T$ is the selected variant's length.
- With fewer than two recorded variants, $V = 1/(T-1)$.
- *Reason:* $1/(T-1)$ is about the sampling variance of a Sharpe estimate for an unskilled, independent trial. Prior trials have no Sharpe ratios, so $\hat V$ cannot see them. The floor keeps the hurdle from collapsing when the recorded trials are near-copies, and it is conservative in the same way as using raw N.

**DSR** $= \mathrm{PSR}(SR^*)$, computed on the selected design variant (the one named by the confirmatory run's `selects`), over its design series. *Reason for keeping the paper's approximation:* it stays within about 0.05 of the exact expected maximum for every N, and agreeing with the published DSR matters more than that error.

**MinTRL** (reported, no verdict). With $z_\alpha = \Phi^{-1}(\text{dsr\_threshold})$:

$$\mathrm{MinTRL} = 1 + \left[1 - \hat\gamma_3\widehat{SR} + \tfrac{\hat\gamma_4 - 1}{4}\widehat{SR}^2\right]\left(\frac{z_\alpha}{\widehat{SR} - SR^*}\right)^2$$

It is reported as "not reachable" when $\widehat{SR} \le SR^*$. *Reason:* with the same hurdle and confidence, $T \ge \mathrm{MinTRL}$ exactly when DSR passes, so it adds no separate verdict.

**PBO** by combinatorially symmetric cross-validation (Bailey et al., 2017).

- **The matrix.** One column per recorded design variant with a series, over the common date range all of them cover. Variants outside the common range are listed and excluded from PBO, but stay in N.
- **Blocks.** Split the rows into $S$ equal blocks. $S$ is `cscv_blocks`, or the largest even $S \ge 4$ for which every block holds at least `min_block_periods` periods.
- **Splits.** For each way of choosing $S/2$ blocks as in-sample, find the variant with the best in-sample Sharpe. Let $\omega$ be its out-of-sample rank divided by (number of variants + 1), and $\lambda = \ln\frac{\omega}{1-\omega}$.
- **PBO** is the share of splits with $\lambda \le 0$.
- **Also reported:** performance degradation and probability of loss, from the same splits.
- **Not computable** with fewer than `pbo_min_trials` variants, or when no even $S \ge 4$ fits. Stage 2 then fails.
- *Speed note:* compute each block's sums once, so each split's Sharpe comes from block sums.

**Holdout PSR.** $\mathrm{PSR}(0)$ on the confirmatory holdout run's net excess series.

**Autocorrelation (reported only).** Lo's (2002) autocorrelation-adjusted annualised Sharpe ratio, for the selected variant and the holdout run. Confirm the exact form and the lag truncation against Lo (2002) before building.

**Bootstrap band (stage 4, reported only).** A stationary bootstrap (Politis and Romano, 1994) of the selected variant's design series, with mean block length chosen automatically (Politis and White, 2004, with the Patton, Politis and White, 2009 correction). It uses 10,000 replications and a seed taken from the study's lock hash, and gives a two-sided 95% band for a live Sharpe of the incubation's length.

**Effective N.** Trials are rarely independent, so raw N overstates the effective number. v0.1 reports raw N as the primary figure. A clustering-based effective N may be added later, as a labelled sensitivity check only. Also read before building: Harvey and Liu (2015) on Sharpe ratio haircuts.

## Deployment gate (`bgl gate STUDY`)

The gate answers one question: may this study's strategy be deployed with real money? It refuses a study without a `[gate]` table, with a clear message.

**Order and verdict.** The verdict is the first failing stage, and nothing later can change it.

- Stage 1 failing halts the gate before any statistic is computed.
- Stages 2 and 3 both run and are both reported.
- Stage 4 is evaluated only when stages 1 to 3 all pass.

*Reason for the order:* if governance fails, the statistics are computed on a record that may be incomplete, so their numbers are not evidence.

**Grade.** A passing verdict is *deployment-grade* only if G1 and G2 reach their top levels. Otherwise it is *pass, not deployment-grade*, with every reason listed.

**Stage 1: Governance**

- **G1. Locked in time.** A lock row exists before every counted open of the study, in every chain, and the current config equals the locked values plus amendments.
  - A gate table added after the lock is labelled late and is never deployment-grade.
  - Claims added after the lock cannot reach deployment-grade.
  - Amended gate values: the original verdict comes first, with the amended result beside it.
- **G2. Witnessed and custodied.** All of these are needed for deployment-grade:
  - the project witnesses;
  - every counted open and the lock row are witnessed: a Rekor entry with a verified inclusion proof, and an OpenTimestamps stamp verified against Bitcoin;
  - every head in every Rekor entry made with a registered writer's key is still in that writer's chain (no discarded or rewritten heads);
  - the study has a holdout and a custodian;
  - the custodian's person ran none of the study's design runs.
  
  The report gives the split provenance, the Rekor instance used, and the source used to check the stamps. If any of these fails, G2 passes at the *ledger-ordered* level, which is not deployment-grade.
- **G3. Every run closed.** No unclosed or in-progress run. Every late close is acknowledged by an `ack` row.
- **G4. Holdout looked at once.** Across all chains, at most one holdout run exposed holdout-derived output, voided runs included, unless the record proves no exposure (R6). Holdout runs were made by the custodian when one is declared. A second exposure fails G4 with "holdout opened twice".
- **G5. Cross-chain recount.** Over the union of all registered chains:
  - every registered chain is present, verifies, and has rows from one machine;
  - every `other_heads` hash appears in its chain;
  - counted opens are within budget plus amendments;
  - each claim was decided by at most one confirmatory run;
  - no counted duplicates exist (R4 facts).
  
  The N used in stage 2 is the N computed here, and the report shows its breakdown.

**Stage 2: Statistical evidence**

- **G6. Deflated Sharpe ratio.** DSR of the selected design variant is at least `dsr_threshold`.
- **G7. Holdout.** In a study with a holdout, $\mathrm{PSR}(0)$ on the confirmatory holdout run is at least `psr_threshold`.
- **G8. Probability of backtest overfitting.** PBO is computable and at most `pbo_threshold`. Prior trials and series outside the common range cannot enter the matrix. The report says PBO covers recorded variants only, and names DSR as the check that covers the rest. Counted runs without a series are listed as "missing from PBO", never dropped silently. On a machine without the series files, PBO is not checkable and stage 2 cannot pass.

The report also gives the raw and annualised Sharpe ratios, $SR^*$, $V$ (and whether the floor applied), N, MinTRL, the Lo-adjusted Sharpe, and PBO's companion measures.

**Stage 3: Implementability**

- **G9. Cost model consistent.** Every counted run's manifest holds the locked cost model. A mismatch fails G9.
- **G10. Survives double costs.** G6 and G7, recomputed with every series' cost doubled, both still pass.

The report states that the cost model is an assumption: BGL applied it, but whether it is realistic is for the reviewer. Capacity is on the reviewer checklist.

**Stage 4: Incubation**

- **G11. Incubation recorded.**
  - An `incubation` row names the confirmatory run's commit, written by the custodian (or the researcher in solo mode).
  - Each live period has a `live_return` row whose OpenTimestamps block time is at most `live_witness_lag_days` calendar days after the end of the period.
  - Each `live_return` row lists the live input files the live system recorded for that period, with their hashes.
  - A `live_return` row carrying a different commit ends the incubation, and a new one must start.
  - The incubation lasts at least `min_live_days` calendar days.
- **G12. Positions replay exactly.** The custodian's replay run uses the incubation commit, with `sample = live`, and declares the recorded live input files as its inputs. Their hashes must equal those in the `live_return` rows. For every live period, the replay's position hash must equal the live row's position hash. Any mismatch fails G12, and the report lists the periods that differ. *Reason:* both sides compute positions from the same recorded inputs with the same code, so an honest live system matches exactly, and no tolerance is needed for a bug to hide in.
- **G13. Live costs within the tested range** (implementation shortfall; Perold, 1988). Over the incubation, the realised trading costs declared in the `live_return` rows must be at most twice the costs the locked model gives for the live turnover:

  $$\sum_t \kappa_t \le 2 \sum_t \tau_t\, c$$

  where $\kappa_t$ is the realised cost in period $t$, $\tau_t$ the live turnover and $c$ the locked cost per unit of turnover. When the model gives zero cost, realised costs must also be zero. *Reason:* G10 showed the strategy survives at twice the modelled cost, so live costs inside that range keep the backtest evidence valid. Realised costs and turnover are declared by the live system.
- **G14. Live evidence reported.** The live Sharpe, its position against the bootstrap band, and MinTRL on live periods. No verdict. *Reason:* over a few months, the band has almost no power to fail a strategy whose edge was overstated.

**Comparisons.** Every gate comparison is inclusive exactly as written:

- DSR, holdout PSR and the double-cost checks pass at or above their threshold ($\ge$).
- PBO passes at or below its bar ($\le$).
- Position hashes must be equal; there is no tolerance.
- Live costs pass at or below twice the modelled costs ($\le$).
- Incubation passes at or after `min_live_days` ($\ge$).
- Live rows pass when their block time is at or before the lag limit ($\le$).

Each comparison has a test at its exact boundary.

**Output rules**

1. Each stage reports PASS or FAIL, the reason and the numbers behind it. The verdict carries its grade.
2. No blended score. One number would hide which stage failed.
3. Every declared value in the report is labelled declared.
4. The result is written as a `gate` row, with the report file's hash. Numbers in rows are strings.
5. The original verdict always comes first. A result under amended values is labelled amended and shown beside it, never in its place.
6. A stage that runs after an earlier failure is labelled "diagnostic, verdict already fixed".

**Data each stage reads**

| Stage | Reads |
| --- | --- |
| 1 | Every chain's rows, lock and amend rows, Rekor entries and proofs, OpenTimestamps proofs, the config |
| 2 | Return series files (checked against their hashes), the risk-free file, the locked costs, N from G5, the thresholds |
| 3 | Each counted run's manifest `costs`, the return series at double cost |
| 4 | `incubation` and `live_return` rows and their stamps, the replay run's position hashes, the locked cost model, the design series for the band |

## v0.2: GUI

The GUI is a **read-only local web page** (`bgl ui`, served on localhost), not a desktop app. *Reason:* a browser page needs no installer and works on every operating system.

**Read-only by design:** runs are only created by scripts, so the GUI can never make or edit a run.

**Views**

1. **Project overview:** studies, budget, N, claims, chain and witness status, unclosed runs.
2. **Run list:** filterable by study, type, status and chain. Refused, crashed, interrupted, abandoned, invalid and unclosed runs are shown, not hidden.
3. **Run detail:** prediction next to result, commit, inputs and their hashes, environment, notes, files opened that were not declared.
4. **Timeline:** every run of a study on one time axis. The lock, amendments, the seal and the holdout run are marked, with each open's witness time beside its self-reported time.
5. **Gate report:** each stage with its numbers, the grade, and every declared value labelled.
6. **Multi-project view:** several projects side by side, for reviewers.

**Built on:** a small local API from the library and a static front end that shows only what the command line can already show, so the GUI adds no new rules to test.

## Using it in a team

- **Writers:** each person on each machine is a registered writer with their own chain and key.
- **Custodian:** a person who is not a researcher on the study. They hold the holdout files, seal them, run the confirmatory holdout run, and run incubation and the replay.
- **Review before money:** a reviewer runs `verify` and `gate`, and works through the reviewer checklist, which ships with the library. Its items include:
  - each declared variant count matches the recorded code;
  - the split script uses no holdout-period information to build the design outputs;
  - the live system records its input files each period and computes position hashes with `position_hash`;
  - every void has a fix commit that plausibly fixes the stated bug;
  - the cost model and return basis are realistic;
  - capacity: the strategy can trade at the intended size.
- **Adoption cost:** install, `init` or `join`, the config, and about ten lines per script. Projects with an old log import it with `migrate`, marked as unverified.
- **Licensed data:** only hashes and paths of data files are committed, so projects using licensed data can share their repository without breaching their terms.
- **Teaching use:** the budget, prediction and holdout rules are a practical way to teach why backtests overfit.

## Testing, packaging and quality

A library that certifies other people's honesty has to be held to a higher standard than the projects it checks.

**Testing**

- **One test per rule:** every R, C and G rule in the canonical lists, written first and seen to fail before the rule is implemented.
- **Real git in tests:** each test builds a temporary repository.
- **No network in tests:** a local fake implementing Rekor's API stands in for Rekor, and OpenTimestamps is tested with fixed proof files.
- **Tamper tests:** edit a byte of an old row, delete a row, swap two rows, re-sign a row with another key, delete a manifest, edit a close file or a return series. `verify` must catch each.
- **Rebuild test:** rebuild a chain with valid hashes and signatures after one of its heads has been pushed and stamped. `verify --contains` and the gate must catch it.
- **Torn-tail test:** a partial final line refuses every append until `repair`, which records the removed bytes.
- **Crash tests:** an exception, a Ctrl+C and a missing result each give the right status and still use budget.
- **Hard-kill, live-run and lock-after-death tests,** on Linux, macOS and Windows.
- **Other-machine test:** a run with a different machine ID makes `verify` say it cannot tell, and fail. A chain with two machine IDs fails.
- **Abandon tests:** refused while the lock is held here; refused on another machine without `--force`. A later normal close is reported as a late close until acknowledged.
- **Ledger concurrency test (R18):** two processes append at once, many times over. No row is lost and `verify` passes.
- **Writer tests (R19):** a missing writer file, an unregistered code, and a key that does not match the registry each refuse the run.
- **Lock tests (R22):** the first counted open writes the lock; a changed budget refuses the next open; an increase by `amend` is accepted; a decrease is refused; a change to a holdout key can never be amended.
- **Custodian handover tests:** before sealing, any writer's amendment is accepted. After sealing, an amendment not signed by the outgoing custodian is refused. Holdout runs are refused until the reseal. A reseal whose plaintext hashes differ from the original fails `verify`. After the holdout run, the handover moves only incubation.
- **Holdout tests (R20):**
  - a design run declaring a holdout, restricted or tainted file is refused;
  - a second holdout run is refused;
  - a non-custodian holdout run is refused when a custodian is declared;
  - a holdout file opened by a design run closes it as `invalid`.
- **Void tests (R6):** a void with no exposure and a valid fix commit frees one rerun. A void after exposure, without a fix commit, or with a fix commit that does not descend from the run or changes no tracked code is refused. A second void on the same claims is refused.
- **Duplicate tests (R4):** identical recorded facts refuse a counted run; one changed input hash allows it; an exploratory exact rerun is allowed.
- **Untracked-code tests (R3b, R17):** an untracked `.py` file refuses the run. A git-ignored module imported above the `with` block refuses it at open. One imported inside the block closes it as `invalid`.
- **Witness tests:** offline submission failures leave the run unwitnessed but complete. `witness` retries succeed against the fake log. An entry whose head is missing from the chain fails G2. An inclusion proof that does not verify fails.
- **Signature tests:** a good, bad, unsigned and unknown-key commit each give the right `commit_signature` value.
- **Canonical hash test:** a fixed row with a known hash, including a non-ASCII value and a decimal stored as its canonical string, gives that exact hash on all three operating systems.
- **Series tests:** the same series written twice gives the same bytes on all three operating systems. A series off the study grid, or with the wrong variant count, is refused.
- **Dry-run tests (R25):** fixtures replace inputs; a Python open of a non-fixture data file is blocked; a `dry` row is written; no budget is used.
- **Audit-hook spike result:** a pyarrow read is tested and its result recorded, whichever way it goes.
- **Property tests** (Hypothesis): any sequence of valid appends verifies, and any single change to any row fails.

**Statistics and gate tests**

- **Fixed vectors:**
  - DSR and PSR against the DSR paper's worked example (verify the numbers against the paper first);
  - hand-computed cases with stated derivations;
  - PBO against a hand-computed $S = 4$ matrix (6 splits);
  - the approximate hurdle against the exact expected maximum of N standard normals, within a stated tolerance.
- **Property tests:** DSR never rises as N grows with everything else fixed; N = 1 gives PSR against zero; the variance floor applies when the recorded variants are identical.
- **One test per gate rule** on fixed ledgers in temporary repositories.
- **Boundary tests:** every threshold comparison is tested at exactly the threshold, and just either side of it.
- **Stage order test:** a stage 1 failure computes no statistic. A stage 2 failure still runs stage 3, and the verdict stays at stage 2.
- **Grade tests:** a solo study with no custodian, a study with `holdout = false`, a late gate table, and an unwitnessed counted open each give a pass that is not deployment-grade.
- **Cost tests:** BGL's net series equals gross minus turnover times the cost minus the risk-free return. Doubling the cost can turn a pass into a G10 failure.
- **Replay tests:** matching position hashes pass; one differing period fails G12 and is listed; a replay whose input hashes differ from the live rows fails; weights differing only beyond the eighth decimal place give the same hash; a `live_return` row stamped later than the lag fails G11.
- **Live cost tests:** realised costs exactly twice the modelled costs pass; slightly more fails G13; zero modelled cost with non-zero realised cost fails.
- **Migration tests:** imported rows decide claims and use up the holdout, but add nothing to budget or N; `prior_trials` below the imported count is refused; the import is all or nothing.

**Packaging**

- Python 3.11 or later, built with uv, published to PyPI as `bgl`.
- **Dependencies:** `cryptography` (sealing and row signatures), `opentimestamps` (stamp proofs) and `numpy` (statistics). Each is named here with its reason, pinned with hashes in `uv.lock`, and checked by a dependency vulnerability audit in CI. Hash-chain verification needs only the standard library; signature and stamp checks need the pinned dependencies. `opentimestamps` is LGPL-3, which as far as I know is fine to depend on from an MIT project; check before release.
- The GUI may add dependencies as an optional extra.
- Semantic versioning. Every ledger row, manifest and series file carries a `format_version`, and any change to a format ships with a migration.

**Quality**

- Type hints throughout, checked in CI; a linter in CI.
- Documentation: a README with a quick start, a "Common refusals" section (starting with R3b), one page per command, the reviewer checklist, the custodian workflow, the supported git workflow, and this spec as the design rationale.
- An open-source licence (MIT suggested).

## Example project and dogfooding

BGL is designed and tested on its own terms. Real projects use it; they do not shape it.

**The example project** (`examples/` in the BGL repository) is a small, made-up research project that exercises every feature:

- a team-mode `bgl.toml` with two researchers' writers and a custodian, a study with a holdout and gate table, and a study with `prior_trials` and no holdout;
- a split, diagnostic grids, a sealed holdout run, a void, a sample import, witnessing against a fake Rekor log with fixed stamp proofs, incubation with a position replay and a cost check, and a full `bgl gate` run.

- **Synthetic data only.** Every input is generated from a fixed random seed. *Reason:* CI never touches licensed data, and every run of the example gives the same numbers on every machine.
- **It is the release gate.** The release is checked by running the example end to end in CI on Linux, macOS and Windows.
- **It holds no rules.** If the example needs a special case, that is a bug in BGL.

**Dogfooding.** Before release, BGL is used on at least one real project. Feedback flows one way only:

1. A problem found in a real project is written up as a general BGL issue, without the project's names, data or history.
2. The fix comes with an example-project test that fails before the fix and passes after it.
3. No rule, default, example or test in BGL names a user project or depends on one.

## Roadmap, build order and open decisions

| Version | Contents | Gate |
| --- | --- | --- |
| v0.1 | The whole library | Every R, C and G test passes on all three operating systems; the example project runs end to end in CI; one real dogfooding pass is complete |
| v0.2 | GUI | A reviewer completes the review checklist on the example project using only the GUI |

**Build order for v0.1** (each step with its tests, seen failing first):

1. **Package skeleton:** repository, `uv init --lib`, pinned dependencies, `.gitignore` covering `.bgl/`, CI on three operating systems with the dependency audit, licence, README stub.
2. **Config loader:** C1 to C20.
3. **Ledger:** canonical JSON, keys and signatures, the hash chain, torn-tail detection and `repair`, the ledger lock with `fsync` and its two-process test, chain verification. *First because everything writes to it.*
4. **Git helpers:** clean tree (R3), tracked script, untracked Python (R3b), commit, user, commit signature, code fingerprint, reachability.
5. **Audit-hook spike:** test whether `sys.addaudithook` sees pyarrow reads, and what the module scan sees. R8, R17 and R25's wording depends on the result, so it comes before the code that relies on it.
6. **Run lifecycle:** pre-open checks, the study lock, manifests, open and close rows, crash and interrupt handling, atomic writes, output capture, `run.returns`, `run.holdout`, dry runs.
7. **Writers and chains:** `join`, other heads, cross-chain reading, machine ID checks.
8. **Witness:** Rekor submission, inclusion-proof checks and listing by public key; OpenTimestamps submission, upgrade, and checks against a node or two explorers.
9. **Custodian:** `seal`, decryption for holdout runs, taint tracking.
10. **Command-line tool:** the lock test on three operating systems first, then every command.
11. **Statistics:** estimators, PSR, DSR, MinTRL, CSCV and PBO, the Lo adjustment, the bootstrap band, with fixed vectors.
12. **Gate:** stages 1 to 4 in order, the report, the `gate` row.
13. **Migrate.**
14. **Register** (optional OSF bundle).
15. **Example project:** end to end in CI on three operating systems, then the dogfooding pass. This is the release gate.

Adopting BGL in a real project is that project's own task, in its own repository. It is not a BGL build step.

**Decided (18 Nov 2026).** Everything in the decision batch at the top of this spec, plus these earlier decisions, which still stand:

| Decision | Outcome |
| --- | --- |
| Package name | BGL (Backtest Governance Library); package and import name `bgl` |
| Unclosed runs | Fail `verify`; cleared only by `abandon` with a written reason; a run whose lock is held here is in progress |
| Live runs | A lock file held by an OS lock, not a PID |
| Synced or network folders | Unsupported and documented; `init` warns |
| Effective number of trials | Raw N is primary; effective N later, as a labelled sensitivity check only |
| Stopping at a failed stage | The verdict is the first failure; stages 2 and 3 both run; stage 4 only after 1 to 3 pass |
| Config defaults | No key has a default |

**Still open, or to check before the step that needs them:**

| Item | Needed by |
| --- | --- |
| The audit hook's view of pyarrow reads and loaded modules | Step 5 |
| Whether Rekor's v2 API still lists entries by public key; whether it accepts Ed25519-signed hash entries; its rate limits. If listing is unavailable, use the fallback in Witness | Step 8 |
| The OpenTimestamps calendar API and the `opentimestamps` library's verification interface | Step 8 |
| The authenticated encryption construction for sealing (streamed, from `cryptography`) | Step 9 |
| The DSR paper's worked example values; the exact Lo (2002) form and lag truncation; the automatic block-length method | Step 11 |
| The licence compatibility of `opentimestamps` (LGPL-3) | Before release |

## References

- Arnott, R., Harvey, C. R., and Markowitz, H. (2019). A Backtesting Protocol in the Era of Machine Learning. *Journal of Financial Data Science*, 1(1), 64 to 74.
- Bailey, D. H., and López de Prado, M. (2012). The Sharpe Ratio Efficient Frontier. *Journal of Risk*, 15(2). Source of PSR and MinTRL. *Check page numbers before citing.*
- Bailey, D. H., and López de Prado, M. (2014). The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality. *Journal of Portfolio Management*, 40(5), 94 to 107. [SSRN](https://papers.ssrn.com/abstract=2460551)
- Bailey, D. H., Borwein, J., López de Prado, M., and Zhu, Q. J. (2017). The Probability of Backtest Overfitting. *Journal of Computational Finance*, 20(4), 39 to 69. Source of CSCV and PBO. Section 3.1 of the February 2015 revision describes rejecting models whose PBO exceeds 0.05 as customary.
- Harvey, C. R., and Liu, Y. (2015). Backtesting. *Journal of Portfolio Management*, 42(1). *From memory; check before citing.*
- Harvey, C. R., Liu, Y., and Zhu, H. (2016). ...and the Cross-Section of Expected Returns. *Review of Financial Studies*, 29(1). *Check page numbers before citing.*
- Lo, A. W. (2002). The Statistics of Sharpe Ratios. *Financial Analysts Journal*, 58(4). Source of the autocorrelation adjustment. *From memory; check before citing.*
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
- Perold, A. F. (1988). The Implementation Shortfall: Paper versus Reality. *Journal of Portfolio Management*, 14(3). Source of the live cost check (G13). *From memory; check before citing.*
- Patton, A., Politis, D. N., and White, H. (2009). Correction to "Automatic Block-Length Selection for the Dependent Bootstrap". *Econometric Reviews*, 28(4). *From memory; check before citing.*
- Politis, D. N., and Romano, J. P. (1994). The Stationary Bootstrap. *Journal of the American Statistical Association*, 89(428). *Check page numbers before citing.*
- Politis, D. N., and White, H. (2004). Automatic Block-Length Selection for the Dependent Bootstrap. *Econometric Reviews*, 23(1). *From memory; check before citing.*
- Python PEP 578, Python Runtime Audit Hooks. The `import` audit event fires before the module name is resolved to a file, and its arguments other than the module name may be `None`, which is why R17 scans loaded modules instead.
- OpenTimestamps: the Python client and the `opentimestamps` library.
- Sigstore Rekor: the public transparency log used for witnessing (rekor.sigstore.dev).
- Open Science Framework: osf.io.

## Appendix A: worked example, adoption in a student trading society

This appendix shows one real adoption. Nothing in it is a rule.

- **Context.** A university student trading society's algorithm programme runs research projects whose strategies may be traded with the society's fund.
- **Mode.** Fund projects use `team` mode. Personal projects may use `solo` mode, and their gate results are labelled not deployment-grade.
- **Custodian.** The head of the society's quant division is the custodian for fund projects. When the committee changes each year, the outgoing head hands over by a signed amendment before leaving. They receive the raw data where the licence allows, run the split, seal the holdout, run each study's one holdout test, and run incubation and the replay.
- **Reviewers.** The fund's decision makers work through the reviewer checklist before money is committed.
- **Return series backup.** Each researcher copies `returns/` to their university cloud storage, never the project folder itself.
- **Licensed data.** Each member holds their own data licence, so a custodian split is not always possible. Those studies report a researcher split.