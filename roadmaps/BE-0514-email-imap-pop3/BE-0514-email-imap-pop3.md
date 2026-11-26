**English** · [日本語](BE-0514-email-imap-pop3-ja.md)

# BE-0514 — Receive mail for the email step over IMAP and POP3

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0514](BE-0514-email-imap-pop3.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0514") |
| Implementing PR | [#2581](https://github.com/bajutsu-e2e/bajutsu/pull/2581) |
| Topic | Scenario authoring features |
| Related | [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md), [BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md) |
<!-- /BE-METADATA -->

## Introduction

The `email` step reads mail from a Hypertext Transfer Protocol (`HTTP`) endpoint. The endpoint answers
with JavaScript Object Notation (`JSON`). This item replaces that transport. The step reads mail over
Internet Message Access Protocol (IMAP). It also supports Post Office Protocol version 3 (POP3).
The `HTTP` mailbox goes away.

The change breaks existing files. A `mailbox:` block with a Uniform Resource Locator (URL) stops
loading.

## Motivation

The current mailbox accepts `JSON` alone. The runner parses every response as `JSON`. A plain-text
answer thus breaks the step on its first fetch.

Services also differ in the `JSON` they return. The `messages` and `fields` settings absorb different
key names. They cannot absorb a different response shape.

An `HTTP` service is already within reach of the `http` step. A second `HTTP` reader inside `email`
duplicates that step and still covers fewer services. Mail itself has two widely served retrieval
protocols, IMAP and POP3.

[BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) deferred IMAP until demand appeared.
[BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md) built the
registry for a new transport.

We can tell that the change arrived when a scenario reads a code from a mailbox over IMAP or POP3.

## Detailed design

### Mailbox configuration

`targets.<name>.services.mailbox` describes a mail server:

```yaml
services:
  mailbox:
    kind: imap                          # required: imap or pop3
    host: "${secrets.MAIL_HOST}"
    port: 993                           # optional
    security: tls                       # tls (default) or starttls
    username: "${secrets.MAIL_USER}"
    password: "${secrets.MAIL_PASSWORD}"
    folder: INBOX                       # imap only; default INBOX
```

The default port follows from `kind` and `security`:

| `kind` | `tls` | `starttls` |
|---|---|---|
| `imap` | 993 | 143 |
| `pop3` | 995 | 110 |

Every connection uses Transport Layer Security (TLS). It verifies the server certificate and the
server name on it. The item offers no unencrypted mode.

Under `security: starttls`, the adapter sends no credential before the upgrade succeeds. The step
stops when the server offers neither `STARTTLS` (IMAP) nor `STLS` (POP3). It never continues in
clear text.

Authentication uses a user name and a password. A `folder` under `kind: pop3` is a configuration
error, because POP3 has a single inbox.

The settings `url`, `headers`, `messages`, and `fields` go away.

A block that still carries one of
them stops loading. The message names IMAP, POP3, and the `http` step.

A team that reads an `HTTP` mailbox today moves to the `http` step. Mail that arrives late also needs
a status wait on that step, which a separate proposal adds. That proposal should ship no later than
this item. The registry from
BE-0186 stays, and its built-in adapters become `imap` and `pop3`. Both adapters use the Python
standard library, so the item adds no dependency.

### The `email` step

The scenario syntax stays the same: `match`, `extract`, and a required `timeout`. The step still
considers mail that arrives after it starts, and the newest match still wins.

The reader interface splits in two, so a poll does not download the whole mailbox each time:

```python
class MailboxReader(Protocol):
    def list_ids(self, timeout: float) -> frozenset[str]: ...
    def fetch(self, ids: Collection[str], timeout: float) -> list[MailboxMessage]: ...
```

The handler records the identifiers present at the start. Each poll lists the identifiers again and
fetches the unseen ones. A message that does not match joins the seen set. The step thus downloads it
once. Each call opens its own connection, because one POP3 connection shows a fixed snapshot.

| Concern | IMAP | POP3 |
|---|---|---|
| Message identifier | `UIDVALIDITY` and the unique identifier of the message | the unique-id listing, `UIDL` |
| Reading a message | `BODY.PEEK[]`, with the folder opened read-only | `RETR` |
| Receive time | `INTERNALDATE` | the `Date` header |
| Server without the identifier command | not applicable | the step stops with an error |

Neither adapter deletes mail or marks it as read.

A pure function in `bajutsu/common/mailbox.py` turns the raw message into a `MailboxMessage`:

- `to` holds every address in the `To` header. `match.to` succeeds when one address equals it,
  ignoring letter case.
- `subject` is the decoded header text.
- `body` joins the `text/plain` parts that are not attachments. A message without such a part
  yields the source of its `text/html` parts.
- `received_at` holds the time in coordinated universal time, written in the `ISO 8601` form.
- A message without a usable date has an empty `received_at`.
- Two or more eligible matches can include an undated one. The step then fails, because the newest
  match is unknown. It never chooses by identifier.

The step fails on a connection error, a rejected login, or a certificate error. The error message
never quotes the password or the message body.

### Prime directives

| Directive | How the design keeps it |
|---|---|
| No model on the verdict path | Mail matching and value extraction are fixed rules. |
| Determinism | The wait ends on a matching message or the required deadline. |
| App-agnostic | Server and credentials stay in `targets.<name>`, and scenarios name neither. |

### Work breakdown

| # | Work | Main files | Done when | Depends on |
|---|---|---|---|---|
| 1 | Replace the message model, the reader interface, and the selection rule | `bajutsu/common/mailbox.py`, `mailbox_reader.py`, `_do_email` | Parsing and selection tests pass on raw mail fixtures, undated mail included | none |
| 2 | Add the IMAP and POP3 adapters and the new settings, and remove the `HTTP` reader | `runner/mailbox.py`, `config/schema/mailbox.py` | Tests with stubbed `imaplib` and `poplib` pass, and an old block stops loading | 1 |
| 3 | Update the documents in both languages | `docs/scenarios.md`, `docs/configuration.md`, `docs/architecture.md` | No page describes the removed settings | 2 |
| 4 | Check the adapters against a real test mail server | none | One `email` step passes over IMAP and one over POP3 | 2 |

The gate cannot reach a mail server, so the last unit is a manual check.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Keep the `HTTP` mailbox and add a plain-text format | Response shapes differ by service, so the reader would keep growing format settings. |
| Drop `email` and use `http` for all mail | A mailbox that speaks IMAP or POP3 alone would stay unreachable. |
| Keep `kind: http` beside IMAP and POP3 | Two paths would serve `HTTP` services, and the `JSON` mapping settings would remain. |
| Support OAuth 2.0 for mail login | Token issue and renewal form a feature of their own. A server that requires OAuth stays unsupported. |
| Allow unencrypted mail connections | Credentials would cross the network in clear text. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Message model and reader interface
- [x] IMAP and POP3 adapters, new settings, and removal of the `HTTP` reader
- [x] Documents in both languages
- [ ] Manual check against a mail server

Log:

- [#2581](https://github.com/bajutsu-e2e/bajutsu/pull/2581): units 1 to 3 landed together. The change removes the `HTTP`
  mailbox, and the manual check is still open. BE-0515,
  which this design wants no later than this item, has not shipped yet.
- Review added two stricter rules. Matches that share the newest receive time stop the step. The
  reader also gained `check_same_inbox`, which stops the step when an IMAP folder is rebuilt.

## References

| Item | Why it matters here |
|---|---|
| [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) | The origin of the `email` step and the `HTTP` mailbox |
| [BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md) | The registry for the new adapters |
