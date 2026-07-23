**English** · [日本語](BE-0192-record-vision-on-demand-ja.md)

# BE-0192 — Vision-on-demand in record (attach a screenshot only when it adds information)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0192](BE-0192-record-vision-on-demand.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0192") |
| Implementing PR | [#785](https://github.com/bajutsu-e2e/bajutsu/pull/785) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

Stop attaching a full-resolution screenshot to **every** record turn. Today the loop sends a fresh
image on each of up to `max_steps` turns; the image is the single largest per-turn cost and — unlike
the system prompt and tools — it is never prompt-cached. This item makes vision **on-demand**: the
per-turn user message carries the screen's accessibility elements always, and the screenshot only
when it actually adds information — the first time a screen is seen, when the element tree is too
degenerate to act on, or when the agent explicitly asks to see the screen. On fully-addressable
screens, the follow-up turns become text-only, cutting the dominant uncached token cost without
removing vision or weakening any determinism guarantee.

## Motivation

The record loop (`bajutsu/record.py:record`) captures a screenshot every iteration
(`_screenshot_bytes` in `bajutsu/record.py`) and `ClaudeAgent` attaches it to the turn's user message
whenever it is present (`_user_content` / `ImagePart` in `bajutsu/claude_agent.py`). The image is
sent as a raw PNG and base64-encoded verbatim into the request (`bajutsu/ai/anthropic.py`).

Two facts make this the right thing to attack:

- **The image dominates the per-turn cost.** The record loop does *not* resend a growing transcript
  — each `next_action` is a fresh single-user-message request, and only a compact last-6-actions
  summary carries state forward (`_render` in `claude_agent.py`). So per-turn input is roughly
  constant across a session; the bulk of it is the screenshot plus the element tree.
- **The image is never cached.** Prompt caching covers only the static system prompt and tool
  definitions (`cache_control: ephemeral` in `bajutsu/ai/anthropic.py`); the per-turn observation —
  image + elements — is uncached by construction. So the screenshot is paid in full, every turn, at
  full device resolution, for all `max_steps` turns.

Yet a screenshot does not add information on most turns. The agent **acts only by an element's `id`
or `label`** (it never invents ids; docs/recording.md), so on a screen whose controls are all
addressable, the image is confirmatory, not decisive — the agent would choose the same action from
the element list alone. The screenshot earns its cost only in a few situations: seeing a screen for
the first time, reaching a control the accessibility tree omits (the `tap_point` fallback — e.g. an
individual tab in a bottom tab bar that idb collapses into one opaque group), or reading an
appearance/state the tree does not expose. Sending it on every turn pays for vision the agent is not
using.

This is complementary to, and independent of,
[BE-0178](../BE-0178-record-multi-action-turn/BE-0178-record-multi-action-turn.md): that item cuts
the *number* of model turns by batching intra-screen actions; this item cuts the *per-turn payload*
by not re-sending an image the agent does not need. They compose — fewer turns, and each remaining
turn lighter. It is also complementary to the sibling `record-screenshot-downscale` proposal (which
right-sizes the image on the turns that *do* carry one) and the sibling `record-turn-payload-diet`
proposal (which leans the element-tree text): together they attack every term of the per-turn cost.

### Accuracy is preserved, not traded

The design is built so vision is **deferred, never removed**:

- Every distinct screen is seen with a screenshot **at least once** (the first-observation trigger).
- A degenerate tree — the case where addressing-by-id is impossible and `tap_point` is the only way
  in — **always** gets the image (the degenerate-tree trigger).
- Any residual need is caught by the agent itself asking for the screen (the escalation trigger),
  which re-issues that one turn with the image attached.

So the agent can always see what it needs to; it simply is not handed an image on turns where the
element list already fully determines the action. This is why the item claims a token cut **without**
an accuracy cost, rather than a cost/accuracy trade.

## Detailed design

The change is confined to the authoring path (`record.py` / `claude_agent.py` / `agent.py`); the
recorded scenario artifact is byte-for-byte unchanged, so `run`, `codegen`, and the report are
unaffected. `Observation.screenshot` is already `bytes | None` — the loop simply decides, per turn,
whether to populate it, and the agent gains one way to ask for it back.

### 1. The loop decides per turn whether to attach the screenshot

`record()` tracks the previous turn's screen fingerprint (`crawl.fingerprint(elements)` — the
id-and-state projection already used to detect screen transitions, `bajutsu/crawl.py`). Before
building the `Observation`, it evaluates two deterministic triggers over the current elements:

