# bgl: Backtest Governance Library

**Status: early development (v0.1 in progress). Not usable yet.**

BGL applies the research discipline a fund imposes on an alpha researcher to every backtest run in a Python project. Each run is:

- logged, with its prediction written before it starts and its result after it ends;
- counted against a budget;
- signed by the person and machine that ran it;
- witnessed outside the researcher's control.

A deployment gate then corrects the reported result for the number of variants tried, and tests it on a holdout the researcher cannot open alone.

## Quick start (planned for v0.1)

```bash
pip install bgl          # or: uv add bgl
bgl init                 # writes bgl.toml, your writer key, an empty chain and .gitignore entries
bgl join CODE PERSON     # sets up each further person or machine
```

```python
from bgl import start_run

with start_run(
    study="alpha",
    run_type="diagnostic",
    script=__file__,
    what_changed="Lookback grid 10 to 60 days",
    prediction="Sharpe rises with lookback up to about 40 days, then flattens",
    inputs=["data/design/alpha_design.parquet"],
    sample="design",
    variants=6,
) as run:
    gross, turnover = run_grid(...)  # your research code
    run.returns(gross=gross, turnover=turnover)
    run.result("Best lookback 40; flat above it")
```

## Key ideas

- **Budget and N.** Budget limits how many counted runs a study may open. N counts every strategy variant tried, and the deflated Sharpe ratio corrects the result for it.
- **Study lock.** A study's settings are locked at its first counted run. Later changes are recorded amendments that can only weaken the evidence, never strengthen it.
- **Holdout custodian.** In team projects, a person who is not a researcher on the study seals the holdout data and runs the one holdout test.
- **Witness.** Every ledger head is submitted to a public transparency log (Sigstore Rekor) and stamped with OpenTimestamps, so discarded or rewritten runs are detectable.

## Common refusals

- **`Refused (R3b): untracked Python files`**: new `.py` files must be committed, or moved to `scratch/`, which git ignores.
- **`Refused (R19)`**: this machine has no registered writer. Run `bgl join`, then commit the registry entry it prints.
- **`Refused (R22)`**: the study's config differs from its lock. Undo the change, or record it with `bgl amend` if the change is allowed.

## What it does not do

- It does not run backtests or compute strategy returns.
- It does not store your data, only hashes of input files. Return series, captured output and sealed holdout files stay on your machine, git-ignored.
- It cannot stop a determined cheat. It makes dishonest editing detectable.
- It cannot see work done outside runs, so N is a lower bound.

## Limits

- Times in the ledger are self-reported. A stamp proves a head existed by a Bitcoin block's time.
- A run opened offline is unwitnessed, and a study containing one is not deployment-grade.
- Files read by C extensions (such as pyarrow) may not be seen by the read check.
- Locks are unreliable on network drives and in synced folders (OneDrive, Dropbox, iCloud Drive, Google Drive). Keep projects out of them. `bgl init` warns if you don't.
- A live run can only be checked on the machine where it runs.
- Witness entries in Rekor are public. They contain only hashes and public keys, but anyone can see when and how often each writer opens runs.

## Roadmap

| Version | Contents |
| --- | --- |
| v0.1 | The whole library: runs, ledger, writers, witness, custodian sealing, statistics, the deployment gate, import |
| v0.2 | A read-only local GUI |

## Design

The design spec is the source of truth: [docs/SPEC.md](docs/SPEC.md). Decisions, open items and the reasons behind each rule are recorded there.

## Development

```bash
uv sync                        # install dependencies
uv run ruff format .           # format
uv run ruff check --fix .      # lint
uv run mypy src                # type check (strict)
uv run pytest                  # tests
```

Optional Allure report: `uv run python scripts/dev_tests.py`. It needs the separate `allure` command-line tool, which runs on Java.

## Licence

MIT. See `LICENSE`.