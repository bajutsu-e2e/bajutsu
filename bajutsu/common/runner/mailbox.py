"""Build the injected `MailboxReader` for the `email` step, via a transport registry (BE-0186).

The deterministic parse/match/selection logic is pure (`bajutsu.common.mailbox`); this is the only
place that touches the network. The registry is keyed on transport `kind`, so adding one is *register
an adapter*, not *branch the runner*; it ships `imap` and `pop3` on the standard library (BE-0514).
Every call opens its own connection, because a POP3 connection shows a fixed snapshot of the inbox.
Neither adapter deletes mail or marks it as read.

Error messages name what failed without ever quoting the password or message content.
"""

from __future__ import annotations

import imaplib
import poplib
import re
import ssl
import time
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bajutsu.common.config import Mailbox
from bajutsu.common.drivers import base
from bajutsu.common.mailbox import MailboxMessage, date_header, parse_message
from bajutsu.common.orchestrator import MailboxReader, MailboxTimeout
from bajutsu.common.scenario import interp

# An adapter builds a `MailboxReader` for its transport from the resolved mailbox config and the run's
# secret bindings. Keyed on transport, never on vendor.
MailboxAdapter = Callable[[Mailbox, Mapping[str, str]], MailboxReader]

_ADAPTERS: dict[str, MailboxAdapter] = {}

_DEFAULT_PORTS = {
    ("imap", "tls"): 993,
    ("imap", "starttls"): 143,
    ("pop3", "tls"): 995,
    ("pop3", "starttls"): 110,
}
# One socket operation never waits longer than this, however long the step's own timeout is.
_MAX_SOCKET_TIMEOUT = 30.0
# Saying goodbye must not extend a call whose budget is spent.
_MAX_CLOSE_TIMEOUT = 5.0
# POP3 reads a whole message with `TOP n <this many body lines>` rather than `RETR`: a server that
# shares flags with IMAP marks a `RETR` message read, and the email step must leave mail unread
# (BE-0514). Servers support TOP without always listing it under CAPA, so the adapter tries it.
_WHOLE_MESSAGE = 1_000_000_000


def register(kind: str, adapter: MailboxAdapter) -> None:
    """Register *adapter* under the transport *kind* (idempotent — a later call overrides)."""
    _ADAPTERS[kind] = adapter


@dataclass(frozen=True)
class _Server:
    """The resolved connection settings both adapters share."""

    protocol: str
    host: str
    port: int
    starttls: bool
    username: str
    password: str
    unset: tuple[str, ...] = ()
    multiline: tuple[str, ...] = ()


def _unset(**settings: str) -> tuple[str, ...]:
    """The settings still holding a `${secrets.*}` token no binding filled."""
    return tuple(name for name, value in settings.items() if "${secrets." in value)


def _multiline(**settings: str) -> tuple[str, ...]:
    """The settings holding a line break, which would split a protocol command in two."""
    return tuple(name for name, value in settings.items() if "\r" in value or "\n" in value)


def _server(cfg: Mailbox, bindings: Mapping[str, str]) -> _Server:
    """Resolve `${secrets.*}` from the same bindings the steps use, and fill the default port."""
    host = str(interp.interpolate(cfg.host, bindings))
    username = str(interp.interpolate(cfg.username, bindings))
    password = str(interp.interpolate(cfg.password, bindings))
    folder = str(interp.interpolate(cfg.folder or "", bindings))
    settings = {"host": host, "username": username, "password": password, "folder": folder}
    return _Server(
        protocol=cfg.kind.upper(),
        host=host,
        port=cfg.port or _DEFAULT_PORTS[(cfg.kind, cfg.security)],
        starttls=cfg.security == "starttls",
        username=username,
        password=password,
        unset=_unset(**settings),
        multiline=_multiline(**settings),
    )


def _failure(server: _Server, what: str) -> base.SelectorError:
    return base.SelectorError(f"email: {server.protocol} {what}")


