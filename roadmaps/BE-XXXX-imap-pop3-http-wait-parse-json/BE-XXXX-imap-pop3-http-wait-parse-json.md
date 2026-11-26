**English** · [日本語](BE-XXXX-imap-pop3-http-wait-parse-json-ja.md)

# BE-XXXX — Receive mail over IMAP and POP3, wait on an HTTP status, and parse JSON text

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-imap-pop3-http-wait-parse-json.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md), [BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md), [BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction.md), [BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations.md) |
<!-- /BE-METADATA -->

## Introduction

The `email` step reads mail from a Hyper Text Transfer Protocol (HTTP) endpoint. The endpoint answers
with Java Script Object Notation (JSON). This item changes where mail comes from. It also changes how
a scenario reads an HTTP response. The four changes are:

| Change | Result |
|---|---|
| Mail transport | `email` reads a mailbox over Internet Message Access Protocol (IMAP) or Post Office Protocol version 3 (POP3). The HTTP mailbox goes away. |
| HTTP wait | The `http` step can repeat a request until the response carries the expected status. |
| JSON parsing | A new `parseJson` step reads values out of any JSON text by path. |
| `extractBody` | The option leaves the `http` step, and `parseJson` takes over its job. |

Two of the changes break existing files. A `mailbox:` block with a Uniform Resource Locator (URL)
stops loading. So does a scenario that uses `extractBody`.

## Motivation

The current mailbox accepts JSON alone. The runner parses every response as JSON. A plain-text
answer thus breaks the step on its first fetch.
 Services also differ in the JSON they
return. The `messages` and `fields` settings absorb different key names. They cannot absorb a
different response shape.

An HTTP service is already within reach of the `http` step. A second HTTP reader inside `email`
duplicates that step and still covers fewer services. Mail itself has two widely served retrieval
protocols, IMAP and POP3.

[BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) deferred
IMAP until demand appeared.
[BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md) built the
registry for a new transport.

The `http` step cannot replace the HTTP mailbox today, because it sends one request and moves on.
A value that arrives seconds later stays out of reach.

JSON reading has a related limit.
[BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction.md) reads fields
from a response body inside the `http` step alone. Text held in a variable has no such reader. The
`json` operation of the `eval` step turns a JSON array into a list and cannot read inside an object.

Two scenarios that cannot pass today show that the change arrived. One reads a code from a mailbox
over IMAP or POP3. The other waits for an HTTP service. It then reads a nested value from the saved
text.

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

Every connection uses Transport Layer Security (TLS) and verifies the server certificate. The item
offers no unencrypted mode. Authentication uses a user name and a password. A `folder` under
`kind: pop3` is a configuration error, because POP3 has a single inbox.

The settings `url`, `headers`, `messages`, and `fields` go away.

A block that still carries one of
them stops loading. The message names IMAP, POP3, and the `http` step. The registry from
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

The step fails on a connection error, a rejected login, or a certificate error. The error message
never quotes the password or the message body.

### Waiting on an HTTP status

The `http` step gains an optional `timeout`, in seconds:

```yaml
- http:
    url: "${secrets.API}/codes/latest"
    status: 200
    timeout: 30
    saveBody: response
```

With `timeout`, the step repeats the request about once a second. It stops when the response
status equals `status`.

The rules are:

- `timeout` without `status` is a scenario load error.
- `timeout` requires the `GET` or `HEAD` method, because repeating another method can create data
  twice.
- A different status leads to the next attempt. A connection error fails the step at once.
- Each request may take the remaining time, up to 30 seconds.
- `saveBody` stores the body of the successful response.
- At the deadline the step fails and reports the last status it saw.
- A cancelled run leaves the wait, like every other condition wait.

Without `timeout`, the step behaves as it does today. The status is the sole success condition. A
service that answers 200 with an empty body before the data exists cannot use this wait.

The wait also has no notion of new data, unlike `email`.

A scenario avoids a stale value by asking
for something unique to the run. The `generate` step can build such an address.

### The `parseJson` step

```yaml
- parseJson:
    from: "${vars.response}"
    extract:
      - { var: code, path: "data.items[0].code" }
      - { var: userId, path: "data.user.id" }
```

`from` is a string with the usual `${...}` interpolation. `extract` is a non-empty list of
`{ var, path }` entries. The step takes its rules from `extractBody`:

