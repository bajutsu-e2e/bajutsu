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
from bajutsu.anthropic_client import AiConfig, resolve_effort, resolve_model

BINARY = "claude"

# BE-0047 fail-closed gap token: the `claude` CLI is absent. Auth beyond "binary present" is left to
# the CLI — it keeps its Pro/Max/Console credential outside the environment, so a cheap pre-flight
# probe would be unreliable; a genuinely unauthenticated call fails loud at run instead.
CLI_MISSING = "claude-code-cli-missing"

# Tools the adapter's contract never needs, denied outright (BE-0125). `Read` is added back only for
# the vision transport, and only scoped to the per-call scratch directory via `--add-dir`.
_DENY = ("Bash", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "WebFetch", "WebSearch", "Task")

# A runner takes the argv, the working directory, the prompt (fed on stdin), and a wall-clock
# timeout, and returns the CLI's stdout. Injectable for tests.
Runner = Callable[..., str]

# Default per-call wall-clock cap. A best-effort call (the up-front plan) overrides it with a short
# value via MessageRequest.timeout_s, so a hung CLI fails fast instead of blocking the whole run.
_DEFAULT_TIMEOUT = 180.0


def _forced_tool(request: MessageRequest) -> ToolDef | None:
    """The single tool to force, or ``None`` when the model must also pick which tool.

    `NamedTool` names one tool; `AnyTool` over exactly one offered tool is the same case. `AnyTool`
    over several (only `ClaudeAgent.next_action`) returns ``None`` — the wrapper schema then carries
    the choice.

    Raises:
        RuntimeError: the request is malformed — no tools offered, or a `NamedTool` naming a tool
            that isn't among them. Fail loud like the Anthropic adapter would at the API, rather
            than silently downgrading a forced-tool call to free choice over a bogus tool set.
    """
    if not request.tools:
        raise RuntimeError("claude-code: request offers no tools to force a call on")
    if isinstance(request.tool_choice, AnyTool):
        return request.tools[0] if len(request.tools) == 1 else None
    name = request.tool_choice.name
    forced = next((t for t in request.tools if t.name == name), None)
    if forced is None:
        raise RuntimeError(f"claude-code: forced tool {name!r} is not among the offered tools")
    return forced


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
    # `AnyTool` over several tools: the model emits an ordered `actions` list, each entry a
    # `{tool, arguments}` pair (BE-0178) — the Claude Code CLI returns one structured object, not
    # parallel tool calls, so a list-shaped output is how it expresses a multi-action turn. Usually
    # one action; several only when each is determinable from the current screen. `arguments` stays
    # a bare object (not a per-tool `oneOf`) so the schema is one the CLI reliably enforces; the
    # per-tool schemas ride in the note for the model to follow.
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                # At least one action — an empty batch would map to "no tool call" and terminate
                # the record turn as `done`, the same premature-finish the single-tool shape avoided.
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "enum": [t.name for t in request.tools]},
                        "arguments": {"type": "object"},
                    },
                    "required": ["tool", "arguments"],
                },
            }
        },
        "required": ["actions"],
    }
    catalog = "\n".join(
        f"- `{t.name}`: {t.description} — arguments schema: {json.dumps(t.input_schema)}"
        for t in request.tools
    )
    note = (
        "\n\nYou are running non-interactively. Emit one JSON object with an `actions` array; each "
        "entry has `tool` (the chosen tool's name) and `arguments` (an object matching that tool's "
        "input schema). Usually the array holds ONE action. Include several ONLY when each is "
        "determinable from the current screen without seeing the previous action's effect (e.g. "
        "fill several form fields, then submit); a `finish` entry ends the turn. Do not call any "
        "other tool or read any file other than the screenshots named below (when present). The "
        f"tools:\n{catalog}"
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
    scratch: str,
    image: bool,
) -> list[str]:
    """Build the `claude -p` argv for one turn (the prompt is passed on stdin, not in argv).

    ``request.max_tokens`` is intentionally not forwarded: `claude -p` has no output-token cap flag
    and manages its own budget, so the neutral field is honored by the SDK adapters but not here.
    ``request.model`` passes straight to ``--model`` — the caller's `resolve_model` yields the bare
    Anthropic id (e.g. ``claude-opus-4-8``), which is what the CLI expects.

    The prompt is fed via stdin rather than as the trailing `[prompt]` positional because
    ``--allowedTools`` / ``--disallowedTools`` / ``--add-dir`` are variadic (`<tools...>`): a
    positional after them is swallowed as bogus tool names. So the variadic flags go last (each
    stopped by the next `--flag` or end of argv) and take space-separated values, and there is no
    trailing positional at all.
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
    if request.effort:
        cmd += ["--effort", request.effort]  # reasoning-effort level, when the caller set one
    deny = list(_DENY)
    if image:
        cmd += ["--add-dir", scratch, "--allowedTools", "Read"]
    else:
        deny.append("Read")  # no image this turn — the adapter needs no tool at all
    cmd += ["--disallowedTools", *deny]  # variadic + last: space-separated, nothing trails it
    return cmd


# Env vars that would route the CLI to a specific model backend or billing identity. The adapter is
# defined to use the CLI's own subscription login (BE-0176), so these are stripped from the child —
# otherwise a backend configuration inherited from the ambient environment silently takes over. The
# Claude desktop app, for one, exports a full Amazon Bedrock setup (`CLAUDE_CODE_USE_BEDROCK`,
# `AWS_PROFILE`, a Bedrock model ARN in `ANTHROPIC_MODEL`); a `make serve` launched from it inherits
# that, so `record` ran against Bedrock — and, off-cloud, hung in AWS credential resolution (a
# provider-chain fallback probes the metadata endpoint 169.254.169.254, whose TCP connect sits in
# SYN_SENT for the ~75s OS connect timeout). Stripping them keeps the adapter on the subscription
# login regardless of what the environment was configured for.
_ROUTING_ENV = (
    "ANTHROPIC_API_KEY",  # forces API billing instead of the subscription login
    "ANTHROPIC_AUTH_TOKEN",  # a custom bearer token
    "ANTHROPIC_MODEL",  # a Bedrock/Vertex model id or ARN that would override --model
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_USE_BEDROCK",  # → Amazon Bedrock
    "CLAUDE_CODE_USE_VERTEX",  # → Google Vertex
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLOUD_ML_REGION",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


def auth_summary() -> str:
    """One line naming how the child `claude -p` authenticates — shown before a record.

    The claude-code provider always uses the CLI's own subscription login: the adapter drives the
    CLI purely from Bajutsu's configured provider, never from the ambient environment's backend
    routing (see `_ROUTING_ENV`), so this is a fixed statement of that mode.
    """
    return "Claude Code CLI subscription login (Pro/Max/Console)"


def announce(ai: AiConfig | None, provider: str, default_model: str) -> list[str]:
    """This provider's startup disclosure — the registry's `announce` for `claude-code` (BE-0176).

    Beyond the generic provider+model line, the `claude` CLI honors reasoning effort (unlike the
    Anthropic SDK, which ignores it), so this names the resolved effort; and the CLI's forced
    subscription login is non-obvious, so it discloses the auth mode too.
    """
    effort = resolve_effort(ai)
    return [
        f"🤖 AI: {provider} · model {resolve_model(default_model, ai)} · effort {effort or 'default'}",
        f"🔑 auth: {auth_summary()}",
    ]


def _child_env() -> dict[str, str]:
    """The child env for `claude -p`: force the CLI's own subscription login.

    Every backend-routing / billing-identity variable in `_ROUTING_ENV` is dropped so the CLI falls
    back to its own stored subscription login (Pro / Max / Console) rather than an inherited Bedrock /
    Vertex / API-key configuration — see that constant for why (the Claude desktop app's Bedrock env
    leaking in froze `record` in off-cloud AWS credential resolution). With the AWS routing stripped
    the SDK does no credential resolution at all, so the metadata (IMDS 169.254.169.254) probe that
    hung the call cannot fire — disabling it separately is unnecessary.
    """
    env = dict(os.environ)
    for var in _ROUTING_ENV:
        env.pop(var, None)
    return env


def _default_runner(
    cmd: list[str], cwd: str, prompt: str, timeout: float = _DEFAULT_TIMEOUT
) -> str:
    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=_child_env(),
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "`claude` CLI not found — install Claude Code, or switch ai.provider to api-key / "
            "bedrock / ant."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        # The CLI occasionally hangs; subprocess.run has already killed it. Surface a clear, bounded
        # error so a best-effort caller (the plan) can proceed rather than block on the long default.
        raise RuntimeError(f"claude -p timed out after {timeout:g}s") from exc
    if result.returncode != 0:
        # A CLI error (e.g. an auth 401) is reported in the stdout JSON envelope's `result`, with
        # stderr often empty — surface stdout as the fallback so the failure is actionable.
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"claude -p failed ({result.returncode}): {detail[:300]}")
    return result.stdout


def _tool_uses(out: Any, forced_name: str | None) -> list[ToolUseBlock]:
    """Map the CLI's `structured_output` to neutral tool-use blocks, in order (BE-0178).

    A single forced tool yields one block from the arguments object. The multi-tool wrapper yields
    one block per entry in its `actions` list — usually one, several for a determinable batch.
    Malformed output (or a malformed entry) is dropped, matching the empty-content contract.
    """
    if forced_name is not None:
        return [ToolUseBlock(name=forced_name, input=out)] if isinstance(out, dict) else []
    if not isinstance(out, dict) or not isinstance(actions := out.get("actions"), list):
        return []
    blocks: list[ToolUseBlock] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        tool, args = action.get("tool"), action.get("arguments")
        if isinstance(tool, str) and isinstance(args, dict):
            blocks.append(ToolUseBlock(name=tool, input=args))
    return blocks


def _response(stdout: str, forced_name: str | None) -> MessageResponse:
    """Parse the `--output-format json` envelope into a neutral response.

    Raises:
        RuntimeError: the CLI returned non-JSON or a non-object JSON, or its envelope reports an
            error — fail loud (determinism first) rather than silently returning an empty proposal.
    """
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude -p returned non-JSON output: {stdout[:200]!r}") from exc
    if not isinstance(envelope, dict):
        raise RuntimeError(f"claude -p returned non-object JSON: {stdout[:200]!r}")
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p reported an error: {envelope.get('result')!r}")
    content: list[ContentBlock] = list(_tool_uses(envelope.get("structured_output"), forced_name))
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
            cmd = _command(request, schema, note, str(scratch), image)
            stdout = self._runner(
                cmd, str(scratch), prompt, timeout=request.timeout_s or _DEFAULT_TIMEOUT
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return _response(stdout, forced_name)


def factory(ai: AiConfig | None = None) -> ClaudeCodeBackend:
    """Build the Claude Code backend — the registry's adapter factory for `claude-code`."""
    return ClaudeCodeBackend()


def credential_gap(ai: AiConfig | None = None) -> str | None:
    """`CLI_MISSING` when the `claude` binary is absent, else ``None`` (BE-0047)."""
    return None if shutil.which(BINARY) is not None else CLI_MISSING
