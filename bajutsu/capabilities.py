"""Which commands reach Claude, and which are Claude-free — the one authoritative classification.

BE-0101 makes the boundary between the Claude-using authoring / investigation paths and the
deterministic, zero-config Claude-free ones a first-class property of the tool. This module is its
single source of truth: the CLI help grouping, the `docs/ai-boundary` reference, the `doctor`
readiness section, and the zero-config regression test all read it, so those surfaces can never
disagree and a newly added command is classified in exactly one place.

The axis is whether a path invokes Claude *at all* — independent of provider (Anthropic / Bedrock /
the Anthropic CLI `ant`), a credential / config detail (BE-0047 / BE-0053 / BE-0163), not part of
the classification. It is at the granularity of the *path*, not the command
name: `triage` is Claude-free but `triage --ai` reaches Claude, and a single flag flips it — so a
command carries both its default classification and the flag (if any) that flips it.

Kept import-light on purpose (stdlib only): it sits on the deterministic path, and the zero-config
guarantee (BE-0101) forbids pulling an AI SDK in at import time.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    """One command's place on the Claude / Claude-free boundary (BE-0101).

    `uses_claude` classifies the command's *default* path. `claude_flag`, when set, names the flag
    that flips an otherwise-Claude-free command onto the Claude path (`triage --ai`,
    `run --alert-handling`) — the path-granularity the classification is expressed at; it is None
    when no flag changes the classification.
    """

    command: str
    uses_claude: bool
    claude_flag: str | None = None


# Every CLI command, classified exactly once. `test_capabilities.py` asserts this covers the
# registered command set with no gaps or extras, so adding a command forces a classification here.
CAPABILITIES: tuple[Capability, ...] = (
    Capability("record", uses_claude=True),
    Capability("crawl", uses_claude=True),
    Capability("triage", uses_claude=False, claude_flag="--ai"),
    Capability("run", uses_claude=False, claude_flag="--alert-handling"),
    Capability("doctor", uses_claude=False),
    Capability("codegen", uses_claude=False),
    Capability("trace", uses_claude=False),
    Capability("lint", uses_claude=False),
    Capability("schema", uses_claude=False),
    Capability("approve", uses_claude=False),
    Capability("mcp", uses_claude=False),
    Capability("worker", uses_claude=False),
    Capability("report", uses_claude=False),
    Capability("audit", uses_claude=False),
    Capability("coverage", uses_claude=False),
    Capability("stats", uses_claude=False),
    Capability("flakiness", uses_claude=False),
    Capability("export", uses_claude=False),
    Capability("serve", uses_claude=False),
    Capability("project", uses_claude=False),
)

_BY_COMMAND = {c.command: c for c in CAPABILITIES}


def by_command(name: str) -> Capability | None:
    """The classification for command `name`, or None when it is not classified."""
    return _BY_COMMAND.get(name)


def claude_using() -> tuple[str, ...]:
    """Commands whose default path reaches Claude, in declaration order (BE-0101)."""
    return tuple(c.command for c in CAPABILITIES if c.uses_claude)


def claude_free() -> tuple[str, ...]:
    """Commands whose default path never reaches Claude, in declaration order (BE-0101)."""
    return tuple(c.command for c in CAPABILITIES if not c.uses_claude)
