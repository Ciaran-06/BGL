"""Live test of BGL's witness code against the real Rekor and OpenTimestamps.

Part 1 (now):    uv run python spikes/witness/live_test.py
Part 2 (hours later, once Bitcoin has confirmed the stamp):
                 uv run python spikes/witness/live_test.py --upgrade

Part 1 logs two random heads in public Rekor with a throwaway key (never your
writer key), checks BGL's verification of Rekor's signed timestamp, inclusion
proof and checkpoint, checks the search-by-key discard check, and submits one
head to two OpenTimestamps calendars. Part 2 upgrades that stamp and checks it
against two block explorers. The Rekor entries are public and permanent; they
hold only the throwaway key and random hashes.
"""

import argparse
import os
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bgl import rekor, stamps, witness
from bgl.config import Project, Witness, Writer
from bgl.errors import WitnessError

REKOR = "https://rekor.sigstore.dev"
CALENDARS = (
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
)
SAVED = Path(__file__).with_name("pending.ots")
SAVED_HEAD = Path(__file__).with_name("pending.head")


def check(label: str, action: object) -> bool:
    try:
        result = action() if callable(action) else action
    except WitnessError as error:
        print(f"  FAIL  {label}: {error}")
        return False
    print(f"  ok    {label}" + (f": {result}" if result not in (None, True) else ""))
    return True


def part_one() -> int:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    kept, thrown = os.urandom(32).hex(), os.urandom(32).hex()
    print(f"Rekor {REKOR}")
    results = []
    record: dict[str, object] = {}

    def log_kept() -> str:
        record.update(rekor.submit(REKOR, key, kept))
        return f"uuid {str(record['uuid'])[:24]}..."

    results.append(check("submit a head", log_kept))
    if record:
        results.append(
            check(
                "BGL's checks pass on the returned record (signed timestamp, "
                "inclusion proof, checkpoint, SSH signature)",
                lambda: rekor.check_record(record, public) and None,
            )
        )
    results.append(
        check("submit a second head", lambda: rekor.submit(REKOR, key, thrown) and None)
    )
    writer = Writer(code="TT1", person="live test", public_key=public)
    project = Project(
        name="live test",
        mode="solo",
        ledger="bgl/ledger",
        runs_dir="bgl/runs",
        writers={"TT1": writer},
        witness=Witness(REKOR, CALENDARS),
    )
    report = None
    for attempt in range(6):
        try:
            report = witness.discard_check(project, writer, [kept])
        except WitnessError as error:
            print(f"  ...   search not ready ({error}); waiting")
            report = None
        if report is not None and report.entries >= 2:
            break
        time.sleep(5 * (attempt + 1))
    if report is None or report.entries < 2:
        print("  FAIL  search by key did not return both entries")
        results.append(False)
    else:
        ok = report.matched == 1 and len(report.unmatched) == 1 and not report.invalid
        print(
            f"  {'ok  ' if ok else 'FAIL'}  discard check: {report.entries} entries, "
            f"{report.matched} in the chain, {len(report.unmatched)} thrown away "
            "(expected 2, 1, 1)"
        )
        results.append(ok)

    print("OpenTimestamps")

    def stamp_it() -> str:
        proof, failed = stamps.stamp(kept, CALENDARS)
        SAVED.write_bytes(proof)
        SAVED_HEAD.write_text(kept)
        state = stamps.state(proof, kept)
        return (
            f"pending at {len(state.pending)} calendar(s); failed: {failed or 'none'}"
        )

    results.append(check("stamp a head", stamp_it))
    print()
    if all(results):
        print("RESULT: part 1 passed. Run part 2 with --upgrade in a few hours.")
        return 0
    print("RESULT: part 1 had failures (see above). Paste this output.")
    return 1


def part_two() -> int:
    if not SAVED.exists():
        print("No saved stamp; run part 1 first.")
        return 1
    head = SAVED_HEAD.read_text().strip()
    proof, changed = stamps.upgrade(SAVED.read_bytes(), head, CALENDARS)
    SAVED.write_bytes(proof)
    state = stamps.state(proof, head)
    print(
        f"upgraded: {changed}; Bitcoin heights claimed: {state.heights or 'none yet'}"
    )
    if not state.heights:
        print("RESULT: not confirmed yet. Try again in an hour or two.")
        return 1
    try:
        found = stamps.confirm(
            proof, head, stamps.explorer_source(stamps.DEFAULT_EXPLORERS)
        )
    except WitnessError as error:
        print(f"  FAIL  {error}")
        return 1
    first = found[0]
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(first.block_time))
    print(
        f"  ok    confirmed in Bitcoin block {first.height} ({when}), checked with "
        f"{len(stamps.DEFAULT_EXPLORERS)} explorers"
    )
    print("RESULT: part 2 passed.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upgrade", action="store_true")
    sys.exit(part_two() if parser.parse_args().upgrade else part_one())