- **New-screen trigger** — the current fingerprint differs from the previous turn's (or it is the
  first turn). The agent has not seen this screen yet, so attach the screenshot.
- **Degenerate-tree trigger** — the element list is too thin to act on by selector: no element
  carries an `id`, or addressable elements are few / the tree has collapsed to one opaque
  container spanning the screen (the no-id, tab-bar-style case where `tap_point` is the expected
  path). Attach the screenshot.

If neither fires — a screen already seen, with a rich addressable tree — the turn is **text-only**:
elements, plan, and recent-actions summary, no `ImagePart`. To also save the capture itself, the
screenshot is taken **lazily**, only when a trigger (or the escalation below) calls for it, rather
than every iteration.

### 2. The agent can escalate to vision when the tree is not enough

`claude_agent.py` gains a `need_screenshot` tool (and the equivalent structured-output field on the
Claude Code backend, so both providers behave identically). On a text-only turn, the system prompt
tells the agent: the screen's elements are always given; act from them when they suffice; **if you
genuinely need to see the screen to proceed** — a control you need is not in the list, or you must
read an appearance the tree does not expose — call `need_screenshot`. `proposal_from_call` maps that
call to a `Proposal` carrying no step and a `need_screenshot=True` signal. The loop, seeing it,
re-issues `next_action` **for the same, unchanged screen** with the screenshot attached (captured
lazily now). Escalation happens at most once per turn, so a turn costs at most one extra text-only
round-trip — and only on a screen that genuinely needed vision and was not caught by the two
deterministic triggers.

### 3. System-prompt guidance

The system prompt is extended to state that a screenshot **may or may not** be present on a given
turn, that the element list is always authoritative for addressing, and when to call
`need_screenshot`. The existing rules — act only on listed elements, prefer `id`/`label`, use
`tap_point` only for a control genuinely absent from the list — are unchanged; they simply now run
against a turn that may be text-only.

### 4. Timing / settle and redaction behavior is unchanged

No fixed `sleep` is introduced; the loop's settle behavior and per-action live resolution are
untouched. Redaction (BE-0047) already masks secrets in the element text and cannot mask an image;
this item, by sending *fewer* images, strictly reduces (never increases) the unredactable image
surface — a small privacy side-benefit, not a regression.

### Determinism, prime-directive compliance, and the gate

This stays strictly **Tier 1 (record only)**: no model call is added to `run` or CI. The two
attach/skip triggers are deterministic computations over the element tree (a fingerprint comparison
and an id-coverage check), not an LLM judgment (prime directive 1); the escalation is an
authoring-time request by the agent, still outside the gate. Per-action resolution is unchanged — a selector
still resolves uniquely against the live screen and an ambiguous one fails immediately (prime
directive 2). The triggers read only the generic element tree and need no per-app config (prime
directive 3). The scenario artifact shape is invariant, so the whole downstream and its tests are
unaffected.

### Cost model and its one trade-off

On an accessibility-rich app (e.g. the id-based showcase variant), most follow-up turns on a screen
become text-only → a large cut. On a no-id app the degenerate-tree trigger keeps sending images
(correctly), and the residual risk is that the agent, on an ambiguous text-only turn, needs one
escalation round-trip. To keep escalation the rare exception rather than the norm, the
degenerate-tree trigger is deliberately **generous** (attach proactively whenever id coverage is
low), trading a few unnecessary images on hard screens for avoiding a cascade of escalation
round-trips. The net effect is a clear win on well-instrumented apps and no worse than break-even on
poorly-instrumented ones.

### Test strategy (fits the Linux `make check` gate, no Simulator)