- The path grammar stays the same: object keys and zero-based `[n]` indexes.
- The step stores a string value as it stands. Any other value becomes compact JSON text.
- Text that is not JSON, a malformed path, or an unresolved path fails the step.
- All entries resolve together. One bad entry leaves every variable unchanged.
- Two entries with one `var` are a load error, checked again after interpolation.
- A substituted path value fills one segment and cannot carry `.`, `[`, or `]`.

Two rules come from the `eval` step
([BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations.md)). Error
messages never quote the text or a substituted value. A value read from a secret input counts as a
secret. Later artifacts mask it. The text in `from` shares the `eval` input limit of 1,048,576
characters.

### Removing `extractBody`

`extractBody` leaves the `http` step. A scenario that still uses it stops loading, with a message
that points to `saveBody` plus `parseJson`:

```yaml
- http: { url: "${secrets.API}/login", status: 200, saveBody: login }
- parseJson:
    from: "${vars.login}"
    extract:
      - { var: token, path: "data.token" }
```

### Prime directives

| Directive | How the design keeps it |
|---|---|
| No model on the verdict path | Mail matching, status comparison, and path resolution are fixed rules. |
| Determinism | Both waits end on a condition or a required deadline. |
| App-agnostic | Server and credentials stay in `targets.<name>`, and scenarios name neither. |

### Work breakdown

| # | Work | Main files | Done when | Depends on |
|---|---|---|---|---|
| 1 | Add the `parseJson` model, handler, load check, and secret marking | `bajutsu/common/scenario/`, `bajutsu/common/orchestrator/` | Unit tests cover the rules above | none |
| 2 | Remove `extractBody` and move its tests to `parseJson` | `http_request.py`, `_http_extract.py`, `handlers/http.py`, `substitution.py` | An old scenario stops loading with the migration message | 1 |
| 3 | Add the status wait to `http` | `http_request.py`, the step dispatch in `orchestrator/loop/` | Tests with a stub server and clock cover success, deadline, and cancellation | none |
| 4 | Replace the message model and the reader interface | `bajutsu/common/mailbox.py`, `mailbox_reader.py`, `_do_email` | Parsing and selection tests pass on raw mail fixtures | none |
| 5 | Add the IMAP and POP3 adapters and the new settings, and remove the HTTP reader | `runner/mailbox.py`, `config/schema/mailbox.py` | Tests with stubbed `imaplib` and `poplib` pass, and an old block stops loading | 4 |
| 6 | Update the documents in both languages | `docs/scenarios.md`, `docs/configuration.md`, `docs/dsl-grammar.md`, `docs/architecture.md` | No page describes the removed settings | 2, 3, 5 |
| 7 | Check the adapters against a real test mail server | none | One `email` step passes over IMAP and one over POP3 | 5 |

The gate cannot reach a mail server, so the last unit is a manual check.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Keep the HTTP mailbox and add a plain-text format | Response shapes differ by service, so the reader would keep growing format settings. |
| Drop `email` and use `http` for all mail | A mailbox that speaks IMAP or POP3 alone would stay unreachable. |
| Keep `kind: http` beside IMAP and POP3 | Two paths would serve HTTP services, and the JSON mapping settings would remain. |
| A general retry block around several steps | The block could repeat interface actions, which widens the language far past the need. |
| Wait until the body matches a pattern | The request asked for the status alone. A later item can add the condition. |
| Add a path operation to `eval` | A named step reads better for several values at once and keeps the `{ var, path }` form. |
| Keep `extractBody` beside `parseJson` | Two ways to do one job would need the same rules maintained twice. |
| Support OAuth 2.0 for mail login | Token issue and renewal form a feature of their own. A server that requires OAuth stays unsupported. |
| Allow unencrypted mail connections | Credentials would cross the network in clear text. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `parseJson` step
- [ ] Removal of `extractBody`
- [ ] Status wait in `http`
- [ ] Message model and reader interface
- [ ] IMAP and POP3 adapters, new settings, and removal of the HTTP reader
- [ ] Documents in both languages
- [ ] Manual check against a mail server

## References

| Item | Why it matters here |
|---|---|
| [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) | The origin of the `email` step and the HTTP mailbox |
| [BE-0186](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md) | The registry for the new adapters |
| [BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction.md) | The path grammar that `parseJson` keeps |
| [BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations.md) | The rules for secret values and error messages |
