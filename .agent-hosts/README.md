# Agent runtime adapters

This directory contains runtime-specific adapters for Bajutsu's shared agent workflows. An adapter
selects a workflow and loads its entire procedure from
[`../.agent-workflows/`](../.agent-workflows/). The adapter then maps the procedure to one
runtime's tools and configuration.

`.agent-hosts` is a Bajutsu repository convention. Codex and Claude Code do not discover the
directory directly. Each runtime needs an entry point at a path that the runtime recognizes:

- [`.agents`](../.agents) links to [`codex/`](codex/), exposing `codex/skills` as
  `.agents/skills` for Codex.
- [`.claude/skills`](../.claude/skills/) contains the corresponding Claude Code adapters.

## Directory structure

```text
.agent-hosts/
└── codex/
    └── skills/
        └── <name>/
            ├── SKILL.md
            └── agents/
                └── openai.yaml
```

Each `SKILL.md` stays small. Its frontmatter helps Codex select the skill. Its body instructs Codex
to read the matching `.agent-workflows/<name>/workflow.md` before acting. Optional
`agents/openai.yaml` files contain Codex-specific interface metadata.

## What belongs here

Store configuration here when the configuration applies to a single agent runtime:

- skill discovery metadata and default prompts;
- runtime-specific tool names, policies, and invocation syntax;
- mappings from portable workflow operations to runtime capabilities; and
- runtime-specific interface metadata, such as `agents/openai.yaml`.

Do not copy a complete workflow into an adapter. Put procedures and portable resources in
[`../.agent-workflows/`](../.agent-workflows/), then make each adapter load the shared workflow.
Do not link `.agents/skills` to the complete `.claude/skills` tree. The link would make Codex read
Claude-specific configuration. Examples include models and tools as well as lifecycle callbacks,
commands, and plugins.

When adding another runtime, give the runtime a separate subtree. Expose the subtree through the
entry point that the runtime recognizes.
