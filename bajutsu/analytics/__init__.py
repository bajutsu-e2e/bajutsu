"""Token/cost accounting, split by role (BE-0257).

`usage` is the process-global, in-memory token accumulator (reporting-only, best-effort, never
raises); `ledger` is the attributed, persistent AI usage/cost ledger (BE-0196), one JSONL line per
AI call; `stats` aggregates the ledger for the serve usage dashboard (BE-0195). No package-level
re-export: every caller already names a specific module (`bajutsu.analytics.usage`, …).
"""

from __future__ import annotations
