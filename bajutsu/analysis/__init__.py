"""Read-only advisory analysis, split by role (BE-0257).

`coverage` maps a scenario suite's declared id namespaces; `audit` is the static determinism/
flakiness score; `stats` aggregates run history into the run-stats dashboard. None gates CI — a
missing/unreadable input is the only thing that exits non-zero. No package-level re-export: every
caller already names a specific module (`bajutsu.analysis.coverage`, …).
"""

from __future__ import annotations
