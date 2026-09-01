"""Common — the deterministic core and shared infrastructure behind bajutsu's feature packages.

Holds the packages several feature directories (`run/`, `crawl/`, `record/`, `triage/`, `serve/`,
`mcp/`, `codegen/`, `analysis/`) depend on but none of them own outright — contract layers
(`assertions`, `scenario`), the deterministic pipeline (`drivers`, `orchestrator`, `runner`,
`evidence`, `report`, `config`), and shared infrastructure spanning both layers — so membership here
says nothing about whether a package is deterministic core or periphery; the import-linter contracts
in `pyproject.toml` remain the boundary (BE-0112). Populated incrementally as each PR of the
feature-first reorg lands, following the merged BE-0257 layer-package-topology precedent; the
sequence has no roadmap item of its own yet.
"""

from __future__ import annotations
