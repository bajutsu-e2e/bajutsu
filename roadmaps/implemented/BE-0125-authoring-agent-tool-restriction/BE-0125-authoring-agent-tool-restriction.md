**English** · [日本語](BE-0125-authoring-agent-tool-restriction-ja.md)

# BE-0125 — Restrict the claude-code authoring agent tools

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0125](BE-0125-authoring-agent-tool-restriction.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0125") |
| Implementing PR | [#620](https://github.com/bajutsu-e2e/bajutsu/pull/620) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

`ClaudeCodeAgent` (`bajutsu/claude_code_agent.py`), the authoring agent backed by the
local `claude` CLI, launches that CLI in print mode with no explicit tool
restriction, relying only on a one-line system-prompt instruction to keep it from
reaching for tools it shouldn't need.

## Motivation

`ClaudeCodeAgent._command` (`bajutsu/claude_code_agent.py:148`) builds the CLI
invocation as:

```python
def _command(self, prompt: str, schema: dict[str, Any], system: str) -> list[str]:
    cmd = [
        self._binary,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema),
        "--append-system-prompt",
        system,
    ]
```

No `--allowedTools`, `--disallowedTools`, or `--permission-mode` flag is passed, so
the CLI runs with its default tool surface (shell, file read/write, and whatever else
the invoking environment grants) and the only thing steering it away from calling
tools is prose appended to the system prompt — a soft guard, not an enforced one. This
matters because this agent's job is to decide the next test action from an
**observation of the app under test** (`_render(observation)` in the prompt), and that
observation includes on-screen text and accessibility labels the agent did not
generate. An adversarial or compromised screen — a label or field value crafted to
look like an instruction — is a prompt-injection vector: text in the observation could
try to talk the model into invoking a tool (running a shell command, reading or
writing an arbitrary file) instead of returning the expected structured action.
Severity is medium: exploitation requires the app under test to render attacker-
controlled content into something the accessibility tree/screenshot surfaces, but that
is exactly the situation E2E authoring is meant to exercise (logging into real or
staging backends, third-party web content in a webview, and so on).

## Detailed design

Constrain the CLI's tool surface for this one call site; nothing about the agent
protocol, the JSON-schema action contract, or the deterministic run/CI path changes:

- Pass an explicit `--disallowedTools` (or the equivalent `--allowedTools` allowlist,
  whichever the installed CLI version supports more precisely) in `_command`,
  covering shell execution and file read/write — this agent's contract is "return one
  structured action," so it never legitimately needs those tools.
- Additionally pass `--permission-mode` set to a non-interactive, deny-by-default
  mode, so that even a tool call the flag list doesn't anticipate cannot silently
  proceed (print mode already can't prompt a human, so an unhandled permission
  request should fail closed, not fall through).
- Keep the change local to `ClaudeCodeAgent`: the API-backed `ClaudeAgent` in
  `bajutsu/claude_agent.py` doesn't invoke a CLI and is unaffected; `plan()`'s CLI
  invocation goes through the same `_command` helper, so it is covered for free.

## Alternatives considered

- **Rely on the system-prompt instruction alone (status quo).** Rejected: a system
  prompt is guidance the model can be argued out of by sufficiently adversarial input
  in the observation; it is not an enforcement boundary, which is what closing a
  prompt-injection vector needs.
- **Sandbox the whole `claude` process (container/VM) instead of restricting tools.**
  Rejected as disproportionate for this codebase: it adds an operational dependency
  (container runtime) to a path that today only needs a CLI binary, for a risk that the
  CLI's own flags already address directly.
- **Drop the Claude Code agent path and keep only the API agent.** Rejected: the whole
  point of `ClaudeCodeAgent` is to let authoring run off a Claude Pro/Max subscription
  instead of API credits (see the module docstring); removing it removes that option
  rather than fixing the gap.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add `--disallowedTools`/`--allowedTools` to `ClaudeCodeAgent._command` covering
      shell and file read/write.
- [x] Add `--permission-mode` set to a deny-by-default, non-interactive mode.
- [x] Add a test asserting the built command always carries the restriction flags.

- [#620](https://github.com/bajutsu-e2e/bajutsu/pull/620) — Add `--disallowedTools` (`Bash,Read,Write,Edit,NotebookEdit,Glob,Grep`) and
  `--permission-mode default` to `ClaudeCodeAgent._command`; both `next_action` and `plan`
  are covered through the shared helper, with tests asserting the flags are always present.

## References

- `bajutsu/claude_code_agent.py:148` (`ClaudeCodeAgent._command`)
- Related: [BE-0047](../../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
  (AI data sovereignty)
- Originates from the 2026-07-02 codebase-analysis report (security).
