"""Shared foundation used by more than one feature directory (roadmap reorg, BE-0257 successor).

Deterministic core packages (`drivers`, `evidence`, `orchestrator`, `runner`, `assertions`,
`config`, `scenario`, `report`, `platform_lifecycle`) and AI/periphery infrastructure (`ai`,
`agents`, `analytics`, `cloud`, `github`) live here rather than under a feature directory
(`run/`, `crawl/`, `record/`, `triage/`, `serve/`, `mcp/`, `codegen/`, `analysis/`) because each is
used by more than one of them. No package-level re-export: every caller already names a specific
submodule.
"""

from __future__ import annotations
