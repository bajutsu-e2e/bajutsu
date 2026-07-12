"""Build the injected `MailboxReader` for the `email` step, via a transport registry (BE-0046 / BE-0186).

The deterministic match/extract/selection logic is pure (`bajutsu.mailbox`); this is the only place
that touches the network. A mailbox is a backend behind one interface: BE-0186 keys the `MailboxReader`
seam on a transport `kind` (`http`, later `imap`) through a registry that mirrors `bajutsu/ai/registry.py`,
so adding a transport is *register an adapter*, not *branch the runner*. The registry ships only the
`http` reference adapter (the existing HTTP-JSON reader, re-homed); an unknown `kind` fails closed when
the runner resolves the mailbox. Kept out of the orchestrator so the run loop stays backend-agnostic.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

from bajutsu import interp
from bajutsu.config import Mailbox
from bajutsu.drivers import base
from bajutsu.mailbox import MailboxMessage, read_messages
from bajutsu.orchestrator import MailboxReader

# An adapter builds a `MailboxReader` for its transport from the resolved mailbox config and the run's
# secret bindings (the ${secrets.*} the url/headers reference). Keyed on transport, never on vendor:
# JSON providers differ only in field names, which the `fields` mapping already absorbs one level down.
MailboxAdapter = Callable[[Mailbox, Mapping[str, str]], MailboxReader]

_ADAPTERS: dict[str, MailboxAdapter] = {}


def register(kind: str, adapter: MailboxAdapter) -> None:
    """Register *adapter* under the transport *kind* (idempotent — a later call overrides)."""
    _ADAPTERS[kind] = adapter


def _build_http_reader(cfg: Mailbox, bindings: Mapping[str, str]) -> MailboxReader:
    """The built-in `http` adapter: a reader that GETs the configured inbox and normalizes its JSON.

    `${secrets.*}` tokens in the url and headers are interpolated from `bindings` (the same secrets
    the steps use), so credentials live in config-referenced secrets, never in the scenario file.
    """
    url = str(interp.interpolate(cfg.url, bindings))
    headers = {str(k): str(v) for k, v in interp.interpolate(dict(cfg.headers), bindings).items()}
    messages_path = cfg.messages
    fields = dict(cfg.fields)

    class _HttpMailbox:
        def fetch(self, timeout: float) -> list[MailboxMessage]:
            if not url.startswith(("http://", "https://")):
                raise base.SelectorError(f"email: mailbox url must be http/https, got {url!r}")
            req = urllib.request.Request(url, headers=headers)  # noqa: S310 (scheme checked above)
            try:
                # Bound a single request by the poll's remaining budget (capped at 30s), so one slow
                # request can't overrun the step's `email.timeout`.
                with urllib.request.urlopen(req, timeout=min(timeout, 30.0)) as resp:  # noqa: S310 (http/https only)
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as e:
                raise base.SelectorError(f"email: mailbox returned status {e.code}") from e
            except (urllib.error.URLError, ValueError) as e:
                raise base.SelectorError(f"email: mailbox fetch failed: {e}") from e
            return read_messages(payload, messages_path, fields)

    return _HttpMailbox()


def _ensure_builtins() -> None:
    """Register the built-in `http` adapter on first use (`setdefault` leaves a test override intact)."""
    _ADAPTERS.setdefault("http", _build_http_reader)


def build_mailbox_reader(cfg: Mailbox | None, bindings: Mapping[str, str]) -> MailboxReader | None:
    """The `MailboxReader` for the configured transport, or None if unconfigured.

    Resolves `cfg.kind` against the registry — this is BE-0186's single fail-closed point, mirroring
    BE-0104's `_provider_name`: an unknown `kind` raises here (a clean config error) the first time a
    run resolves the mailbox, rather than silently falling back.

    Raises:
        ValueError: `cfg.kind` has no registered adapter.
    """
    if cfg is None:
        return None
    _ensure_builtins()
    if cfg.kind not in _ADAPTERS:
        allowed = ", ".join(repr(k) for k in _ADAPTERS)
        raise ValueError(f"unknown mailbox kind {cfg.kind!r}: registered kinds are {allowed}")
    return _ADAPTERS[cfg.kind](cfg, bindings)
