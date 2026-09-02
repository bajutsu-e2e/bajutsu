"""The record feature: `loop.py` is the record loop (observe → propose → execute → emit);
`cli.py` is the `bajutsu record` command wired onto it. No package-level re-export — every
caller already names a specific module (`bajutsu.record.loop`, …).
"""

from __future__ import annotations
