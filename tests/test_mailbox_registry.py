"""Tests for the mailbox registry and its IMAP / POP3 adapters (BE-0186, BE-0514).

All pure: `imaplib` and `poplib` are replaced with in-memory stubs, so nothing here touches the
network.
"""

from __future__ import annotations

import imaplib
import poplib
import ssl
from collections.abc import Collection
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from bajutsu.common.config import Mailbox
from bajutsu.common.drivers import base
from bajutsu.common.mailbox import MailboxMessage
from bajutsu.common.orchestrator import MailboxReader
from bajutsu.common.runner import mailbox as mb

_RAW = b"To: u@x.io\r\nSubject: Code\r\nDate: Thu, 01 Jan 2026 00:00:00 +0000\r\n\r\ncode 4242\r\n"
_INTERNALDATE = b'INTERNALDATE "02-Jan-2026 09:00:00 +0900"'


def _cfg(**overrides: Any) -> Mailbox:
    values: dict[str, Any] = {
        "kind": "imap",
        "host": "mail.test",
        "username": "u",
        "password": "pw-s3cr3t",
    }
    return Mailbox.model_validate(values | overrides)


def _reader(cfg: Mailbox, bindings: dict[str, str] | None = None) -> MailboxReader:
    reader = mb.build_mailbox_reader(cfg, bindings or {})
    assert reader is not None
    return reader


# --- config ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["url", "headers", "messages", "fields"])
def test_a_removed_http_key_stops_the_block_loading(key: str) -> None:
    with pytest.raises(ValidationError, match=r"imap.*pop3.*`http` step"):
        Mailbox.model_validate({key: "x"})


def test_pop3_rejects_a_folder() -> None:
    with pytest.raises(ValidationError, match="POP3 has a single inbox"):
        _cfg(kind="pop3", folder="INBOX")


def test_there_is_no_clear_text_security_mode() -> None:
    with pytest.raises(ValidationError, match="security"):
        _cfg(security="none")


# --- registry -------------------------------------------------------------------------------------


def test_none_config_yields_no_reader() -> None:
    assert mb.build_mailbox_reader(None, {}) is None


def test_unknown_kind_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown mailbox kind 'http'"):
        mb.build_mailbox_reader(_cfg(kind="http"), {})


def test_registry_is_a_real_extension_point() -> None:
    """Register a fake transport, resolve it, then remove it (global registry)."""

    class _FakeReader:
        def list_ids(self, timeout: float) -> frozenset[str]:
            return frozenset()

        def fetch(self, ids: Collection[str], timeout: float) -> list[MailboxMessage]:
            return []

        def check_same_inbox(self, baseline: frozenset[str], current: frozenset[str]) -> None:
            pass

    mb.register("fake", lambda cfg, bindings: _FakeReader())
    try:
        assert isinstance(_reader(_cfg(kind="fake")), _FakeReader)
    finally:
        mb._ADAPTERS.pop("fake", None)


# --- stub servers ---------------------------------------------------------------------------------


class _Socket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)


class _Stub:
    """Records every command; a class-level `fail` maps a command name to the error it raises."""

    log: ClassVar[list[tuple[Any, ...]]]
    fail: ClassVar[dict[str, Exception]]

    def __init__(self, host: str, port: int, **kwargs: Any) -> None:
        self.sock = _Socket()
        self._record("connect", type(self).__name__, host, port, kwargs)

    def _record(self, name: str, *args: Any) -> None:
        self.log.append((name, *args))
        if name in self.fail:
            raise self.fail[name]


class _Imap(_Stub):
    # The adapter reads these off `imaplib.IMAP4`, which this stub replaces.
    error: ClassVar[type[Exception]] = imaplib.IMAP4.error
    abort: ClassVar[type[Exception]] = imaplib.IMAP4.abort
    capabilities: ClassVar[tuple[str, ...]] = ("IMAP4REV1", "STARTTLS")
    uids: ClassVar[bytes] = b"1 2"
    select_status: ClassVar[str] = "OK"
    uid_status: ClassVar[str] = "OK"
    validity: ClassVar[list[bytes | None]] = [b"7"]

    def starttls(self, ssl_context: ssl.SSLContext) -> None:
        self._record("starttls", ssl_context)

    def login(self, user: str, password: str) -> None:
        self._record("login", user, password)

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self._record("select", folder, readonly)
        return self.select_status, [b"2"]

    def response(self, code: str) -> tuple[str, list[bytes | None]]:
        return code, self.validity

    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]:
        self._record(command.lower(), *args)
        if command == "SEARCH":
            return self.uid_status, [self.uids]
        if args[0] == "2":  # expunged between the search and the fetch
            return self.uid_status, [None]
        head = b"1 (UID 1 BODY[] {%d}" % len(_RAW)
        return self.uid_status, [(head, _RAW), b" " + _INTERNALDATE + b")"]

    def logout(self) -> None:
        self._record("logout")


