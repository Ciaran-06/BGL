# BGL build plan: steps 8a and 9 to 15

Written 19 Nov 2026 from the audit of steps 1 to 8 and decision batch N10 to N61. For Ciaran, and for every session that builds or reviews a step.

`docs/SPEC.md` stays the source of truth. The decisions below are approved, but their spec text is not written yet (step 0b). Until a decision's spec text is approved, code that relies on it carries a `[DECISION]` tag naming its N number.

Every step ends with the definition of done in `CLAUDE.md`: ruff format and check, strict mypy, pytest, each rule's test seen failing first, deliberate breaks for decision logic, CI on Linux, macOS and Windows with Python 3.11 and 3.14, and an independent review by a fresh session.

## 1. Decisions N10 to N61

| N | Decision | Steps |
| --- | --- | --- |
| N10 | **Witnessed at open.** The close file records the Rekor entry the R23 call returned for the open head: UUID, log index, integrated time, log ID and signed entry timestamp. It is null when that call failed. Only that call's result is recorded, never an entry obtained later in the run. An open is witnessed at open only when this record exists, the log's entry for the open head matches it (N11), and a stamp of the open head verifies against Bitcoin. The close row binds the record through `close_sha256`. | 8a, 12 |
| N60 | A counted run with no close file (hard-killed, then abandoned) is witnessed at open if the live log holds an entry for its open head made with the writer's key. Such a run has no result or series, so a late entry gains nothing. | 8a, 12 |
| N11 | At the gate a head counts as logged only if the live log at the locked `rekor_url` returns its entry. Stored records are an offline convenience, and `status` labels them "not checked against the log". | 8a, 12 |
| N12 | Accept the dependence on Rekor v1 and state it in the spec's limits. Run the live test now. Revisit when Sigstore names an end date for v1. | 0, 0b |
| N13 | An amendment applies where its recorded `old` equals the value then in force. One that never fits is never applied. `verify` fails it until an `ack` row names it (N61). The gate lists every such amendment, acknowledged or not. | 8a, 10, 12 |
| N61 | `bgl ack ROW_HASH "reason"` acknowledges an amendment. The ack row has `id` null, the amendment's `study`, and `{"row": hash}` in its `amend` field. `bgl ack RUN_ID "reason"` still acknowledges a late close. | 8a, 10 |
| N14 | Writers may be added after a lock without an amend row, except that a new writer whose `person` is a study's custodian needs an amend row signed by an existing writer of that custodian. The gate lists writers added after the lock. | 9, 12 |
| N15 | `bgl amend STUDY KEY "reason" [--fix COMMIT]`. The new value is read from `bgl.toml`. | 10 |
| N16 | `selects` must name a diagnostic run of the study that closed `ok` and whose series holds that variant. Exploratory runs may store series, and those series enter $\hat V$ and PBO. | 10, 11, 12 |
| N17 | Output capture stays at the Python stream level, as a declared limit. A process start seen by the hook after the holdout load counts as exposure. A logging handler holding the original stream at open gives a warning. | 9, 10 |
| N18 | R3 refuses a run when `bgl.toml` is not tracked. | 8a |
| N19 | Spec wording only: signature codes include `X`, `Y`, `R`, `E`; the lock row holds `mode` and the witness settings; `init CODE PERSON`; `run.input(path)`; R19 and R7 refusals write no row; the spike document's path; the hook's sqlite event; a stamp covers the witness message, not the bare head. | 0b |
| N20 | `pandas` joins the dev dependency group, never the runtime ones. CI runs `pip-audit` on `uv export`. | 8a |
| N21 | **Sealing.** One AES-256-GCM call per file. One 32-byte seal key per study, in the custodian's home folder. A random 96-bit nonce per file. Associated data is the header: format version, study, path and plaintext length. A file over 1 GiB is refused with an error naming the cap. Peak memory is about twice the file. | 9 |
| N22 | With a custodian declared, the holdout run is refused without a current seal row. Deployment-grade also needs the seal row to come before the study's first counted design open. | 9, 12 |
| N23 | A holdout run declares the holdout path. With a seal row, R8 checks `<path>.sealed` against the seal row's sealed hash, the manifest records both hashes, and R4 uses the plaintext hash. | 9 |
| N24 | A plaintext holdout file present after a seal row fails `verify` and warns in `status`. | 9 |
| N25 | `bgl seal STUDY --reseal --from-key FILE` decrypts with the outgoing custodian's key file, checks the plaintext hashes against the original seal row, and encrypts under the new key. No plaintext reaches the disk. | 9 |
| N26 | Taint by hash. The hook also guards the paths of tainted outputs, and the outputs of any run the hook saw open a holdout or tainted file are tainted. | 9 |
| N27 | Only a writer of the person who ran a run may void it. | 10 |
| N28 | Void's fix commit must change the code fingerprint (N8). | 10 |
| N29 | A hard-killed confirmatory run may be voided only in a sealed study, and only when it has no load marker. `run.holdout` writes the marker `<runs_dir>/<ID>.load.json` before it decrypts anything. The missing marker is attested only by the custodian. When the custodian's person is also a researcher on the study (a writer of that person opened a design run of the study, as in a solo project whose researcher is its own custodian), a holdout run rescued this way rests on self-attestation, and the gate report says so beside the verdict. | 9, 10, 12 |
| N30 | `bgl live STUDY --from FILE` reads JSON Lines, one object per period: `period`, `value` (gross return), `turnover`, `cost`, `position_sha256`, `commit`, `inputs` (path and hash). Decimals are text. Only custodian writers may append when a custodian is declared. It runs once per period. | 10 |
| N31 | G11 and G12 compare code fingerprints, not commits. Committing the ledger moves HEAD, so commit equality could never hold. | 10, 12 |
| N32 | `bgl incubate` needs the study's confirmatory run closed `ok`, and a custodian writer when a custodian is declared. A new incubation row ends the previous one. | 10 |
| N33 | `list` sorts by `started` then ID, with fixed CSV columns. `show` prints the rows, manifest and close file. `stats dsr RUN_ID --variant K` requires K when the run has more than one variant. `abandon` refuses a run that has a close row. | 10, 12 |
| N34 | $\hat V$ uses divisor $K-1$ over the $K$ variants with a series. A series with $s=0$ has no Sharpe ratio. As the selected variant it fails G6 as not computable; any other such variant leaves $\hat V$ and PBO and is listed. | 11 |
| N35 | PBO drops the oldest $T \bmod S$ rows. Rank 1 is the worst out-of-sample Sharpe. Tied ranks take the lowest rank. A tie for in-sample best takes the tied variant with the lowest out-of-sample rank. | 11 |
| N36 | PBO's common range is the selected variant's periods. A variant missing any of them is excluded and listed, and stays in N. | 11 |
| N37 | C10: `cscv_blocks` is at most 20. | 11 |
| N38 | The risk-free file is CSV `period,risk_free`, decimal text, per-period returns. A missing period makes the statistic not computable, never zero. Each open manifest records the file's hash; the gate lists the distinct hashes and reports a change, without a verdict. | 11, 12 |
| N39 | Close-file statistics with no gate table are computed with $c=0$ and record `cost_bps: null`. All close-file statistics come from the series file's rounded values, so `verify` can recompute them. | 11 |
| N40 | Lo (2002) uses all $q-1$ lags, with $q$ = `periods_per_year`. It is not computable when $T \le q$ or the term under the root is not positive. Reported only. | 11 |
| N41 | The bootstrap seed is the first 8 bytes of the lock row's hash. Uniform draws come from PCG64's raw output. Quantiles use linear interpolation. Block length is checked against the 2009 correction's Table 1 and property tests, with no outside package. | 11 |
| N42 | Each decision statistic is rounded to 12 significant digits; that text is stored, and that value is compared. G13 uses exact fractions from the decimal texts. | 11, 12 |
| N43 | With a holdout, the gate judges the one holdout run that is not voided. Without one, every confirmatory run that is not voided must pass G6. The judged run must have closed `ok`. Outcomes are printed, labelled declared, with no verdict. | 12 |
| N44 | G1: a counted open is after the lock if a lock row is earlier in its chain, or at or before a head it recorded for another chain. Two lock rows with different content fail G1. The gate re-checks every amendment against the typed rules. | 12 |
| N45 | A design run closed `invalid` for opening a holdout or restricted file fails G4. | 12 |
| N46 | G9: each manifest must hold the cost model in force when its run opened, meaning the lock plus amendments up to then. | 12 |
| N47 | Under a corrected gate table, the gate row's verdict and exit code follow the original values. The amended result sits beside them. | 12 |
| N48 | G11: a row's witness time is the earliest verified block time among stamped heads at or after it. Lag is the block's UTC date minus the period's date. The live rows and the replay cover the same periods, and the union of `live_inputs` equals the replay's declared inputs. Incubation length runs from the incubation row's block time to the last live period. | 12 |
| N49 | Position hash: each weight as `Decimal(repr(w))`, rounded half-even to 8 places. Weights that are zero after rounding are dropped. SHA-256 of the canonical JSON of `[[asset, "0.12345678"], ...]` sorted by asset. | 12 |
| N50 | The gate report is canonical JSON with numbers as text, at `<runs_dir>/gate/<STUDY>-<UTC time>-<CODE>.json`, committed. The gate row holds `study`, `id` (the judged run or null), `result` (the verdict text) and `report_sha256`. Without a writer identity the gate prints a report labelled "not recorded" and writes nothing. | 12 |
| N51 | Without network, G2 stays at ledger-ordered and G11 fails as not checkable. | 12 |
| N52 | G14: live net excess is `value` minus `cost` minus $f_t$. MinTRL is computed against 0 at `psr_threshold`. | 12 |
| N53 | The import file is CSV with header `id,study,type,counts,claims,sample,what_changed,started,ended,status,result`. Claims are separated by `;`. | 13 |
| N54 | Row field `counts` (true, false or null) joins format version 1, making 56 fields. | 13 |
| N55 | Import validates the whole file, then appends every row under one ledger lock. A rerun skips identical rows and refuses any difference. | 13 |
| N56 | Imported confirmatory and holdout rows bind under R6 and R20 and cannot be voided. `prior_trials` must cover the imported non-data rows, checked at import and at the gate. Import after the lock is allowed. | 13 |
| N57 | `register` builds a deterministic zip under `<runs_dir>/registration/` and appends a `registration` row with its hash in `report_sha256`. Upload to OSF is by hand. | 14 |
| N58 | The example's driver patches `bgl.netio.resolve` in-process and switches `BGL_HOME` and `.bgl/writer` for each person. The fakes move to `devtools/`. | 15 |
| N59 | The example uses the fake calendar. One real completed proof becomes a unit-test vector. | 0, 15 |

