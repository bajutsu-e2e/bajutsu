"""AI / authoring-agent periphery, grouped by role (BE-0257).

`protocols` defines the `Agent`/`EnrichmentAgent` abstractions the record and enrich loops program
against; `factory` builds the one SDK-backed implementation behind them. `claude` is the Claude
authoring agent, `claude_backed` its shared base (BE-0246), `claude_enrich` the enrichment variant,
`claude_triage` the failure-diagnosis variant. `ai_config` resolves the provider/model/effort/
language knobs every Claude-backed agent shares; `anthropic_client` constructs the Anthropic SDK
client; `availability` turns a credential gap into an actionable message for `serve`/`doctor`.
`enrich` is the enrichment loop itself; `alerts` is the system-alert guard. All periphery — never on
the verdict path, prime directive #1. No package-level re-export: every caller already names a
specific module (`bajutsu.agents.claude`, `bajutsu.agents.ai_config`, …), the `crawl/guide.py` /
`github/actions.py` pattern of importing a periphery submodule directly rather than through the
package `__init__`.
"""

from __future__ import annotations
