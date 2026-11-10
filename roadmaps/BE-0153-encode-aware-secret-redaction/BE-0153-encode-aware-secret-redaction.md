**English** · [日本語](BE-0153-encode-aware-secret-redaction-ja.md)

# BE-0153 — Encode-aware secret redaction

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0153](BE-0153-encode-aware-secret-redaction.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0153") |
| Implementing PR | [#634](https://github.com/bajutsu-e2e/bajutsu/pull/634) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

`Redactor` masks a known secret value by exact literal-string substitution. Any encoded form of the
same value passes through unmasked: URL-encoded, Basic-auth base64, HTML/JSON-escaped, or split
across chunks. This item makes redaction encode-aware. It matches common encodings of a known
secret value, not just its literal bytes.

## Motivation

`Redactor.redact_text` (`bajutsu/redaction.py:63`) masks a literal secret value with
`text.replace(value, PLACEHOLDER)`, a straight substring replacement. This catches the exact value
verbatim in text and nothing else. Several common transformations of the very same secret slip
through:

1. **Percent-encoding.** A secret passed as a URL query parameter or form-encoded body
   (`token=p%40ssw0rd`) does not contain the literal value `p@ssw0rd`. The encoded form is a
   different string, so the substitution never matches it.
2. **`Basic base64(user:pass)`.** HTTP Basic auth sends
   `Authorization: Basic <base64(username:password)>`. The literal password never appears in the
   header text. Its base64-encoded, colon-joined form does appear. The exact-match pass never
   touches that form, so a captured `network.json` exchange can carry a trivially decodable
   credential in full.
3. **HTML/JSON escaping.** A secret embedded in an HTML-escaped attribute (`&quot;`, `&amp;`) or a
   JSON-escaped string (`\"`, `\/`, `\uXXXX`) is not byte-identical to the raw value. The
   substitution does not match it either.
4. **Values split across chunks.** Evidence assembled from multiple reads or chunks (e.g. a
   streamed body, or text built up incrementally) can have the secret value's bytes land across a
   chunk boundary. The substitution never sees them as one contiguous string.

Each of these is a realistic way for a genuine secret to end up in evidence, verbatim but encoded
differently. The secret is a password, token, or API key bound via `${secrets.*}` (BE-0032). The
evidence is written to disk and, for the AI authoring and investigation paths, sent to the
configured AI provider (BE-0047). An author who bound `secrets:` correctly expects the value to
stay out of evidence. That author is protected against one encoding: the one the tool happens to
string-match.

## Detailed design

1. **Match common encodings of each known secret value**, not just the literal string, in
   `Redactor.redact_text` and `redact_exchange`:
   - Percent-encoding (`urllib.parse.quote` of the value, in a couple of common safe-char
     configurations, since encoders vary in what they leave unescaped).
   - Basic-auth base64: for each secret value that could be a Basic-auth password (or the full
     `user:pass` pair when both are known secrets), also mask the `base64(...)` forms found after
     `Authorization: Basic `.
   - HTML-escaped and JSON-escaped forms of the value (`html.escape`, `json.dumps` minus the
     surrounding quotes) wherever the literal text search already looks.
   - The transforms form a fixed, enumerable set applied to *known* secret values, not a general
     decode-everything scan. The cost and the false-positive surface stay bounded.
2. **Do not weaken key-based pattern matching.** The header/field key patterns (`_patterns`,
   `bajutsu/redaction.py:21`) are unaffected. This item extends how a literal *value* is recognized
   once the key or Basic-auth context locates roughly where the value lives.
3. **Treat chunk-boundary splitting as a best-effort mitigation, not a guarantee.** In some paths,
   evidence is assembled before redaction runs (e.g. a full body string rather than a live stream).
   There, redaction already sees the complete text and the problem does not arise. Where genuinely
   fragmented evidence exists, note the limitation instead of claiming complete coverage.
4. **Tests.** A secret value is redacted correctly when percent-encoded in a URL/query, when
   carried in a `Basic base64(...)` header, and when HTML/JSON-escaped in a body.
5. **Docs.** Note the encoding-aware matching and its known limits (e.g. genuinely fragmented
   streaming evidence) in the redaction docs (`docs/` and `docs/ja/`).

The default-header-masking gap (which headers are redacted at all) belongs to a separate sibling
item, default network secret redaction. This item is scoped to how a known secret *value* is
matched, regardless of which headers or fields triggered the search for it. Nothing here touches
the deterministic `run`/CI gate or introduces an LLM. The change is a pure text transform inside
the evidence-writing path.

## Alternatives considered

- **Decode every string found in evidence and compare against secret values.** Rejected as too
  broad and too slow. Decoding arbitrary text against every possible encoding is expensive, and it
  produces false positives (garbage that happens to decode to something). The reverse direction is
  cheaper and precise: encode each known secret value, then search for the known encodings.
- **Require authors to redact by header/field name alone, dropping value-based matching
  entirely.** Rejected. Value-based matching (BE-0032's `values` list) exists to catch a secret
  the app echoes somewhere the author did not anticipate (a log line, a response body). Removing it
  would regress that protection instead of fixing its encoding gap.
- **Leave this to the AI data sovereignty warning (screenshot-secret-capture-warning) instead of
  fixing the matcher.** Rejected. That item covers image evidence, which genuinely cannot be
  pixel-redacted. Text evidence *can* be matched correctly, so the fix belongs in the matcher, not
  in a disclosure.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Percent-encoded forms of known secret values matched
- [x] Basic-auth base64 forms of known secret values matched
- [x] HTML/JSON-escaped forms of known secret values matched
- [x] Chunk-boundary limitation documented where full-text redaction cannot see a fragmented value
- [x] Tests: percent-encoding, Basic-auth base64, HTML/JSON escaping all correctly masked
- [x] Docs updated (both languages)

- 2026-07-04: Implemented encode-aware value matching in `Redactor` — `_encoded_variants`
  precomputes percent / HTML / JSON forms into the masked-terms list, and `redact_text` decodes
  `Basic <base64>` tokens to mask credentials whose plaintext carries a known secret. Docs
  (`evidence.md` + `ja`) note the encoding-aware matching and the chunk-boundary limit.

## References

- `bajutsu/redaction.py:63` — `redact_text`'s literal `text.replace(value, PLACEHOLDER)`.
- `bajutsu/redaction.py:21` — `_patterns`, the key-based matching this item does not change.
- [BE-0032 — Secret variables](../BE-0032-secret-variables/BE-0032-secret-variables.md)
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
- Originates from the 2026-07-02 codebase-analysis report (security).