**Readings to confirm when the spec text is reviewed:**

- N13: while a non-fitting amendment is unacknowledged, G1 fails, as an unacknowledged late close fails G3.

## 2. Spec text to draft (step 0b)

| Spec section | Decisions and findings |
| --- | --- |
| Witness | N10, N11, N12 (limits), N60 |
| Close file | `open_witness` (N10); stream byte lengths (F9); the load marker (N29) |
| Study lock and amendments; custodian handover | N13, N14, N15, N25, N61 |
| Holdout, custodian and sealing | N21 (closes the open item), N22, N23, N24, N26 |
| Functional requirements | R3 (N18); R6 (N16, N27, N28, N29); R8 and R25 (sqlite, N19); R10 (failure after the open row, F3) |
| File formats | `counts` (N54); `amend` on ack rows (N61); risk-free file (N38); import file (N53); live file (N30); gate report (N50) |
| Config rules | C2: `ledger` and `runs_dir` may not overlap (F15); C10: `cscv_blocks` at most 20 (N37) |
| Command-line tool | N15, N25, N30, N33, N61; `init CODE PERSON` (N19) |
| Statistics | N34 to N36, N38 to N42; PBO's $\lambda \le 0$ marked as BGL's choice |
| Deployment gate | N31, N43 to N48, N51, N52; position hash (N49); the self-attestation label (N29) |
| Packaging | pandas in the dev group, `pip-audit` (N20) |
| Register; example project | N57; N58, N59 |
| Wording | N19's list |

