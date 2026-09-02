"""Common — the deterministic core and shared infrastructure behind bajutsu's feature packages.

Holds the packages several feature directories (`run/`, `crawl/`, `record/`, `triage/`, `serve/`,
`mcp/`, `codegen/`, `analysis/`) depend on but none of them own outright — the AI backend seam
(`ai`) and the authoring-agent periphery (`agents`), contract layers (`assertions`, `scenario`),
the batch/CI periphery (`analytics`, `cloud`, `github`), the evidence/report pair (`evidence`,
`report`), and config/capability/provisioning (`config`, `config_source`, `capability`,
`provisioning`). Still to move here: the rest of the deterministic pipeline (`drivers`,
`orchestrator`, `runner`, `platform_lifecycle`) — so membership here says nothing about whether a
package is deterministic core or periphery; the import-linter contracts in `pyproject.toml` remain
the boundary (BE-0112). Populated incrementally as each PR of the feature-first reorg lands,
following the merged BE-0257 layer-package-topology precedent; the sequence has no roadmap item of
its own yet. No package-level re-export: every caller already names a specific submodule.
"""

from __future__ import annotations
