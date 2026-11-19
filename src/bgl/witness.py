"""Witnessing (R23): Rekor and OpenTimestamps. Built in step 8.

Until then submit_head does nothing, so every open is unwitnessed. That is the
correct record: witness status is worked out from proofs, never written.
"""

from pathlib import Path

from bgl.config import Project
from bgl.writers import Identity


def submit_head(root: Path, project: Project, identity: Identity) -> None:
    """Submit the writer's new head to Rekor and the OpenTimestamps calendars.

    A project with witness = false submits nothing. Network failures must not
    stop a run, so the caller ignores any error raised here.
    """
    if project.witness is None:
        return
    # Build step 8: Rekor entry, OpenTimestamps stamp, saved proofs.
