"""Build the injected `MailboxReader` for the `email` step (BE-0046).

The deterministic match/extract/selection logic is pure (`bajutsu.mailbox`); this is the only place
that touches the network — a thin HTTP GET of the configured inbox, normalized through the pure
`read_messages`. Kept out of the orchestrator so the run loop stays backend-agnostic.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping

from bajutsu import interp
from bajutsu.config import Mailbox
from bajutsu.drivers import base
from bajutsu.mailbox import MailboxMessage, read_messages
from bajutsu.orchestrator import MailboxReader


def build_mailbox_reader(cfg: Mailbox | None, bindings: Mapping[str, str]) -> MailboxReader | None:
    """A `MailboxReader` that GETs the configured inbox and normalizes its JSON; None if unconfigured.

    `${secrets.*}` tokens in the url and headers are interpolated from `bindings` (the same secrets
    the steps use), so credentials live in config-referenced secrets, never in the scenario file.
    """
    if cfg is None:
        return None
    url = str(interp.interpolate(cfg.url, bindings))
    headers = {str(k): str(v) for k, v in interp.interpolate(dict(cfg.headers), bindings).items()}
    messages_path = cfg.messages
    fields = dict(cfg.fields)

    class _HttpMailbox:
        def fetch(self) -> list[MailboxMessage]:
            if not url.startswith(("http://", "https://")):
                raise base.SelectorError(f"email: mailbox url must be http/https, got {url!r}")
            req = urllib.request.Request(url, headers=headers)  # noqa: S310 (scheme checked above)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (http/https only)
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as e:
                raise base.SelectorError(f"email: mailbox returned status {e.code}") from e
            except (urllib.error.URLError, ValueError) as e:
                raise base.SelectorError(f"email: mailbox fetch failed: {e}") from e
            return read_messages(payload, messages_path, fields)

    return _HttpMailbox()
