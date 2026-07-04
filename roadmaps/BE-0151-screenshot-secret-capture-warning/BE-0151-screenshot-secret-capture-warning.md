**English** · [日本語](BE-0151-screenshot-secret-capture-warning-ja.md)

# BE-0151 — Warn when screenshots and video may capture on-screen secrets

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0151](BE-0151-screenshot-secret-capture-warning.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0151") |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

Screenshots and video captured during `record`/`enrich`/`triage` are not redacted — a typed
password, an OTP, or on-screen PII stays in the image, is written to disk under `runs/`, and the
current screenshot is sent to the configured AI provider as image content on every turn. This item
adds a clear, up-front warning when secrets are bound, so the author knows this before it happens,
rather than silently masking pixels.

## Motivation

The AI authoring/investigation agent sends the current screenshot as part of every turn's user
content: `_user_content` (`bajutsu/claude_agent.py:381`) appends the screenshot as a base64 `image`
block "as-is — images cannot be pixel-masked," alongside the redacted textual element tree
(`Redactor`, `bajutsu/redaction.py:7`, which only ever operates on text: free-text evidence and
structured element values, never image bytes). The same screenshots and any recorded video persist
in `runs/` on disk as evidence artifacts.

This is not a bug in the redaction mechanism — an image genuinely cannot be selectively masked
without knowing which pixels are the secret, and visual evidence (seeing what the app rendered) is
the point of a screenshot. But it means that whenever a scenario binds `secrets:` (BE-0032) — most
commonly a password or OTP field the app under test displays or partially unmasks — the raw
on-screen value can end up:

1. Written to a screenshot/video file under `runs/` on the local disk.
2. Sent to the Anthropic API (or whichever provider is configured) as image content on every
   `record`/`enrich`/`triage` turn where the app is visible.

An author binding a secret for a login flow may reasonably assume that "secrets" means "the value
never leaves the machine" or "the value is masked everywhere," neither of which is true for
on-screen pixels. The fix is not to attempt pixel redaction (out of scope, and probably infeasible
to do reliably) but to make the exposure visible: warn plainly, at the point secrets are bound,
that on-screen secrets can leak into evidence files and to the AI provider.

This is scoped entirely to the AI authoring/investigation paths (`record`, `enrich`, `triage`); the
deterministic `run`/CI gate stays LLM-free and unaffected, per the prime directive that AI never
sits on the pass/fail path.

## Detailed design

1. **Warn when a command that both uses AI and binds secrets starts.** In `record`/`enrich`/
   `triage`, when the resolved `Effective.secrets` list (`bajutsu/cli/_shared.py:37`) is non-empty,
print a one-time, explicit warning before the AI loop starts: on-screen secrets (typed
passwords, OTPs, PII the app displays) are not redacted from screenshots or video, and persist on
disk under `runs/`. The current screenshot is sent to the configured AI provider as image content on
every turn where the app is visible.
2. **State what "secrets" redaction does and does not cover.** The warning names the boundary
   precisely: `${secrets.*}` values are redacted from the *text* evidence (network, element tree,
   logs) via `Redactor`, but never from *images* — screenshots are sent as-is to the configured AI
   provider, and screenshots/video artifacts are stored as-is under `runs/`.
3. **No silent behavior change.** No new flag suppresses the screenshot capture or the AI call;
   this item is a disclosure, not a mitigation of the underlying exposure (visual evidence is the
   product). An author who wants to avoid the exposure entirely already can (skip AI-driven
   authoring for secret-bearing flows, or avoid displaying the secret on-screen in the app under
   test); the warning makes that trade-off visible instead of implicit.
4. **Tests.** A `record`/`enrich`/`triage` invocation with `secrets:` bound emits the warning; an
   invocation with no secrets bound does not.
5. **Docs.** Document the screenshot/video redaction boundary alongside the existing secret
   variables and AI data sovereignty docs (`docs/` and `docs/ja/`).

## Alternatives considered

- **Attempt pixel-level redaction (blur/black-box regions).** Rejected: there is no reliable way to
  know which region of a screenshot holds a secret value without app-specific instrumentation
  (accessibility labels don't map to pixel regions across arbitrary UI), and a wrong guess would
  give false confidence. This is explicitly out of scope; the by-design nature of visual evidence
  is the reason a warning, not a mitigation, is the right fix.
- **Silently skip screenshot capture when secrets are bound.** Rejected: it would degrade the
  authoring/investigation experience (no visual evidence for exactly the flows — login, OTP — where
  it is often most useful for confirming the UI behaved correctly) without the author choosing that
  trade-off.
- **Say nothing and rely on the general AI data sovereignty documentation (BE-0047).** Rejected:
  BE-0047 covers the AI provider relationship in general; this exposure is specific and easy to
  miss (screenshots feel like "just UI evidence," not "a channel that shares typed secrets"), so it
  warrants its own explicit, moment-of-use warning.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Warning emitted at the start of `record`/`enrich`/`triage` when `secrets:` is bound
- [ ] Warning text states the screenshot/video redaction boundary precisely
- [ ] No behavior change to screenshot capture or the AI call itself
- [ ] Tests: warning emitted with secrets bound, absent without
- [ ] Docs updated (both languages)

No PR has landed yet.

## References

- `bajutsu/claude_agent.py:381` — `_user_content`, sending the screenshot as-is alongside redacted text.
- `bajutsu/redaction.py:7` — `Redactor`'s module docstring: "Images (screenshots/video) cannot be
  masked and are left as-is."
- `bajutsu/cli/_shared.py:37` — resolving `Effective.secrets` against the environment.
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
- [BE-0097 — Crawl AI data sovereignty](../BE-0097-crawl-ai-data-sovereignty/BE-0097-crawl-ai-data-sovereignty.md)
- [BE-0032 — Secret variables](../BE-0032-secret-variables/BE-0032-secret-variables.md)
- Originates from the 2026-07-02 codebase-analysis report (security).
