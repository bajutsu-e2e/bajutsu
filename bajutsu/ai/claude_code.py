"""Claude Code CLI adapter for the vendor-neutral AI seam (BE-0176).

Revives a Claude Code backend, this time behind BE-0104's `AiBackend` seam so one adapter serves
every AI path with vision — not the text-only, `record`-only detour BE-0163 removed. It shells out
to `claude -p --output-format json` and expresses BE-0104's forced tool call as the CLI's
`--json-schema` structured output.

Vision travels by file: each `ImagePart` is written to a per-call scratch directory whose path the
prompt names, and the CLI is allowed only `Read` (scoped to that directory via `--add-dir`) to view
it — the mechanism the earlier attempt lacked. On-screen text and element labels are
attacker-influenced (BE-0125), so every other tool is denied and `--permission-mode` stays
fail-closed. Billing draws on the `claude` CLI's Claude Pro / Max / Console credential, so
`ANTHROPIC_API_KEY` is stripped from the child (its presence would force API billing) and the call
runs from the empty scratch directory so the CLI loads none of this repo's CLAUDE.md / skills / MCP.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bajutsu.ai.base import (
    AnyTool,
    ContentBlock,
    ImagePart,
    MessageRequest,
    MessageResponse,
    TextPart,
    ToolDef,
    ToolUseBlock,
)
from bajutsu.anthropic_client import AiConfig

BINARY = "claude"

# BE-0047 fail-closed gap token: the `claude` CLI is absent. Auth beyond "binary present" is left to
# the CLI — it keeps its Pro/Max/Console credential outside the environment, so a cheap pre-flight
# probe would be unreliable; a genuinely unauthenticated call fails loud at run instead.
CLI_MISSING = "claude-code-cli-missing"

# Tools the adapter's contract never needs, denied outright (BE-0125). `Read` is added back only for
# the vision transport, and only scoped to the per-call scratch directory via `--add-dir`.
_DENY = ("Bash", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "WebFetch", "WebSearch", "Task")

# A runner takes the argv and the working directory and returns the CLI's stdout. Injectable for tests.
Runner = Callable[[list[str], str], str]


def _forced_tool(request: MessageRequest) -> ToolDef | None:
    """The single tool to force, or ``None`` when the model must also pick which tool.

    `NamedTool` names one tool; `AnyTool` over exactly one offered tool is the same case. `AnyTool`
    over several (only `ClaudeAgent.next_action`) returns ``None`` — the wrapper schema then carries
    the choice.
    """
    if isinstance(request.tool_choice, AnyTool):
        return request.tools[0] if len(request.tools) == 1 else None
    name = request.tool_choice.name
    return next((t for t in request.tools if t.name == name), None)


def _schema_and_note(request: MessageRequest) -> tuple[dict[str, Any], str, str | None]:
    """Map BE-0104's tools + `tool_choice` to a CLI `--json-schema` and its system-prompt note.

    Returns the JSON schema, the note appended to the system prompt, and the tool name to attribute
    the result to — ``None`` when the schema is the multi-tool wrapper (the model emits the name).
    """
    forced = _forced_tool(request)
    if forced is not None:
        note = (
            f"\n\nYou are running non-interactively. Emit exactly one JSON object: the arguments "
            f"for the `{forced.name}` tool, matching its input schema. Do not call any other tool "
            "or read any file other than the screenshots named below (when present)."
        )
        return forced.input_schema, note, forced.name
    # `AnyTool` over several tools: the model picks `tool` and supplies its `arguments`. `arguments`
    # stays a bare object (not a per-tool `oneOf`) so the schema is one the CLI reliably enforces;
    # the per-tool schemas ride in the note for the model to follow.
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": [t.name for t in request.tools]},
            "arguments": {"type": "object"},
        },
        "required": ["tool", "arguments"],
    }
    catalog = "\n".join(
        f"- `{t.name}`: {t.description} — arguments schema: {json.dumps(t.input_schema)}"
        for t in request.tools
    )
    note = (
        "\n\nYou are running non-interactively. Choose exactly one tool and emit one JSON object: "
        "`tool` is the chosen tool's name and `arguments` is an object matching that tool's input "
        "schema. Do not call any other tool or read any file other than the screenshots named "
        f"below (when present). The tools:\n{catalog}"
    )
    return schema, note, None


def _prompt_and_images(request: MessageRequest, scratch: Path) -> tuple[str, bool]:
    """Flatten the user message into a prompt string, writing each image to *scratch* by path.

    Returns the prompt and whether any image was written (so the caller allows `Read` only then).
    """
    texts: list[str] = []
    paths: list[str] = []
    for message in request.messages:
        for part in message.content:
            if isinstance(part, ImagePart):
                path = scratch / f"screen-{len(paths)}.png"
                path.write_bytes(part.data)
                paths.append(str(path))
            elif isinstance(part, TextPart):
                texts.append(part.text)
    prompt = "\n\n".join(texts)
    if paths:
        listing = "\n".join(f"- {p}" for p in paths)
        prompt += (
            "\n\nThe screen for this turn is saved as PNG file(s). Use the Read tool to view each "
            f"one before you decide:\n{listing}"
        )
    return prompt, bool(paths)


def _command(
    request: MessageRequest,
    schema: dict[str, Any],
    note: str,
    prompt: str,
    scratch: str,
    image: bool,
) -> list[str]:
    """Build the `claude -p` argv for one turn.

    ``request.max_tokens`` is intentionally not forwarded: `claude -p` has no output-token cap flag
    and manages its own budget, so the neutral field is honored by the SDK adapters but not here.
    ``request.model`` passes straight to ``--model`` — the caller's `resolve_model` yields the bare
    Anthropic id (e.g. ``claude-opus-4-8``), which is what the CLI expects.
    """
    cmd = [
        BINARY,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema),
        "--append-system-prompt",
        request.system + note,
        "--model",
        request.model,
        "--permission-mode",
        "default",
    ]
    deny = list(_DENY)
    if image:
        cmd += ["--add-dir", scratch, "--allowedTools", "Read"]
    else:
        deny.append("Read")  # no image this turn — the adapter needs no tool at all
    cmd += ["--disallowedTools", ",".join(deny), prompt]
    return cmd


def _child_env() -> dict[str, str]:
    """The child env with `ANTHROPIC_API_KEY` removed, so billing uses the CLI's subscription token."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def _default_runner(cmd: list[str], cwd: str) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd, env=_child_env(), timeout=180
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "`claude` CLI not found — install Claude Code, or switch ai.provider to api-key / "
            "bedrock / ant."
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(f"claude -p failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def _tool_use(out: Any, forced_name: str | None) -> ToolUseBlock | None:
    """Map the CLI's `structured_output` to a neutral tool-use block, or ``None`` when malformed."""
    if not isinstance(out, dict):
        return None
    if forced_name is not None:
        return ToolUseBlock(name=forced_name, input=out)
    tool, args = out.get("tool"), out.get("arguments")
    if not isinstance(tool, str) or not isinstance(args, dict):
        return None
    return ToolUseBlock(name=tool, input=args)


def _response(stdout: str, forced_name: str | None) -> MessageResponse:
    """Parse the `--output-format json` envelope into a neutral response.

    Raises:
        RuntimeError: the CLI returned non-JSON, or its envelope reports an error — fail loud
            (determinism first) rather than silently returning an empty proposal.
    """
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude -p returned non-JSON output: {stdout[:200]!r}") from exc
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p reported an error: {envelope.get('result')!r}")
    block = _tool_use(envelope.get("structured_output"), forced_name)
    content: list[ContentBlock] = [block] if block is not None else []
    return MessageResponse(
        content=content, stop_reason=envelope.get("stop_reason"), usage=envelope.get("usage")
    )


class ClaudeCodeBackend:
    """`AiBackend` over the Claude Code CLI (`claude -p`), with file-based vision (BE-0176).

    ``runner`` short-circuits the subprocess — the injection seam the adapter's tests use.
    """

    def __init__(self, *, runner: Runner | None = None) -> None:
        self._runner = runner or _default_runner

    def create_message(self, request: MessageRequest) -> MessageResponse:
        schema, note, forced_name = _schema_and_note(request)
        scratch = Path(tempfile.mkdtemp(prefix="bajutsu-cc-"))
        try:
            prompt, image = _prompt_and_images(request, scratch)
            cmd = _command(request, schema, note, prompt, str(scratch), image)
            stdout = self._runner(cmd, str(scratch))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return _response(stdout, forced_name)


def factory(ai: AiConfig | None = None) -> ClaudeCodeBackend:
    """Build the Claude Code backend — the registry's adapter factory for `claude-code`."""
    return ClaudeCodeBackend()


def credential_gap(ai: AiConfig | None = None) -> str | None:
    """`CLI_MISSING` when the `claude` binary is absent, else ``None`` (BE-0047)."""
    return None if shutil.which(BINARY) is not None else CLI_MISSING
