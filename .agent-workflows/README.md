# Shared agent workflows

This directory is the source of truth for procedures shared by coding agents. Each workflow defines
one task sequence with its safety constraints and completion criteria. Runtime adapters under
[`../.agent-hosts/`](../.agent-hosts/) and [`.claude/skills`](../.claude/skills/) expose each
workflow to supported agents.

Agent runtimes do not discover `.agent-workflows` directly. A runtime adapter must load the entire
workflow before applying its runtime-specific tool mapping.

## Directory structure

```text
.agent-workflows/
└── <name>/
    ├── workflow.md
    └── <portable resources>
```

`workflow.md` is the authoritative procedure. A workflow may keep portable scripts, references,
templates, or validation resources beside it. For example, `document-writing/textlint` contains
the shared prose validation runtime.

## What belongs here

Store content here when every supported agent should follow the same rule:

- task steps and decision points;
- safety constraints and escalation conditions;
- required inputs, outputs, and verification;
- repository commands that do not depend on an agent runtime; and
- scripts, references, and templates used by the shared procedure.

Keep these runtime-specific details out of shared workflows:

- model choices and tool names;
- slash commands, hooks, and plugins; and
- interface metadata.

Runtime-specific details belong in the corresponding adapter.

When changing behavior, update `workflow.md` first. Update an adapter when the shared change needs
a different runtime mapping or selection hint. Following this order prevents the Claude Code and
Codex procedures from drifting apart.