class _ImapTls(_Imap):
    pass


class _Pop(_Stub):
    capabilities: ClassVar[dict[str, list[str]]] = {"STLS": [], "UIDL": []}
    listing: ClassVar[list[bytes]] = [b"1 aaa", b"2 bbb"]

    def capa(self) -> dict[str, list[str]]:
        self._record("capa")
        return self.capabilities

    def stls(self, context: ssl.SSLContext) -> None:
        self._record("stls", context)

    def user(self, name: str) -> None:
        self._record("user", name)

    def pass_(self, password: str) -> None:
        self._record("pass", password)

    def uidl(self) -> tuple[bytes, list[bytes], int]:
        self._record("uidl")
        return b"+OK", self.listing, 0

    def retr(self, number: int) -> tuple[bytes, list[bytes], int]:
        self._record("retr", number)
        return b"+OK", _RAW.split(b"\r\n"), 0

    def quit(self) -> None:
        self._record("quit")


class _PopTls(_Pop):
    pass


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    commands: list[tuple[Any, ...]] = []
    monkeypatch.setattr(_Stub, "log", commands, raising=False)
    monkeypatch.setattr(_Stub, "fail", {}, raising=False)
    monkeypatch.setattr(imaplib, "IMAP4", _Imap)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _ImapTls)
    monkeypatch.setattr(poplib, "POP3", _Pop)
    monkeypatch.setattr(poplib, "POP3_SSL", _PopTls)
    return commands


def _names(log: list[tuple[Any, ...]]) -> list[str]:
    return [entry[0] for entry in log]


def _connect(log: list[tuple[Any, ...]]) -> tuple[Any, ...]:
    return next(entry for entry in log if entry[0] == "connect")


# --- connection security --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "security", "port"),
    [
        ("imap", "tls", 993),
        ("imap", "starttls", 143),
        ("pop3", "tls", 995),
        ("pop3", "starttls", 110),
    ],
)
def test_default_port_follows_kind_and_security(
    log: list[tuple[Any, ...]], kind: str, security: str, port: int
) -> None:
    _reader(_cfg(kind=kind, security=security)).list_ids(5.0)
    assert _connect(log)[2:4] == ("mail.test", port)


def test_an_explicit_port_and_secret_bindings_are_used(log: list[tuple[Any, ...]]) -> None:
    cfg = _cfg(
        host="${secrets.host}",
        port=1993,
        username="${secrets.user}",
        password="${secrets.pass}",
        folder="My Folder",
    )
    bindings = {"secrets.host": "m.test", "secrets.user": "team", "secrets.pass": "hunter2"}
    _reader(cfg, bindings).list_ids(5.0)
    assert _connect(log)[2:4] == ("m.test", 1993)
    assert ("login", '"team"', "hunter2") in log
    assert ("select", '"My Folder"', True) in log  # quoted, and read-only


@pytest.mark.parametrize("kind", ["imap", "pop3"])
def test_tls_verifies_the_certificate_and_the_server_name(
    log: list[tuple[Any, ...]], kind: str
) -> None:
    _reader(_cfg(kind=kind)).list_ids(5.0)
    kwargs = _connect(log)[4]
    context = kwargs.get("ssl_context") or kwargs.get("context")
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


@pytest.mark.parametrize(
    ("kind", "upgrade", "login"), [("imap", "starttls", "login"), ("pop3", "stls", "user")]
)
def test_starttls_upgrades_before_any_credential(
    log: list[tuple[Any, ...]], kind: str, upgrade: str, login: str
) -> None:
    _reader(_cfg(kind=kind, security="starttls")).list_ids(5.0)
    names = _names(log)
    assert names.index(upgrade) < names.index(login)


