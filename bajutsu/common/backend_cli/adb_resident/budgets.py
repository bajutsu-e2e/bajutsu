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

# The resident `/setPickerValue` walk's own two bounds (BE-0480), copied from the server's
# `MAX_PICKER_ROWS` / `PICKER_ROW_BUDGET_MS` because they size the timeout below;
# `tests/test_adb_resident_budgets.py` fails when the two ends drift apart.
MAX_PICKER_ROWS = 400
PICKER_ROW_BUDGET_MS = 1_500

# The socket timeout on `/setPickerValue`: the whole walk deadline, plus the two row waits the last
# step can spend past it, on top of what `/act` already spends. The server answers
# `picker-walk-exhausted` at its row cap or deadline first, so this firing means the channel failed,
# not that the row is missing.
PICKER_TIMEOUT_S = (MAX_PICKER_ROWS + 2) * PICKER_ROW_BUDGET_MS / 1000 + ACTION_TIMEOUT_S