## 3. Statistics: source and check for every formula

| Quantity | Source | Check before it is trusted |
| --- | --- | --- |
| $r_t = g_t - \tau_t c - f_t$ | BGL (decision 5) | Hand case; doubling $c$. |
| $\widehat{SR}=\bar r/s$, divisor $T-1$; $\hat\gamma_3$, $\hat\gamma_4$, divisor $T$, kurtosis not excess | Decision D | A five-point series worked in exact fractions in the test. |
| $\mathrm{PSR}(SR^*)$ | Bailey and López de Prado (2012), equation number unconfirmed; restated in the 2014 paper's eq. (2) | The DSR vectors below. |
| $E[\max_N] \approx (1-\gamma)\Phi^{-1}(1-\tfrac1N)+\gamma\Phi^{-1}(1-\tfrac1{Ne})$ | Bailey and López de Prado (2014), eq. (1), appendix eqs. (5) and (6) | Against the exact $\int x\,N\varphi(x)\Phi(x)^{N-1}\,dx$ by quadrature for $N \in \{2,3,5,10,100,10^3,10^6\}$, tolerance 0.05. |
| $SR^*$ and DSR | Bailey and López de Prado (2014), eq. (2) | The paper's example: $N=100$, $V=\tfrac12$ annualised, $T=1250$, $\hat\gamma_3=-3$, $\hat\gamma_4=10$, Sharpe 2.5 annualised at 250 periods a year. Published: $SR^*=0.1132$ per period, DSR $=0.9004$; $N=46$ gives 0.9505; normal returns with $N=88$ give about 0.95. |
| $V=\max(\hat V, \tfrac1{T-1})$ | BGL (decision 17) | Hand case; property test with identical variants. |
| MinTRL | Bailey and López de Prado (2012), equation number unconfirmed | Hand case; on a grid, $T \ge \mathrm{MinTRL}$ exactly when $\mathrm{PSR}(SR^*) \ge$ the threshold. |
| CSCV and PBO; $\bar\omega = \bar r/(N+1)$, $\lambda=\ln\frac{\bar\omega}{1-\bar\omega}$ | Bailey, Borwein, López de Prado and Zhu (2017), Algorithm 2.3 steps (f) and (g), eq. (2.2), section 3.1 | A hand-computed $S=4$ matrix (6 splits) with the working in the test; a second, loop-only implementation on random matrices. |
| Performance degradation; probability of loss | Same paper, section 3.2 | The same $S=4$ matrix. |
| $\hat\eta(q)=q\big/\sqrt{q+2\sum_{k=1}^{q-1}(q-k)\hat\rho_k}$ | Lo (2002), equation number unconfirmed | $\rho_k=0$ gives $\sqrt q$; $q=2$, $\rho_1=\tfrac12$ gives $\sqrt{4/3}$; the AR(1) closed form; one value from Lo's table once the paper is read. |
| Stationary bootstrap | Politis and Romano (1994) | Mean block length; $p=1$ is the ordinary bootstrap; wrap-around; same seed, same band. |
| $b=(2G^2/D_{SB})^{1/3}N^{1/3}$, $D_{SB}=2g^2(0)$ | Politis and White (2004), eqs. (6) to (9), corrected by Patton, Politis and White (2009), items 1 and 4 | The 2009 Table 1 for AR(1): $\rho=0.7$ gives 11.47 ($N=200$) and 18.20 ($N=800$); $\rho=0.1$ gives 2.01 and 3.20; $\rho=-0.4$ gives 5.66 and 8.99. |
| $\sum\kappa_t \le 2\sum\tau_t c$ | BGL (decision r), after Perold (1988) | Exact fractions at the boundary and either side. |
| $\Phi$, $\Phi^{-1}$ | `statistics.NormalDist` | $\Phi^{-1}(0.99)=2.326348$; $\Phi(1.959964)=0.975$. |