@pytest.mark.parametrize("kind", ["imap", "pop3"])
def test_a_server_without_starttls_gets_no_credential(
    log: list[tuple[Any, ...]], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(_Imap, "capabilities", ("IMAP4REV1",))
    monkeypatch.setattr(_Pop, "capabilities", {})
    with pytest.raises(base.SelectorError, match="refusing clear text"):
        _reader(_cfg(kind=kind, security="starttls")).list_ids(5.0)
    assert not {"login", "user", "pass"} & set(_names(log))


def test_a_pop3_server_without_capa_counts_as_offering_no_stls(
    log: list[tuple[Any, ...]],
) -> None:
    _Stub.fail["capa"] = poplib.error_proto("-ERR")
    with pytest.raises(base.SelectorError, match="offers no STLS"):
        _reader(_cfg(kind="pop3", security="starttls")).list_ids(5.0)
    assert "user" not in _names(log)


# --- reading --------------------------------------------------------------------------------------


def test_imap_reads_by_uid_without_marking_mail_read(log: list[tuple[Any, ...]]) -> None:
    reader = _reader(_cfg())
    ids = reader.list_ids(5.0)
    assert ids == {"7:1", "7:2"}
    [message] = reader.fetch(ids, 5.0)  # uid 2 has vanished, so it is simply absent
    assert (message.id, message.to, message.subject) == ("7:1", ("u@x.io",), "Code")
    assert message.received_at == "2026-01-02T00:00:00+00:00"  # INTERNALDATE, not `Date`
    assert ("fetch", "1", "(INTERNALDATE BODY.PEEK[])") in log
    assert ("select", '"INBOX"', True) in log


def test_imap_ignores_an_id_from_a_rebuilt_folder(log: list[tuple[Any, ...]]) -> None:
    assert _reader(_cfg()).fetch({"6:1"}, 5.0) == []
    assert "fetch" not in _names(log)


def test_pop3_reads_by_unique_id_and_takes_the_date_header(log: list[tuple[Any, ...]]) -> None:
    reader = _reader(_cfg(kind="pop3"))
    assert reader.list_ids(5.0) == {"aaa", "bbb"}
    [message] = reader.fetch({"bbb", "gone"}, 5.0)
    assert (message.id, message.subject) == ("bbb", "Code")
    assert message.received_at == "2026-01-01T00:00:00+00:00"
    assert ("retr", 2) in log
    assert "dele" not in _names(log)


def test_each_call_opens_and_closes_its_own_connection(log: list[tuple[Any, ...]]) -> None:
    reader = _reader(_cfg(kind="pop3"))
    reader.list_ids(5.0)
    reader.list_ids(5.0)
    assert _names(log).count("connect") == 2 and _names(log).count("quit") == 2


# --- faults ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "command", "error"),
    [
        ("imap", "login", imaplib.IMAP4.error(b"bad password pw-s3cr3t")),
        ("pop3", "pass", poplib.error_proto("-ERR bad password pw-s3cr3t")),
    ],
)
def test_a_rejected_login_fails_without_quoting_the_password(
    log: list[tuple[Any, ...]], kind: str, command: str, error: Exception
) -> None:
    _Stub.fail[command] = error
    with pytest.raises(base.SelectorError, match="login rejected") as caught:
        _reader(_cfg(kind=kind)).list_ids(5.0)
    assert "pw-s3cr3t" not in str(caught.value)
    assert caught.value.__suppress_context__


def _certificate_error() -> ssl.SSLCertVerificationError:
    error = ssl.SSLCertVerificationError()
    error.verify_message = "Hostname mismatch"
    return error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConnectionRefusedError("refused"), "connection failed: refused"),
        (_certificate_error(), "certificate not accepted: Hostname mismatch"),
        (imaplib.IMAP4.abort("body line"), r"protocol error \(abort\)"),
        (UnicodeEncodeError("ascii", "é", 0, 1, "no"), "could not encode"),
        (KeyError(b"Foo"), "unreadable server response"),
        (TimeoutError("timed out"), "did not answer within"),
    ],
)
def test_a_transport_fault_becomes_a_step_error(
    log: list[tuple[Any, ...]], error: Exception, expected: str
) -> None:
    _Stub.fail["connect"] = error
    with pytest.raises(base.SelectorError, match=expected):
        _reader(_cfg()).list_ids(5.0)


