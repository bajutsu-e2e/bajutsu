"""Shared foundation used by more than one feature directory (roadmap reorg, BE-0257 successor).

Today this package holds the AI backend seam (`ai`), the authoring-agent periphery (`agents`), and
the evidence/report pair (`evidence`, `report`). The rest of the reorg moves the remaining
deterministic core packages (`drivers`, `orchestrator`, `runner`, `assertions`, `config`,
`scenario`, `platform_lifecycle`) and the remaining periphery (`analytics`, `cloud`, `github`) here
too, rather than under a feature directory (`run/`, `crawl/`, `record/`, `triage/`, `serve/`,
`mcp/`, `codegen/`, `analysis/`), because each is used by more than one of them. No package-level
re-export: every caller already names a specific submodule.
"""

from __future__ import annotations
