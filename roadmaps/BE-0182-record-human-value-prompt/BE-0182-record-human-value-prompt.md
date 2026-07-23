**English** · [日本語](BE-0182-record-human-value-prompt-ja.md)

# BE-0182 — Human value entry during record (OTP / random / one-off values)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0182](BE-0182-record-human-value-prompt.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0182") |
| Implementing PR | [#1207](https://github.com/bajutsu-e2e/bajutsu/pull/1207) |
| Topic | Authoring experience |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance.md), [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md), [BE-0120](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization.md), [BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md) |
<!-- /BE-METADATA -->

## Introduction

Rides on the record human-in-the-loop handoff substrate
([BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md)). This item
covers the case where the AI can locate the input field but **cannot know the value** — a one-time
password (OTP), a two-factor (2FA) code, a random string, or an externally-issued one-off value.
`record` pauses, the human supplies the value, recording continues, and the recorded artifact
resolves that value **deterministically** on re-run by bridging to a
[BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) `totp` / `email` step or a
`secret` / `var`.

## Motivation

An out-of-band value is the single most common thing that stops an AI recording of a real login,
verification, or password-reset flow. The agent can see the "enter code" field, but the value
exists only in an authenticator app, an inbox, or a human's head. In `record` today the loop either
stops at that field or the agent invents a plausible-but-wrong value — both leave the author to
hand-write the rest.

[BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) already solves the *run-time* side:
`totp` computes an OTP locally from a seed, `email` polls a test mailbox. But it assumes the author
already knows the field is an OTP field and has wired the seed or mailbox ahead of time. The record
loop is exactly where that knowledge is discovered — interactively, mid-flow. This item closes the
gap: let the human supply the value **once** during recording so the flow can be captured end to
end, and emit an artifact that re-runs with no human by pointing at BE-0046 or a declared secret.

## Detailed design

Built on the substrate's request/response contract; this item defines the value-specific behavior.

**Blocker detection and explicit request.** The agent raises the substrate's "needs human" outcome
for a value when it flags a field it cannot fill from its own knowledge — heuristically (a field
labeled OTP / code / verification, or one the author marked) — and the tool **never guesses** a
value to fill it. The author can also mark a field as human-supplied up front.

**Prompt content.** The handoff request names the target field (a compact selector summary) and
asks for the value; the human types it in (CLI stdin, or a `serve` input) through the substrate's
surfaces.

**Live execution versus recorded artifact.** The supplied value is typed into the *live* app so the
recording proceeds to the next screen, but it is **not** written into the scenario — it is random
or secret. This reuses [BE-0120](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization.md)'s
tokenization and masking so the literal never lands in the YAML, the manifest, or the live progress
stream.

**Deterministic output — the bridge.** The recorded step becomes a `${vars.*}` / `${secrets.*}`
placeholder plus a labeled TODO that classifies the source so the author can wire it: "resolve with
`totp` (BE-0046)", "resolve with `email` (BE-0046)", or "declare as a `secret`". Consistent with
prime directive 1, the AI may *propose* the likely classification (it is authoring, not judging);
the author confirms and wires it. Once wired, re-run is fully deterministic and AI-free.

**Provenance.** The step carries `from:` provenance
([BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance.md)) recording that it
originated from a human value handoff, so the report and the GUI editor can show *why* it still
needs wiring.

**CLI and `serve`.** Both surfaces come from the substrate; this item adds only the value prompt
and the classification/TODO emission on top. On `serve`, the handoff pane shows the awaited field
(highlighted on the request screenshot) and a single input for the value — the author reads the
code off their authenticator or inbox and types it into the browser. Because a value is supplied
**entirely in the browser** and needs no access to the device, this pattern works unchanged on a
remote or self-hosted `serve` (BE-0015 / BE-0016), where the Simulator is not in front of the
author. That is the sharp contrast with the operation-takeover pattern, which does need the author
to reach the device.

## Alternatives considered

- **Record the literal value the human typed.** Rejected: the value is random or secret, so the
  scenario would be non-reproducible on the next run and would leak a secret into the artifact —
  the exact problem [BE-0120](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization.md)
  and [BE-0152](../BE-0152-totp-seed-artifact-leak/BE-0152-totp-seed-artifact-leak.md) guard against.
- **Require BE-0046 pre-configuration before recording, with no handoff.** Rejected as the *only*
  path: the author often does not yet know a field is an OTP field until the flow reaches it. The
  handoff discovers it interactively and then nudges toward BE-0046 — the two are complementary, not
  substitutes.
- **A run-time "prompt for a value every run" step.** Rejected — that puts a human in the
  deterministic `run` / CI gate (directive 1). The whole point is to resolve the value to a
  deterministic source so the recorded flow replays unattended.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Value-blocker detection (heuristic flag), never a guessed fill.
- [x] Value prompt content over the substrate's request/response contract.
- [x] Live type of the supplied value with BE-0120 tokenization/masking of the artifact.
- [x] Deterministic-output bridge: `${vars.*}` / `${secrets.*}` placeholder + classified TODO (totp / email / secret).
- [x] `from:` provenance (BE-0044) marking the human-value origin.

Deferred to a follow-up (out of this first slice): the *author mark up front* half of value-blocker
detection — letting the author declare a field as human-supplied ahead of the flow, distinct from the
agent-raised heuristic shipped here. It needs a config surface (`targets.<name>`) and mirrors how the
substrate ([BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md)) deferred its
author-initiated takeover trigger.

**Log**

- Landed the value pattern on the BE-0179 substrate: the agent's `ask_human` tool now addresses the
  field a value goes into and proposes a `classify` (`totp` / `email` / `secret`) and placeholder
  `name` ([`bajutsu/agents/claude.py`](../../bajutsu/agents/claude.py)), carried on `Proposal`
  ([`bajutsu/agents/protocols.py`](../../bajutsu/agents/protocols.py)). On a value response that names
  a field, the record loop types the real value into the live app and records a placeholder `type`
  step — `${vars.*}` (a `totp` / `email` run-time bridge, BE-0046) or `${secrets.*}` (a declared
  secret), never the literal (BE-0120) — with a classified TODO in its `from:` provenance (BE-0044)
  ([`bajutsu/record.py`](../../bajutsu/record.py)). A handoff that names no field re-observes as
  before. Fast-suite tests cover the placeholder shape, the live type, the no-leak guarantee, and the
  no-field fallback; docs updated in both languages.

## References

Substrate: [BE-0179](../BE-0179-record-human-handoff/BE-0179-record-human-handoff.md). Sibling
pattern: `record-human-takeover-step` (operations).
Related existing items:
[BE-0046 — OTP & email side-channel steps](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md)
(the run-time bridge target),
[BE-0120 — Tokenize secrets in recorded scenario YAML](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization.md),
[BE-0044 — Scenario provenance](../BE-0044-scenario-provenance/BE-0044-scenario-provenance.md).
