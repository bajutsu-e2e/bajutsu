"""Common — the deterministic core and shared infrastructure behind bajutsu's feature packages.

Holds the packages several feature directories (`run/`, `crawl/`, `record/`, `triage/`, `serve/`,
`mcp/`, `codegen/`, `analysis/`) depend on but none of them own outright — contract layers
(`assertions`, `scenario`), the deterministic pipeline (`drivers`, `orchestrator`, `runner`,
`evidence`, `report`, `config`), and shared AI/periphery infrastructure. Populated incrementally as
each feature-first reorg PR lands (see `roadmaps/` for the tracking item).
"""

from __future__ import annotations
