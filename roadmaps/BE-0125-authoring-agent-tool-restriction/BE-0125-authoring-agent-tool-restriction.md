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
| Related | [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md) |
| Superseded by | [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) |
<!-- /BE-METADATA -->

## Introduction

`ClaudeCodeAgent` (`bajutsu/claude_code_agent.py`) is the authoring agent backed by the local
`claude` CLI. It launches that CLI in print mode with no explicit tool restriction. The only guard
against the CLI reaching for tools it does not need is a one-line system-prompt instruction.

## Motivation

`ClaudeCodeAgent._command` (`bajutsu/claude_code_agent.py:148`) builds the CLI invocation as:

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

The command passes no `--allowedTools`, `--disallowedTools`, or `--permission-mode` flag. The CLI
therefore runs with its default tool surface: shell, file read and write, and whatever else the
invoking environment grants. Only prose appended to the system prompt steers the CLI away from
calling tools. That guard is soft, not enforced.

The guard matters because the agent decides the next test action from an **observation of the app
under test** (`_render(observation)` in the prompt). The observation includes on-screen text and
accessibility labels that the agent did not generate. An adversarial or compromised screen, such as a
label or field value crafted to look like an instruction, is therefore a prompt-injection vector. Text
in the observation could try to talk the model into invoking a tool (running a shell command, or
reading or writing an arbitrary file) instead of returning the expected structured action.

Severity is medium. Exploitation requires the app under test to render attacker-controlled content
into something that the accessibility tree or screenshot surfaces. That situation is exactly what E2E
authoring is meant to exercise: logging into real or staging backends, third-party web content in a
webview, and so on.

## Detailed design

The change constrains the CLI's tool surface at this one call site. The agent protocol, the
JSON-schema action contract, and the deterministic run/CI path stay unchanged.

- Pass an explicit `--disallowedTools` in `_command`, or the equivalent `--allowedTools` allowlist,
  whichever the installed CLI version supports more precisely. The list covers shell execution and
  file read and write. The agent's contract is "return one structured action", so it never legitimately
  needs those tools.
- Also pass `--permission-mode` set to a non-interactive, deny-by-default mode. Then even a tool call
  that the flag list does not anticipate cannot silently proceed. Print mode already cannot prompt a
  human, so an unhandled permission request should fail closed instead of falling through.
- Keep the change local to `ClaudeCodeAgent`. The API-backed `ClaudeAgent` in
  `bajutsu/claude_agent.py` invokes no CLI and is unaffected. The CLI invocation in `plan()` goes
  through the same `_command` helper, so it is covered at no extra cost.

## Alternatives considered

- **Rely on the system-prompt instruction alone (status quo).** Rejected. A system prompt is guidance
  that sufficiently adversarial input in the observation can argue the model out of. It is not an
  enforcement boundary, and closing a prompt-injection vector needs one.
- **Sandbox the whole `claude` process (container or VM) instead of restricting tools.** Rejected as
  disproportionate for this codebase. A sandbox adds an operational dependency (a container runtime)
  to a path that today needs only a CLI binary. The CLI's own flags already address the risk directly.
- **Drop the Claude Code agent path and keep only the API agent.** Rejected. The whole point of
  `ClaudeCodeAgent` is to let authoring run off a Claude Pro/Max subscription instead of API credits
  (see the module docstring). Removing the path removes that option and does not fix the gap.

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
- Related: [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
  (AI data sovereignty)
- Originates from the 2026-07-02 codebase-analysis report (security).