@pytest.mark.parametrize(
    ("attribute", "value", "expected"),
    [
        ("select_status", "NO", "cannot open folder 'INBOX'"),
        ("uid_status", "NO", "rejected UID SEARCH"),
        ("validity", [None], "no UIDVALIDITY"),
    ],
)
def test_an_imap_refusal_fails_the_step(
    log: list[tuple[Any, ...]],
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    value: Any,
    expected: str,
) -> None:
    monkeypatch.setattr(_Imap, attribute, value)
    with pytest.raises(base.SelectorError, match=expected):
        _reader(_cfg()).list_ids(5.0)


def test_a_pop3_server_without_uidl_stops_the_step(log: list[tuple[Any, ...]]) -> None:
    _Stub.fail["uidl"] = poplib.error_proto("-ERR unknown command")
    with pytest.raises(base.SelectorError, match="rejected UIDL"):
        _reader(_cfg(kind="pop3")).list_ids(5.0)


def test_a_call_never_outlives_its_timeout(log: list[tuple[Any, ...]]) -> None:
    with pytest.raises(base.SelectorError, match="remaining timeout"):
        _reader(_cfg()).list_ids(0.0)
    assert log == []


def test_a_long_timeout_is_capped_per_socket_operation(log: list[tuple[Any, ...]]) -> None:
    _reader(_cfg()).list_ids(600.0)
    assert _connect(log)[4]["timeout"] == 30.0


def test_an_unset_secret_is_never_sent_to_a_server(log: list[tuple[Any, ...]]) -> None:
    reader = _reader(_cfg(password="${secrets.MAIL_PASSWORD}"))
    with pytest.raises(base.SelectorError, match=r"mailbox\.password references a secret"):
        reader.list_ids(5.0)
    assert log == []


def test_a_rebuilt_imap_folder_is_refused() -> None:
    reader = _reader(_cfg())
    reader.check_same_inbox(frozenset({"7:1"}), frozenset({"7:1", "7:2"}))
    reader.check_same_inbox(frozenset(), frozenset({"7:2"}))
    with pytest.raises(base.SelectorError, match="rebuilt"):
        reader.check_same_inbox(frozenset({"7:1"}), frozenset({"8:1"}))
    _reader(_cfg(kind="pop3")).check_same_inbox(frozenset({"a"}), frozenset({"b"}))


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ([b"1"], "unreadable server response"),
        ([b"x aaa"], "unreadable server response"),
        ([b"1 \xff"], "unreadable server response"),
        ([b"1 aaa", b"2 aaa"], "repeats an id"),
    ],
)
def test_a_bad_uidl_listing_fails_the_step(
    log: list[tuple[Any, ...]],
    monkeypatch: pytest.MonkeyPatch,
    listing: list[bytes],
    expected: str,
) -> None:
    monkeypatch.setattr(_Pop, "listing", listing)
    with pytest.raises(base.SelectorError, match=expected):
        _reader(_cfg(kind="pop3")).list_ids(5.0)


@pytest.mark.parametrize(("kind", "command"), [("imap", "login"), ("pop3", "user")])
def test_every_round_trip_gets_only_the_time_left(
    log: list[tuple[Any, ...]], monkeypatch: pytest.MonkeyPatch, kind: str, command: str
) -> None:
    monkeypatch.setattr(_Socket, "settimeout", lambda self, value: log.append(("arm", value)))
    _reader(_cfg(kind=kind)).list_ids(5.0)
    assert _names(log)[_names(log).index(command) - 1] == "arm"
    assert all(0 < entry[1] <= 5.0 for entry in log if entry[0] == "arm")


def test_an_imap_user_name_with_specials_is_quoted(log: list[tuple[Any, ...]]) -> None:
    _reader(_cfg(username='DOMAIN\\qa "bot"')).list_ids(5.0)
    assert ("login", '"DOMAIN\\\\qa \\"bot\\""', "pw-s3cr3t") in log


@pytest.mark.parametrize("setting", ["host", "username", "password", "folder"])
def test_a_line_break_in_a_setting_never_reaches_a_server(
    log: list[tuple[Any, ...]], setting: str
) -> None:
    reader = _reader(_cfg(**{setting: "${secrets.v}"}), {"secrets.v": "value\n"})
    with pytest.raises(base.SelectorError, match=rf"mailbox\.{setting} contains a line break"):
        reader.list_ids(5.0)
    assert log == []