## 4. Order and parallel work

| Step | Kind | Main files | Can run alongside |
| --- | --- | --- | --- |
| 0. Prove the unproven | check | none | all |
| 0b. Spec text | spec | `docs/SPEC.md`, after Ciaran approves the text | 8a, 11 |
| 8a. Audit fixes | core | `ledger.py`, `run.py`, `checks.py`, `audit.py`, `witness.py`, `studylock.py`, `verify.py`, `git.py`, `cli.py`, CI | goes before 9, 10, 12, 13 |
| 9. Custodian and sealing | core | new `seal.py`, `custody.py`, `taint.py`; `run.py`, `checks.py`, `studylock.py`, `verify.py`, `status.py`, `home.py` | 11, 14 |
| 10. Commands | standard; `void`, `abandon`, `ack` core | new `corrections.py`, `listing.py`, `incubation.py`; `cli.py`, `verify.py` | 11, 13, 14, after 9 |
| 11. Statistics | core | new `stats/` package; `series.py`; `pyproject.toml`, `uv.lock` | 9, 10, 13, 14 |
| 12. Gate | core | new `gate/` package, `positions.py`; `run.py`, `witness.py` | none; needs 9, 10, 11, 13 |
| 13. Migrate | standard; binding rules core | new `migrate.py`; `checks.py`, `records.py`, `ledger.py` | 10, 11, 14, after 9 |
| 14. Register | standard | new `register.py` | 9, 10, 11, 13 |
| 15. Example project | standard; the release gate | `examples/`, `devtools/`, one test, CI | none; last |

