"""Fields every BGL-written row shares: when, where and by which machine."""

import datetime
import os
import socket
from typing import Any

from bgl.writers import Identity


def utc_now() -> str:
    """The current UTC time as ISO 8601 text ending in Z, to the second.

    [DECISION] Row timestamps have one-second precision. They are self-reported
    (the witness gives real timing), so finer precision would add nothing.
    """
    now = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def base_fields(identity: Identity) -> dict[str, Any]:
    """machine_id, host and pid for a row written now by this writer."""
    return {
        "machine_id": identity.machine_id,
        "host": socket.gethostname(),
        "pid": os.getpid(),
    }
