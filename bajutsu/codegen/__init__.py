"""Codegen — transpile a passing scenario into a native test for a team's own CI.

Split by target on the shared scenario walk (BE-0083): `common` holds the walk, `xcuitest` /
`playwright` / `uiautomator` supply the per-line syntax, and `emit` is the single dispatcher
both transports (the CLI and serve) call. The public API is re-exported here, so
`from bajutsu.codegen import generate_test, EMIT_TARGETS, CodegenError, to_xcuitest,
class_name_for` is unchanged after the flat `codegen_*` modules became this package (BE-0257).
"""

from __future__ import annotations

from bajutsu.codegen.emit import EMIT_TARGETS, CodegenError, generate_test
from bajutsu.codegen.xcuitest import class_name_for, to_xcuitest

__all__ = [
    "EMIT_TARGETS",
    "CodegenError",
    "class_name_for",
    "generate_test",
    "to_xcuitest",
]
