"""Evidence capture, split by role (BE-0257).

`core` is the instant/interval capture entry point (`Artifact`, `EvidenceSink`, `capture`, …) and is
re-exported here so `from bajutsu.evidence import Artifact` is unchanged after the flat `evidence.py`
became this package. `intervals` runs the video/deviceLog interval capture as simctl child processes,
`network` is the exchange model + in-protocol collector, `visual` is pixel-level image comparison,
`golden` is element-tree comparison, and `redaction` masks secrets before evidence is written — all
deterministic core (BE-0112), accessed as submodules (`bajutsu.evidence.network`, …) rather than
re-exported, the `crawl/__init__.py` pattern of re-exporting only the engine.
"""

from __future__ import annotations

from bajutsu.evidence.core import (
    Artifact,
    EvidenceSink,
    FileSink,
    NullSink,
    capture,
    write_elements,
    write_screenshot,
    write_wait_diagnostic,
)

__all__ = [
    "Artifact",
    "EvidenceSink",
    "FileSink",
    "NullSink",
    "capture",
    "write_elements",
    "write_screenshot",
    "write_wait_diagnostic",
]
