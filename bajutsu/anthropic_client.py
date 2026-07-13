"""Construct the Anthropic SDK client for the configured AI provider (Tier-1 paths only).

`record`, `triage`, alert-dismissal, and `crawl` reach Claude through this one factory, so the
provider is swappable without touching the call sites. Whatever is genuinely Anthropic-SDK-specific
lives here — the client construction and the `ant` CLI token IO — while the provider-agnostic config
resolution every backend shares (model / effort / language, and the provider-name resolution
itself) lives in `bajutsu.ai_config`, and the cross-provider registry / credential dispatch in
`bajutsu.ai` (BE-0246). The provider (`ai.provider` / ``BAJUTSU_AI_PROVIDER``, resolved by
`ai_config.resolve_provider`) selects the Anthropic-SDK variant:

- ``api-key`` (default) → ``anthropic.Anthropic()``, authenticated by the key in ``ai.keyEnv``
  (default ``ANTHROPIC_API_KEY``); ``ai.baseUrl`` points the SDK at a self-hosted gateway /
  enterprise proxy, so a screenshot or element tree only ever reaches the user-configured endpoint.
- ``bedrock`` → ``anthropic.AnthropicBedrock()``, authenticated by the standard AWS credential
  chain (env vars / shared profile / instance or task role) and ``AWS_REGION``.
- ``ant`` → ``anthropic.Anthropic(auth_token=…)``, where the bearer token comes from the official
  Anthropic CLI (``ant auth login`` — a browser-based OAuth/SSO flow against the Claude Console),
  so a Claude Pro/Max/Console seat bills the usage instead of an ``ANTHROPIC_API_KEY`` (BE-0163).
  The token is read from the ``ant`` binary at call time; ``ANTHROPIC_PROFILE`` selects a named
  profile (honored by ``ant`` itself), and ``ai.baseUrl`` points the SDK at a gateway as above.

Keys never live in config: ``ai.keyEnv`` names the env var, the value is read here at call time
(BE-0047). Nothing here runs in the deterministic ``run`` / CI gate (DESIGN §2 / §3.1). The SDK
itself is the optional ``ai`` extra (BE-0111) — imported lazily here, only when a model is actually
called — and the Bedrock variant (``anthropic[bedrock]``, which pulls in boto3) layers on top for
the Bedrock path. A base install without the extra raises an actionable error rather than crashing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Protocol

from bajutsu.ai_config import AiConfig, resolve_provider

ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"

# The Anthropic CLI (BE-0163): the external binary whose OAuth/SSO credential backs the `ant`
# provider, invoked via subprocess like the `claude` CLI probe once was. Not vendored or installed.
ANT_BINARY = "ant"
# The gap tokens the `ant` provider's credential check can return: the CLI binary is absent, or it
# is present but has no active credential (not signed in). Mirrors the CLAUDE_CODE_MISSING pattern
# BE-0163 retires from `ai_availability`.
ANT_CLI_MISSING = "ant-cli-missing"
ANT_CLI_UNAUTHENTICATED = "ant-cli-unauthenticated"


def _ant_token_result() -> tuple[int, str, str]:
    """Run the ``ant`` CLI's non-interactive token command; return ``(returncode, token, stderr)``.

    The single subprocess site for the `ant` provider (BE-0163). ``ant`` resolves a named profile
    from ``ANTHROPIC_PROFILE`` itself, so this passes no ``--profile`` and inherits the environment.
    The binary's presence is the caller's concern (`shutil.which`), so an `OSError` here is an
    unexpected exec failure, left to propagate.
    """
    result = subprocess.run(
        [ANT_BINARY, "auth", "print-credentials", "--access-token"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _ant_access_token(ai: AiConfig | None = None) -> str:
    """The bearer token for the `ant` provider, read from the Anthropic CLI (BE-0163).

    Raises:
        RuntimeError: the `ant` binary is absent, or it has no active credential — an actionable
            message pointing at `ant auth login`, so the AI path fails closed (BE-0047) rather than
            constructing a client with no credential.
    """
    if shutil.which(ANT_BINARY) is None:
        raise RuntimeError(
            "the `ant` provider needs the Anthropic CLI: install `ant` and run `ant auth login` "
            "(a browser-based sign-in against the Claude Console), or switch ai.provider to "
            "api-key / bedrock."
        )
    try:
        code, token, err = _ant_token_result()
    except (OSError, subprocess.TimeoutExpired) as e:  # exec failure or a wedged CLI
        raise RuntimeError(f"could not run the `ant` CLI: {e}") from e
    if code != 0 or not token:
        raise RuntimeError(
            f"the `ant` CLI has no active credential — run `ant auth login`{f': {err}' if err else ''}"
        )
    return token


def ant_credential_gap() -> str | None:
    """Whether the `ant` provider can authenticate, or which gap token if not (BE-0163).

    The `ant` half of the Anthropic adapter's credential check (`bajutsu.ai.anthropic`), kept here
    beside the token IO it probes: the CLI binary is absent (`ANT_CLI_MISSING`), or present but with
    no readable credential (`ANT_CLI_UNAUTHENTICATED` — also returned when the probe fails to exec or
    times out, treated as "no credential"). ``None`` when it is signed in.
    """
    if shutil.which(ANT_BINARY) is None:
        return ANT_CLI_MISSING
    try:
        code, token, _ = _ant_token_result()
    except (OSError, subprocess.TimeoutExpired):  # exec failure or a wedged CLI = no credential
        return ANT_CLI_UNAUTHENTICATED
    return None if code == 0 and token else ANT_CLI_UNAUTHENTICATED


def key_env(ai: AiConfig | None) -> str:
    """The name of the env var holding the Anthropic key — `ai.keyEnv` or the default.

    Public so the CLI can name the env var in a human-readable message without re-deriving the
    fallback (BE-0047).
    """
    return (ai and ai.key_env) or ANTHROPIC_KEY_ENV


def make_client(client: Any = None, ai: AiConfig | None = None) -> Any:
    """Return the Anthropic SDK client for the resolved provider.

    ``client`` short-circuits the factory — it is the injection seam the AI classes use in tests.
    For the ``api-key`` provider the key is read from the env var named by ``ai.keyEnv`` (default
    ``ANTHROPIC_API_KEY``) and ``ai.baseUrl`` (when set) points the SDK at a self-hosted gateway,
    so input only ever reaches the user-configured endpoint (BE-0047). Bedrock resolves AWS
    credentials from the environment (the chain + ``AWS_REGION``). The ``ant`` provider (BE-0163)
    reads a bearer token from the Anthropic CLI and passes it as ``auth_token`` (the
    ``Authorization: Bearer`` header), so a subscription/SSO seat bills the usage.

    Raises:
        RuntimeError: The provider's credential is missing — the Anthropic key env var is unset, or
            the `ant` CLI is absent / has no active credential. The factory itself fails closed
            (BE-0047) rather than passing ``api_key=None`` — which the SDK would silently backfill
            from ``ANTHROPIC_API_KEY``, defeating a custom ``ai.keyEnv`` and the no-hosted-default
            promise.
    """
    if client is not None:
        return client
    prov = resolve_provider(ai)
    if prov == "bedrock":
        try:
            from anthropic import AnthropicBedrock
        except ImportError as e:  # the anthropic[bedrock] extra (boto3) isn't installed
            raise RuntimeError(
                "Bedrock support needs the anthropic Bedrock extra; "
                "install it with `uv sync --extra bedrock`."
            ) from e
        return AnthropicBedrock()
    try:
        from anthropic import Anthropic
    except ImportError as e:  # the anthropic SDK (the `ai` extra, BE-0111) isn't installed
        raise RuntimeError(
            "the AI paths need the anthropic SDK; install it with `uv sync --extra ai` "
            "(or `pip install bajutsu[ai]`)."
        ) from e

    if prov == "ant":
        # Bearer token from the Anthropic CLI's OAuth/SSO credential (BE-0163) — auth_token, not
        # api_key, so the SDK sends `Authorization: Bearer` and the subscription/SSO seat is billed.
        kwargs: dict[str, Any] = {"auth_token": _ant_access_token(ai)}
    else:
        name = key_env(ai)
        api_key = os.environ.get(name)
        if not api_key:
            raise RuntimeError(f"no Anthropic API key: ${name} is unset (BE-0047 fail-closed)")
        kwargs = {"api_key": api_key}
    # ai.baseUrl points either credential's SDK client at a self-hosted gateway / proxy (BE-0047).
    if ai and ai.base_url:
        kwargs["base_url"] = ai.base_url
    return Anthropic(**kwargs)


class CachesClient(Protocol):
    """The shape ensure_client memoizes onto — the two attrs every Claude* AI class already holds."""

    _client: Any
    _ai: AiConfig | None


def ensure_client(agent: CachesClient) -> Any:
    """Return the agent's SDK client, building and caching it on ``_client`` on first use.

    The lazy-build-then-cache wrapper the AI authoring/investigation classes share (BE-0140).
    ``make_client`` already short-circuits an injected client; the one thing this adds is
    memoizing the built client on the instance, so a class doesn't reopen the SDK client (and
    reread the key env var) on every call.
    """
    if agent._client is None:
        agent._client = make_client(ai=agent._ai)
    return agent._client
