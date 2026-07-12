"""Tests for the mailbox provider registry (bajutsu/runner/mailbox.py, BE-0186).

The `email` step reads its inbox through a `MailboxReader` seam; BE-0186 turns the single hardcoded
HTTP path into a registry keyed by transport `kind` (`http`, later `imap`), mirroring the AI
provider registry (BE-0104). These tests cover the built-in `http` default, fail-closed resolution
of an unknown `kind`, and that the registry is a real extension point — all pure, no network.
"""

from __future__ import annotations

import pytest

from bajutsu.config import Mailbox
from bajutsu.mailbox import MailboxMessage
from bajutsu.orchestrator import MailboxReader
from bajutsu.runner import mailbox as mb


def test_none_config_yields_no_reader() -> None:
    assert mb.build_mailbox_reader(None, {}) is None


def test_default_kind_is_http() -> None:
    # `kind` omitted -> the built-in HTTP reader, so a pre-BE-0186 `mailbox:` block is unchanged.
    reader = mb.build_mailbox_reader(Mailbox(url="https://inbox.test/api"), {})
    assert reader is not None
    assert type(reader).__name__ == "_HttpMailbox"


def test_explicit_http_kind_resolves_the_same_reader() -> None:
    reader = mb.build_mailbox_reader(Mailbox(kind="http", url="https://inbox.test/api"), {})
    assert reader is not None
    assert type(reader).__name__ == "_HttpMailbox"


def test_unknown_kind_fails_closed() -> None:
    # Fail-closed at resolution (like BE-0104's `_provider_name`), never a silent fallback.
    with pytest.raises(ValueError, match="unknown mailbox kind 'imap'"):
        mb.build_mailbox_reader(Mailbox(kind="imap", url="https://inbox.test/api"), {})


def test_registry_is_a_real_extension_point() -> None:
    """Register a fake transport, resolve it, then remove it (global registry)."""

    class _FakeReader:
        def fetch(self, timeout: float) -> list[MailboxMessage]:
            return []

    def _adapter(cfg: Mailbox, bindings: object) -> MailboxReader:
        return _FakeReader()

    mb.register("fake", _adapter)
    try:
        reader = mb.build_mailbox_reader(Mailbox(kind="fake", url="x"), {})
        assert isinstance(reader, _FakeReader)
    finally:
        mb._ADAPTERS.pop("fake", None)
