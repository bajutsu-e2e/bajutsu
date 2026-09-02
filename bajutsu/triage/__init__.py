"""The triage feature: `heuristic.py` is the M4 self-heal engine (`HeuristicTriageAgent` +
structured fixes); `cli.py` is the `bajutsu triage` command wired onto it. No package-level
re-export — every caller already names a specific module (`bajutsu.triage.heuristic`, …).
"""

from __future__ import annotations