Step 8a turns `cli.py` into a command table, so later steps each add one entry and do not collide. Steps 9 and 13 both change R6 and R20 in `checks.py`, so 13 follows 9. Step 11 is pure functions in new files and can start at once.

## 5. Steps

### Step 0. Prove the unproven

- Run the suite and CI on Windows.
- Run `spikes/witness/live_test.py` parts 1 and 2. Record the result, Rekor's rate limits and the search behaviour in a new `docs/rekor.md`. If the live test fails, stop: step 12 cannot be built on a witness that does not work.
- Run the audit-hook spike on Windows and macOS and add the results to `docs/audit_hook.md`.
- Save one real Rekor entry and one real completed `.ots` proof, with its block's Merkle root, as fixed test vectors (N59).

### Step 0b. Spec text

Draft the exact spec text for section 2 as one batch. Ciaran approves it, then the spec is edited. Code may start before then, tagged with its N number.

### Step 8a. Audit fixes (core)

Fixes, from the audit:

- **F1.** `ledger.append_row_unlocked` and `repair_chain` verify the chain with the candidate line before writing. BGL never writes a row `verify` would reject, such as one from a second machine.
- **F3.** `run.Run._open`: any failure after the open row, Ctrl+C included, writes a close row (`interrupted` or `crashed`) and re-raises.
- **N10, N60.** `witness.submit_current_head` returns the record from the R23 call; `after_append` returns it or None and still never raises `Exception`. `Run._open` keeps it, and `_close` writes it as `open_witness`. `witness.unwitnessed_opens` uses `open_witness`. A run with no close file is pending while in progress; otherwise it counts if a stored record exists for its open head, labelled not checked against the log (N11).
- **N13, N61.** `studylock.effective_lock` applies by recorded `old`. `verify` fails a non-fitting amendment unless an ack row names its hash.
- **F8.** `audit._hook` handles `sqlite3.connect` like `open`.
- **F9.** The close file records each captured stream's byte length.
- **F12.** `verify`: malformed lock or amend content is a finding, not a traceback; a normal close outside the run's chain fails; a duplicate open ID fails.
- **F15.** C2: `ledger` and `runs_dir` may not be equal or nested.
- **F16.** `git.changed_paths` parses renames as `uncommitted_changes` does.
- **F17.** `check_r22` returns the git problem instead of hitting an assert.
- **F18.** An open's window ends at the first close with its ID in any chain.
- **N18.** R3: `bgl.toml` must be tracked.
- **F13, N20.** CI dependency audit; pandas in the dev group.
- **F14, F21.** Tags for the forced R19 and R7 behaviour and the small untagged choices. F20: fix the spike path and README.

