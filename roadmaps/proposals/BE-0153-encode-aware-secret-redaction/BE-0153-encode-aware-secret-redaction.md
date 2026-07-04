**English** · [日本語](BE-0153-encode-aware-secret-redaction-ja.md)

# BE-0153 — Encode-aware secret redaction

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0153](BE-0153-encode-aware-secret-redaction.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0153") |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

`Redactor` masks a known secret value by exact literal-string substitution, so any encoded form of
the same value — URL-encoded, Basic-auth base64, HTML/JSON-escaped, or split across chunks — passes
through unmasked. This item makes redaction encode-aware: it matches common encodings of a known
secret value, not just its literal bytes.

## Motivation

`Redactor.redact_text` (`bajutsu/redaction.py:63`) masks a literal secret value with
`text.replace(value, PLACEHOLDER)` — a straight substring replacement. This catches the exact
value verbatim in text, but nothing else, so several common transformations of the very same
secret slip through:

1. **Percent-encoding.** A secret passed as a URL query parameter or form-encoded body
   (`token=p%40ssw0rd`) does not contain the literal value `p@ssw0rd`; the encoded form is a
   different string and is never matched.
2. **`Basic base64(user:pass)`.** HTTP Basic auth sends
   `Authorization: Basic <base64(username:password)>`. The literal password never appears in the
   header text — only its base64-encoded, colon-joined form does — so the exact-match pass never
   touches it, and a captured `network.json` exchange can carry a trivially-decodable credential in full.
3. **HTML/JSON escaping.** A secret embedded in an HTML-escaped attribute (`&quot;`, `&amp;`) or a
   JSON-escaped string (`\"`, `\/`, `\uXXXX`) is not byte-identical to the raw value, so it is not
   matched either.
4. **Values split across chunks.** Evidence assembled from multiple reads/chunks (e.g. a streamed
   body, or text built up incrementally) can have the secret value's bytes land across a chunk
   boundary the substitution never sees as one contiguous string.

Each of these is a realistic way a genuine secret (a password, token, or API key bound via
`${secrets.*}`, BE-0032) ends up verbatim, just differently encoded, in evidence that is written to
disk and — for the AI authoring/investigation paths — sent to the configured AI provider (BE-0047).
An author who correctly bound `secrets:` and reasonably expects it to keep the value out of
evidence is only protected against the one encoding the tool happens to string-match.

## Detailed design

1. **Match common encodings of each known secret value**, not just the literal string, in
   `Redactor.redact_text` and `redact_exchange`:
   - Percent-encoding (`urllib.parse.quote` of the value, in a couple of common safe-char
     configurations, since encoders vary in what they leave unescaped).
   - Basic-auth base64: for each secret value that could be a Basic-auth password (or the full
     `user:pass` pair when both are known secrets), also mask
     `base64(...)` forms found after `Authorization: Basic `.
   - HTML-escaped and JSON-escaped forms of the value (`html.escape`, `json.dumps` minus the
     surrounding quotes) wherever the literal text search already looks.
   - This stays a fixed, enumerable set of transforms applied to *known* secret values (not a
     general decode-everything scan), so the cost and false-positive surface stay bounded.
2. **Do not weaken key-based pattern matching.** The header/field key patterns (`_patterns`,
   `bajutsu/redaction.py:21`) are unaffected; this item only extends how a literal *value* is
   recognized once the key or Basic-auth context locates roughly where it lives.
3. **Accept that chunk-boundary splitting is a best-effort mitigation, not a guarantee.** Where
   evidence is assembled before redaction runs (e.g. a full body string rather than a live stream),
   redaction already sees the complete text and this is a non-issue; where genuinely fragmented
   evidence exists, note the limitation rather than claim complete coverage.
4. **Tests.** A secret value redacted correctly when percent-encoded in a URL/query, when carried
   in a `Basic base64(...)` header, and when HTML/JSON-escaped in a body.
5. **Docs.** Note the encoding-aware matching and its known limits (e.g. genuinely fragmented
   streaming evidence) in the redaction docs (`docs/` and `docs/ja/`).

The default-header-masking gap (which headers are redacted at all) is a separate, sibling item
(default network secret redaction) — this item is scoped to how a known secret *value* is matched,
regardless of which headers/fields triggered the search for it. Nothing here touches the
deterministic `run`/CI gate or introduces an LLM; this is a pure text-transform change inside the
evidence-writing path.

## Alternatives considered

- **Decode every string found in evidence and compare against secret values.** Rejected as too
  broad and too slow: decoding arbitrary text against every possible encoding is expensive and
  produces false positives (garbage that happens to decode to something). Matching known *encodings
  of known values* (the reverse direction — encode the secret, search for that) is cheaper and
  precise.
- **Require authors to redact by header/field name only, dropping value-based matching entirely.**
  Rejected: value-based matching (BE-0032's `values` list) exists specifically to catch a secret
  the app echoes somewhere the author didn't anticipate (a log line, a response body); removing it
  would regress that protection rather than fix its encoding gap.
- **Leave this to the AI data sovereignty warning (screenshot-secret-capture-warning) instead of
  fixing the matcher.** Rejected: that item covers image evidence, which genuinely cannot be
  pixel-redacted; text evidence *can* be matched correctly, so the fix belongs in the matcher, not
  in a disclosure.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Percent-encoded forms of known secret values matched
- [ ] Basic-auth base64 forms of known secret values matched
- [ ] HTML/JSON-escaped forms of known secret values matched
- [ ] Chunk-boundary limitation documented where full-text redaction cannot see a fragmented value
- [ ] Tests: percent-encoding, Basic-auth base64, HTML/JSON escaping all correctly masked
- [ ] Docs updated (both languages)

No PR has landed yet.

## References

- `bajutsu/redaction.py:63` — `redact_text`'s literal `text.replace(value, PLACEHOLDER)`.
- `bajutsu/redaction.py:21` — `_patterns`, the key-based matching this item does not change.
- [BE-0032 — Secret variables](../../BE-0032-secret-variables/BE-0032-secret-variables.md)
- [BE-0047 — AI data sovereignty](../../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
- Originates from the 2026-07-02 codebase-analysis report (security).
