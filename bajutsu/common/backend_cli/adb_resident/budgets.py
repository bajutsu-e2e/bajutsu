"""How long each resident-server request may take, on both ends of the channel.

The host's socket timeout and the device's waits have to agree, or a read the device is still working
on looks to the host like a dead channel. This module is the one place that relationship is written
down: the host sends `WAIT_BUDGET_MS` with every request that waits, and the server spends no more
than that. `tests/test_adb_resident_budgets.py` holds the server's own constants to these numbers.
"""

from __future__ import annotations

# The socket timeout on a read. Lengthening it would hide a slow device rather than bound it.
READ_TIMEOUT_S = 5.0

# What one request may spend waiting on the device: idle waits, the read-mark wait, and the waits
# inside each dump. What is left of `READ_TIMEOUT_S` carries the last dump and the reply back.
WAIT_BUDGET_MS = 3_500

# The socket timeout on `/act` and `/type`. Each spends `WAIT_BUDGET_MS` before it does anything; `/act`
# then waits for its gesture to publish and settles the tree it returns, and `/type` waits for focus.
ACTION_TIMEOUT_S = 10.0