def _timed_out(server: _Server) -> MailboxTimeout:
    return MailboxTimeout(
        f"email: {server.protocol} did not answer within the step's remaining timeout"
    )


@contextmanager
def _faults(server: _Server) -> Iterator[None]:
    """Turn a transport fault into a step error.

    A protocol error's text can echo server data (a credential or a message line), so only its type
    is reported; `from None` keeps it out of a traceback too. Socket and certificate errors carry no
    such data and say what to fix.
    """
    try:
        yield
    except ssl.SSLCertVerificationError as e:
        raise _failure(server, f"certificate not accepted: {e.verify_message}") from None
    except TimeoutError:
        raise _timed_out(server) from None
    except OSError as e:
        raise _failure(server, f"connection failed: {e}") from None
    except (imaplib.IMAP4.error, poplib.error_proto) as e:
        raise _failure(server, f"protocol error ({type(e).__name__})") from None
    except UnicodeEncodeError:
        raise _failure(server, "could not encode a configured value for the server") from None
    except (ValueError, KeyError, IndexError, OverflowError):
        # A response the stdlib or this adapter can't read; unguarded, it would end the whole run.
        raise _failure(server, "unreadable server response") from None


class _Deadline:
    """The budget of one `list_ids` / `fetch` call, spread over its several round trips.

    Raises:
        base.SelectorError: A configured secret was never set or holds a line break, so no
            connection is attempted.
    """

    def __init__(self, server: _Server, timeout: float) -> None:
        if server.unset:
            names = ", ".join(f"mailbox.{name}" for name in server.unset)
            raise _failure(server, f"{names} references a secret that is not set")
        if server.multiline:
            names = ", ".join(f"mailbox.{name}" for name in server.multiline)
            raise _failure(server, f"{names} contains a line break")
        self._server = server
        self._end = time.monotonic() + timeout

    def socket_timeout(self) -> float:
        """The time left for the next socket operation.

        Raises:
            MailboxTimeout: The budget is spent. A partial inbox is never returned, because the
                newest match could be in the part that was skipped.
        """
        left = self._end - time.monotonic()
        if left <= 0:
            raise _timed_out(self._server)
        return min(left, _MAX_SOCKET_TIMEOUT)

    def arm(self, conn: imaplib.IMAP4 | poplib.POP3) -> None:
        """Give the next round trip only the time that is still left."""
        conn.sock.settimeout(self.socket_timeout())

    def arm_close(self, conn: imaplib.IMAP4 | poplib.POP3) -> None:
        """Bound the goodbye without raising, since it runs in a `finally`."""
        left = self._end - time.monotonic()
        conn.sock.settimeout(max(0.1, min(left, _MAX_CLOSE_TIMEOUT)))


# --- IMAP -----------------------------------------------------------------------------------------

_INTERNALDATE = re.compile(rb'INTERNALDATE "[^"]*"')