- **Loop tests with a scripted fake driver and agent**: assert the first turn on a screen attaches a
  screenshot and a same-fingerprint follow-up turn does not; assert a fingerprint change re-attaches;
  assert a degenerate (no-id) element list attaches regardless of fingerprint; assert lazy capture
  (the fake driver's `screenshot` is not called on a text-only turn).
- **Escalation tests**: a fake agent that returns `need_screenshot` on a text-only turn causes the
  loop to re-issue with the image attached, at most once, on the same screen; the eventual action is
  recorded normally.
- **Backend-mapping tests**: `need_screenshot` maps to the escalation signal via `proposal_from_call`
  for the API agent, and the Claude Code structured-output equivalent maps to the same signal.
- **Regression**: a screen that always triggers (e.g. every turn a new fingerprint) behaves exactly
  as today (image every turn).

### Scope & non-goals

**In scope:** the per-turn attach/skip decision (new-screen + degenerate-tree triggers) with lazy
capture; the `need_screenshot` escalation on both backends; the system-prompt guidance.

**Non-goals:** reducing the *size* of an attached image (the sibling `record-screenshot-downscale`
proposal); leaning the element-tree text (the sibling `record-turn-payload-diet` proposal); reducing
the *number* of turns (BE-0178); the `crawl` explorer loop (`bajutsu/crawl.py`), whose per-screen
vision needs differ and is out of scope here; changing the scenario schema or any `run`/replay
behavior.

### Decisions

1. **Deterministic triggers up front, agent escalation as the safety net.** The loop decides
   attach/skip from the element tree (no LLM), and the agent can always pull the image back when the
   tree is insufficient. *Rationale:* keeps the common path free of a wasted round-trip while
   guaranteeing vision is never unavailable — accuracy is preserved by construction.
2. **The degenerate-tree trigger is generous.** When id coverage is low, attach proactively rather
   than wait for an escalation. *Rationale:* on poorly-instrumented apps this avoids trading one saved
   image for one wasted escalation round-trip; it keeps the change no-worse-than-break-even there.
3. **Capture lazily.** Take the screenshot only when a trigger or escalation needs it, not every
   iteration. *Rationale:* saves the `simctl screenshot` subprocess on text-only turns too, a small
   wall-clock bonus on top of the token cut.

## Alternatives considered

- **Drop the screenshot entirely (elements-only record).** Maximal token saving, but it removes the
  `tap_point` vision fallback and any appearance-based disambiguation — a real accuracy loss on no-id
  apps. Rejected: the brief is to save tokens *without* losing accuracy, so vision must remain
  available, hence on-demand rather than off.
- **Send the image only on the first turn of the whole session.** Simpler, but it starves the agent
  of vision on every later new screen, where it is most needed. Rejected in favor of per-screen
  first-observation gating.
- **Let the loop, not the agent, decide when vision is needed mid-screen.** The loop has no notion of
  *why* the agent is stuck, so it would either always send (no saving) or guess. Rejected: the agent
  is the right place to ask for vision; the deterministic triggers handle the cases that do not need a
  judgment.
- **Cache the per-turn image with `cache_control`.** The image changes most turns, so caching it
  rarely hits and adds cache-write cost; the win here comes from *not sending* it, not from caching
  it. Rejected as the primary mechanism.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Loop decides attach/skip per turn (new-screen + degenerate-tree triggers) with lazy capture.
- [x] `need_screenshot` escalation: tool + `proposal_from_call` mapping (API agent) and the Claude
      Code structured-output equivalent; loop re-issues once with the image on the same screen.
- [x] System-prompt guidance: screenshot may be absent, elements are authoritative, when to escalate.
- [x] Tests: attach/skip/lazy-capture loop tests; escalation loop + backend-mapping tests;
      image-every-turn regression guard.

**Log**

- [#785](https://github.com/bajutsu-e2e/bajutsu/pull/785) — Shipped the whole item: `_should_attach` (new-screen + degenerate-tree triggers) with
  lazy capture in `record.py`; the `need_screenshot` tool + `proposal_from_call` mapping + `_combine`
  short-circuit in `claude_agent.py` (shared by the API and Claude Code backends); system-prompt and
  `_render` guidance; loop tests, backend-mapping tests, and the image-every-turn regression guard.

## References

`bajutsu/record.py` (the record loop, `_screenshot_bytes`, `with_screenshot`), `bajutsu/agent.py`
(`Observation.screenshot`, `Proposal`, the `Agent` protocol), `bajutsu/claude_agent.py`
(`next_action`, `_user_content`, `_render`, `proposal_from_call`, `SYSTEM_PROMPT`, `TOOLS`),
`bajutsu/ai/anthropic.py` (`cache_control`, the `ImagePart` → base64 translation), `bajutsu/ai/base.py`
(`ImagePart`), `bajutsu/crawl.py` (`fingerprint`), `docs/recording.md` (the authoring-agent
architecture: acts by id/label, prompt caching, `tap_point`).

**Dependencies / related items:**
[BE-0178](../BE-0178-record-multi-action-turn/BE-0178-record-multi-action-turn.md) (cuts the number
of turns; complementary — this item cuts the per-turn payload), the sibling `record-screenshot-downscale`
proposal (right-sizes an attached image; complementary), the sibling `record-turn-payload-diet`
proposal (leans the element-tree text; complementary),
[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) (redaction — images cannot be
masked, so sending fewer strictly helps), [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md)
(the Claude Code backend with file-based vision, which must map the same `need_screenshot` signal).
