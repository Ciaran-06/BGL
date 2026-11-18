# bgl: Backtest Governance Library

**Status: early development (v0.1 in progress). Not usable yet.**

BGL makes every backtest run in a project logged, budgeted and tamper-evident. Each run writes its prediction before it starts and its result after it ends. The ledger is a hash chain, so edited or deleted rows are detectable.

## Quick start (planned for v0.1)

```bash
pip install bgl          # or: uv add bgl
bgl init                 # writes bgl.toml and an empty ledger
```

```python
from bgl import start_run

with start_run(study="equities", run_type="diagnostic", script=__file__,
               what_changed="...", prediction="...", inputs=[...]) as run:
    ...
    run.result("...")
```

## What it does not do

- It does not run backtests or compute returns.
- It does not store data files, only their hashes.
- It cannot stop someone deleting a folder. It makes dishonest editing detectable.

## Limits in v0.1

- Open and close times are self-reported until v0.3.
- Deleting trailing ledger rows is detectable only if the head hash from `bgl status` was recorded elsewhere.
- Locks are unreliable on network drives and in synced folders (OneDrive, Dropbox, iCloud Drive, Google Drive). Keep projects out of them. `bgl init` warns if you don't.
- A live run can only be checked on the machine where it runs.

## Design

The design spec is the source of truth: [link to the spec doc]. Decisions and open questions are in its tables.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy src
```

## Licence

MIT. See `LICENSE`.