Tests:

- **F1:** `amend` and `repair` from a second machine are refused and the file is unchanged; a row with no machine ID is refused.
- **F3:** Ctrl+C and an error raised in a patched witness call each leave one close row with the right status.
- **N10:** a witnessed open stays witnessed; an offline open stays unwitnessed after `bgl witness` submits the backlog (replaces the test that asserted the opposite); an edited `open_witness` fails `verify` through `close_sha256`.
- **N13:** two chains amending the same key in either registry order give the same effective lock; a non-fitting amendment fails `verify` until acknowledged.
- **F8:** a sqlite open is recorded, blocked in a dry run, and closes the run `invalid` on a guarded file.
- **Test debts:** Hypothesis property tests (any valid append sequence verifies, any single change fails); field and event names compared literally; an `ssh-keygen -Y sign` vector replaces the skipped test; the pandas test runs; a true late-close test (abandon, then normal close); the replay custodian rule; cross-chain R4, R6 and holdout-once tests; the canonical hash vector with a decimal stored as text.

*Review must check:* no code path writes a row without the pre-write check; the post-open guard can never write two close rows; `open_witness` comes only from the R23 call.

### Step 9. Custodian and sealing (core)

- `seal.py`: `seal_file`, `open_sealed`, header read and write (N21). `encrypt_into` and `decrypt_into` keep extra copies down.
- `home.py`: `create_seal_key`, `load_seal_key`.
- `custody.py`: `seal_study`, `reseal_study` (N25), `current_seal`, the three handover phases.
- `studylock.py`: the `custodian` amendment and who must sign it; a new writer under a custodian's person (N14).
- `taint.py`: `tainted_hashes`, `tainted_paths` (N26).
- `checks.py`: tainted inputs; seal required and current (N22); sealed inputs (N23).
- `run.Run.holdout`: write the load marker (N29); decrypt to `.bgl/tmp/<run id>/` with the hook paused; check the sealed and plaintext hashes before returning the path; remove only that run's folder at close.
- `verify.py`, `status.py`: plaintext present (N24); a reseal's plaintext hashes equal the original's; leftover temp files; the orphan scan knows the load marker.

*Tests prove:* round trip; a flipped byte, a truncated file, a wrong key and an edited header each fail to open; a file one byte over 1 GiB is refused with the cap in the message (the size check is tested without allocating 1 GiB); `seal` refuses a non-custodian, a study with no custodian, a missing file and a second seal; BGL never deletes plaintext; the holdout run is refused with no seal, a stale seal or a mismatched sealed file; temp plaintext is gone after an ok run and after a crash; a holdout run's output is tainted, and a design run declaring that content under another name is refused; split outputs before the lock are not tainted; the spec's five handover tests.

*Review must check:* the nonce is never reused under one key; the header is bound as associated data; the key never appears in a row, log or error message; no failure path leaves plaintext; a path is returned only after the whole file is authenticated; the spec's handover table line by line.