def _imap_quote(value: str) -> str:
    """An IMAP quoted string.

    `imaplib` sends mailbox and user names unquoted, so a space or a backslash would corrupt one.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _imap_message(message_id: str, data: list[Any]) -> MailboxMessage | None:
    """Read a `UID FETCH (INTERNALDATE BODY.PEEK[])` response; None if the message is gone."""
    literal = next((d for d in data if isinstance(d, tuple) and b"BODY[]" in d[0]), None)
    if literal is None:
        return None
    # The server may put INTERNALDATE before or after the literal.
    framing = b" ".join(d[0] if isinstance(d, tuple) else d for d in data if d is not None)
    found = _INTERNALDATE.search(framing)
    when = imaplib.Internaldate2tuple(found.group(0)) if found else None
    received = datetime.fromtimestamp(time.mktime(when), UTC) if when else None
    return parse_message(literal[1], message_id=message_id, received=received)


class _ImapMailbox:
    def __init__(self, server: _Server, folder: str) -> None:
        self._server = server
        self._folder = folder

    @contextmanager
    def _session(self, deadline: _Deadline) -> Iterator[tuple[imaplib.IMAP4, str]]:
        """A logged-in connection with the folder open read-only, plus its `UIDVALIDITY`."""
        server = self._server
        with _faults(server):
            context = ssl.create_default_context()
            if server.starttls:
                conn: imaplib.IMAP4 = imaplib.IMAP4(
                    server.host, server.port, timeout=deadline.socket_timeout()
                )
            else:
                conn = imaplib.IMAP4_SSL(
                    server.host,
                    server.port,
                    ssl_context=context,
                    timeout=deadline.socket_timeout(),
                )
            try:
                if server.starttls:
                    # No credential leaves before the upgrade succeeds, and there is no fallback.
                    if "STARTTLS" not in conn.capabilities:
                        raise _failure(server, "server offers no STARTTLS; refusing clear text")
                    deadline.arm(conn)
                    conn.starttls(ssl_context=context)
                deadline.arm(conn)
                try:
                    conn.login(_imap_quote(server.username), server.password)
                except imaplib.IMAP4.error:
                    raise _failure(server, "login rejected") from None
                deadline.arm(conn)
                status, _ = conn.select(_imap_quote(self._folder), readonly=True)
                if status != "OK":
                    raise _failure(server, f"cannot open folder {self._folder!r}")
                validity = conn.response("UIDVALIDITY")[1]
                if not validity or validity[0] is None:
                    raise _failure(server, "server reported no UIDVALIDITY")
                yield conn, validity[0].decode("ascii")
            finally:
                with suppress(OSError, imaplib.IMAP4.error):
                    deadline.arm_close(conn)
                    conn.logout()

    def _uid(self, conn: imaplib.IMAP4, deadline: _Deadline, *args: str) -> list[Any]:
        deadline.arm(conn)
        status, data = conn.uid(*args)
        if status != "OK":
            raise _failure(self._server, f"rejected UID {args[0]}")
        return data

    def list_ids(self, timeout: float) -> frozenset[str]:
        deadline = _Deadline(self._server, timeout)
        with self._session(deadline) as (conn, validity):
            data = self._uid(conn, deadline, "SEARCH", "ALL")
            return frozenset(
                f"{validity}:{uid.decode('ascii')}" for uid in (data[0] or b"").split()
            )

    def fetch(self, ids: Collection[str], timeout: float) -> list[MailboxMessage]:
        deadline = _Deadline(self._server, timeout)
        out: list[MailboxMessage] = []
        with self._session(deadline) as (conn, validity):
            for message_id in sorted(ids):
                prefix, _, uid = message_id.partition(":")
                if prefix != validity:  # the folder was rebuilt, so the UID names something else
                    continue
                data = self._uid(conn, deadline, "FETCH", uid, "(INTERNALDATE BODY.PEEK[])")
                message = _imap_message(message_id, data)
                if message is not None:
                    out.append(message)
        return out

    def check_same_inbox(self, baseline: frozenset[str], current: frozenset[str]) -> None:
        # An id carries the folder's UIDVALIDITY; a second value means the folder was rebuilt.
        if len({message_id.partition(":")[0] for message_id in baseline | current}) > 1:
            raise _failure(
                self._server,
                "folder was rebuilt during the step, so new mail can't be told from old",
            )


def _build_imap_reader(cfg: Mailbox, bindings: Mapping[str, str]) -> MailboxReader:
    folder = str(interp.interpolate(cfg.folder or "INBOX", bindings))
    return _ImapMailbox(_server(cfg, bindings), folder)


# --- POP3 -----------------------------------------------------------------------------------------


class _Pop3Mailbox:
    def __init__(self, server: _Server) -> None:
        self._server = server

    @contextmanager
    def _session(self, deadline: _Deadline) -> Iterator[poplib.POP3]:
        server = self._server
        with _faults(server):
            context = ssl.create_default_context()
            if server.starttls:
                conn = poplib.POP3(server.host, server.port, timeout=deadline.socket_timeout())
            else:
                conn = poplib.POP3_SSL(
                    server.host,
                    server.port,
                    context=context,
                    timeout=deadline.socket_timeout(),
                )
            try:
                if server.starttls:
                    # No credential leaves before the upgrade succeeds, and there is no fallback.
                    deadline.arm(conn)
                    try:
                        offered = "STLS" in conn.capa()
                    except poplib.error_proto:
                        offered = False
                    if not offered:
                        raise _failure(server, "server offers no STLS; refusing clear text")
                    deadline.arm(conn)
                    conn.stls(context=context)
                try:
                    deadline.arm(conn)
                    conn.user(server.username)
                    deadline.arm(conn)
                    conn.pass_(server.password)
                except poplib.error_proto:
                    raise _failure(server, "login rejected") from None
                yield conn
            finally:
                with suppress(OSError, poplib.error_proto):
                    deadline.arm_close(conn)
                    conn.quit()

    def _numbers(self, conn: poplib.POP3, deadline: _Deadline) -> dict[str, int]:
        """The session's unique-id listing, as `uid -> message number`."""
        deadline.arm(conn)
        try:
            lines: list[bytes] = conn.uidl()[1]  # no argument, so a whole listing
        except poplib.error_proto:
            raise _failure(
                self._server, "server rejected UIDL, which the email step needs"
            ) from None
        numbers: dict[str, int] = {}
        for line in lines:
            number, uid = line.decode("ascii").split()  # ValueError becomes a step error
            if uid in numbers:
                raise _failure(self._server, "UIDL listing repeats an id")
            numbers[uid] = int(number)
        return numbers

    def list_ids(self, timeout: float) -> frozenset[str]:
        deadline = _Deadline(self._server, timeout)
        with self._session(deadline) as conn:
            return frozenset(self._numbers(conn, deadline))

    def fetch(self, ids: Collection[str], timeout: float) -> list[MailboxMessage]:
        deadline = _Deadline(self._server, timeout)
        out: list[MailboxMessage] = []
        with self._session(deadline) as conn:
            numbers = self._numbers(conn, deadline)
            use_top = True
            for message_id in sorted(set(ids) & numbers.keys()):
                number = numbers[message_id]
                lines: list[bytes] | None = None
                if use_top:
                    deadline.arm(conn)
                    try:
                        lines = conn.top(number, _WHOLE_MESSAGE)[1]
                    except poplib.error_proto as e:
                        # Only a `-ERR` status (bytes) means the server lacks this optional command.
                        # poplib's str errors ("line too long", EOF) leave the stream mid-response,
                        # where a RETR would misread the leftover lines.
                        if not (
                            e.args
                            and isinstance(e.args[0], bytes)
                            and e.args[0].startswith(b"-ERR")
                        ):
                            raise
                        use_top = False
                if lines is None:
                    deadline.arm(conn)
                    lines = conn.retr(number)[1]
                raw = b"\r\n".join(lines)
                out.append(parse_message(raw, message_id=message_id, received=date_header(raw)))
        return out

    def check_same_inbox(self, baseline: frozenset[str], current: frozenset[str]) -> None:
        """A POP3 unique id is never reused, so listings always compare."""


def _build_pop3_reader(cfg: Mailbox, bindings: Mapping[str, str]) -> MailboxReader:
    return _Pop3Mailbox(_server(cfg, bindings))


# --- resolution -----------------------------------------------------------------------------------


def _ensure_builtins() -> None:
    """Register the built-in adapters on first use (`setdefault` leaves a test override intact)."""
    _ADAPTERS.setdefault("imap", _build_imap_reader)
    _ADAPTERS.setdefault("pop3", _build_pop3_reader)


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
