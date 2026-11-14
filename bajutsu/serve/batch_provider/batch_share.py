"""What one batch job may share with the other jobs of its `run-set` group (BE-0484)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BatchShare:
    """The keys a provider shares a job's uploads under, from the job's lease (BE-0484).

    `compat_key` digests the config, the binary, the platform, and the target, so jobs that carry
    it can share the application upload. `group_id` names the `run-set` group; a package holds the
    files of one group alone, so it is shared within the group only.
    """

    group_id: str
    compat_key: str