### Step 10. Remaining commands (standard; `void`, `abandon` and `ack` core)

- First, the lock test across processes on three systems: a child holds a run open and the parent's `verify` says in progress; kill the child and `verify` says unclosed.
- `corrections.py`: `add_note`; `ack` for a run ID or a row hash (N61); `abandon`; `void` (N27 to N29).
- `listing.py`: `list_runs`, `show_run` (N33). `incubation.py`: `incubate`, `add_live_returns` (N30 to N32).
- `cli.py`: `note`, `ack`, `abandon`, `void`, `amend` (N15), `list`, `show`, `incubate`, `live`.
- `checks.py`: "during incubation" for replay runs; `selects` per N16.

*Tests prove:* `abandon` is refused while the lock is held here, from another machine without `--force`, and for a closed run, and keeps budget and N; a later normal close is a late close until acknowledged; `ack` by row hash clears exactly that amendment and an unknown hash is refused; `void` is refused after output past the exposure point, with a written declared output, with no fix, a fix that does not descend from the run, an unchanged fingerprint, a second void on the same claims, or the wrong person; a good void frees exactly one rerun and the rerun cannot be voided; a hard-killed holdout run voids only without a load marker; a live file with one bad row appends nothing; exit codes.

*Review must check:* the exposure test against R6 word for word; no command can create a run; every append goes through `append_row`.

### Step 11. Statistics (core)

- `stats/normal.py` ($\Phi$, $\Phi^{-1}$); `stats/moments.py` (`net_excess`, `sharpe`, `skewness`, `kurtosis`); `stats/psr.py` (`psr`, `expected_max`, `dsr_hurdle`, `trial_variance`, `min_trl`); `stats/pbo.py` (`choose_blocks`, `pbo`); `stats/lo.py`; `stats/bootstrap.py` (`block_length`, `band`). All pure functions: arrays in, numbers out, no file or ledger access.
- `series.py`: `read_series`, the risk-free reader (N38), and the real close-file summary (N39). `numpy` joins the dependencies.

*Tests prove:* every row of section 3; DSR never rises with N; $N=1$ gives $\mathrm{PSR}(0)$; the floor applies to identical variants; PBO is unchanged by column order; N34 to N37 each at their edge.

*Deliberate breaks, each must fail a test:* $T$ for $T-1$ and the reverse; excess kurtosis; $\sqrt T$ for $\sqrt{T-1}$; the sign of the skew term; $e/N$ for $1/(Ne)$; rank direction; $<$ for $\le$; $N$ for $N+1$; the remainder rows.

*Review must check:* each formula against the source paper, equation by equation; each vector recomputed without the code under test; no hidden default.

### Step 12. Gate (core; needs 9, 10, 11 and 13)

- `gate/evidence.py`: reads chains, locks, amendments, manifests, close files, series and witness files; all network calls go through one injected object (N11, N60).
- `gate/stage1.py` (G1 to G5 and the N breakdown; N13, N14, N44, N45); `gate/stage2.py` (G6 to G8; N43); `gate/stage3.py` (G9, G10; N46); `gate/stage4.py` (G11 to G14; N31, N48, N52); `gate/verdict.py` (order, grade, every comparison; N42, N47); `gate/report.py` (N50; the N29 self-attestation label).
- `positions.py`: `position_hash` (N49). `run.Run.positions`. `cli.py`: `gate`, `stats dsr`.

*Tests prove:* one test per G rule on a fixed ledger in a temporary repository; every comparison at its threshold and one step either side; a stage 1 failure calls no statistics function; a stage 2 failure still runs stage 3 and the verdict stays at stage 2; the spec's four grade cases; doubling the cost turns a pass into a G10 failure; a chain rebuilt after witnessing fails G2 and `verify --contains`; an open whose `open_witness` is null, or differs from the log, is not witnessed; a hard-killed run counts under N60; one differing position fails G12 and is listed; weights that differ past the eighth place, or that round to zero, hash alike; a late stamp fails G11; costs at exactly twice the model pass and a hair above fail; the original verdict comes first under an amended table; the offline gate (N51); the gate row's hash matches the report; a hard-killed holdout run voided under N29 and then rerun carries the self-attestation label beside the verdict when the custodian's person also opened a design run of the study, and carries no label when the custodian is independent.

*Review must check:* every "inclusive" in the spec's comparison list; that no missing input can pass a rule; that stage 2 uses the N from G5; that every declared value is labelled; the report against the spec's six output rules.

### Step 13. Migrate (standard; the binding rules core)

- `migrate.py`: `parse_import`, `validate_import`, `import_rows` (N53, N55). `checks.py`, `records.py`: imported rows bind (N56). `ledger.py`: the `counts` field (N54).

*Tests prove:* an imported confirmatory row blocks its claim and an imported holdout row blocks the holdout; neither adds to budget or N; `prior_trials` below the imported count is refused; one bad row imports nothing; a changed study is refused; a rerun changes nothing.

*Review must check:* no import can lower N or free a claim.

### Step 14. Register (standard)

- `register.py`: `build_bundle` (fixed entry times, sorted names, no compression) and `register_study` (N57).

*Tests prove:* two builds give the same bytes; the bundle's hashes equal the lock's; the row holds the bundle's hash.

### Step 15. Example project (standard; the release gate; last)

- `examples/example_project/`: a team `bgl.toml`, a data generator using integer arithmetic so files are identical on every system, and scripts for the split, the grids, the holdout run, the live system and the replay.
- `examples/run_example.py`: the driver (N58). `devtools/fakes.py`. `tests/test_example.py` and a CI step.

*Tests prove:* the whole story runs on three systems with the same verdict and the same rounded figures; every command in the spec's table is used; nothing under `src/` names the example.

*Review must check:* the example needs no special case in BGL. Then the dogfooding pass on a real project.

## 6. Facts still unconfirmed

1. **Rekor v1.** Its maintenance status and v2's missing search index and `rekord` type come from Sigstore's v2 release post and the Rekor README. That v1 accepts BGL's entries, the index finds them, and the rate limits are for step 0.
2. **DSR example inputs.** The symbols were lost when the PDF's text was extracted. $N=100$, $V=\tfrac12$, $\hat\gamma_3=-3$, $\hat\gamma_4=10$ are from memory; they reproduce the three printed results. Check page 9 by eye.
3. **Equation numbers** for PSR and MinTRL (2012) and Lo (2002) are unconfirmed. Lo's formula is confirmed by secondary sources only.
4. **Politis and White estimator constants** ($c=2$, $K_N$, $M=2\hat m$, the cap) are from memory. The 2009 correction and its Table 1 are confirmed from the paper.
5. **The spec's "within about 0.05 for every N"** for the hurdle approximation: hand figures agree; the quadrature test settles it.
6. **The PBO paper prints 12,780 splits for $S=16$.** $\binom{16}{8}=12{,}870$, so it is a misprint.
7. **PBO at $\lambda=0$.** The spec's $\lambda \le 0$ is stricter than the paper; it is BGL's choice.
8. **AES-GCM per-call limit.** I believe `cryptography` refuses more than $2^{31}-1$ bytes in one call. 1 GiB is below that either way; step 9's tests confirm it on the pinned version.
9. **Bit-identical floats across systems** are not guaranteed; N42 exists for that.
10. **Windows.** No certain failure found by reading. One to watch: printing a non-ASCII person name to a cp1252 console in `bgl join`.

Sources: [Rekor v2 GA](https://blog.sigstore.dev/rekor-v2-ga/); [Rekor README](https://sources.debian.org/src/rekor/1.5.0-1/README.md); [Bailey and López de Prado, The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf); [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf); [Patton, Politis and White (2009)](https://public.econ.duke.edu/~ap172/Patton_Politis_White_2009.pdf).
