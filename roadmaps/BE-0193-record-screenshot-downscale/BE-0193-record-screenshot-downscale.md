**English** · [日本語](BE-0193-record-screenshot-downscale-ja.md)

# BE-0193 — Right-size the record screenshot with a deterministic client-side downscale

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0193](BE-0193-record-screenshot-downscale.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0193") |
| Implementing PR | [#784](https://github.com/bajutsu-e2e/bajutsu/pull/784) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

When record does send a screenshot to the model, send it right-sized. Today the loop captures a
full-resolution device screenshot and hands the raw PNG bytes to the model verbatim, with no resize
step anywhere in the authoring or adapter path. An image's token cost scales with its **pixel
dimensions**, so a full-resolution Simulator screenshot (often well over 2000px on the long edge) is
billed for far more pixels than the model can use. This item downscales the screenshot on the client,
before it is encoded, to a bounded long edge — capped at the resolution above which the model gains
nothing — so the per-image token cost is deterministic and provider-independent, with no loss of the
detail the agent actually reads.

## Motivation

`_screenshot_bytes` (`bajutsu/record.py`) captures the screen via `xcrun simctl io <udid>
screenshot` (`bajutsu/drivers/idb.py`, `screenshot_cmd`), which writes a full-resolution PNG with no
size parameter. That PNG is carried unchanged into the request: `ImagePart(data=screenshot)`
(`bajutsu/claude_agent.py`) is base64-encoded verbatim in the Anthropic adapter
(`bajutsu/ai/anthropic.py`), `media_type` defaulting to `image/png` (`bajutsu/ai/base.py`). A repo-wide
search finds **no** image-resize / downscale / compression step in the AI, record, or driver path.

Two properties of image billing make this wasteful:

- **Token cost tracks pixel dimensions, not file size.** For the Anthropic API, an image's token cost
  is a function of its width × height, and the model derives no additional benefit above a bounded
  resolution (roughly a ~1568px long edge / ~1.15 MP). A Simulator screenshot at full device
  resolution is typically well beyond that, so the excess pixels are paid for and then discarded.
- **The reduction is currently implicit and provider-dependent.** The Anthropic API downscales an
  oversized image server-side, so today's cost is *as if* it were bounded — but that behavior is the
  provider's, not ours. On another backend (e.g. Amazon Bedrock, BE-0053) or a future provider the
  guarantee may differ, and even on the Anthropic API the oversized bytes are still uploaded every
  turn. Doing the downscale on the client makes the resolution — and therefore the token cost —
  something Bajutsu controls and can hold constant across providers.

This is a small, self-contained change that composes with the two sibling proposals: the
`record-vision-on-demand` proposal cuts *how often* an image is sent, and this one caps *how large*
each sent image is; the `record-turn-payload-diet` proposal leans the text half of the turn. It is
also independent of [BE-0178](../BE-0178-record-multi-action-turn/BE-0178-record-multi-action-turn.md)
(which reduces the number of turns).

### Why accuracy does not regress

The cap is set at (not below) the resolution the model already reduces to internally, so the pixels
the agent reads are unchanged from today — the client simply does the reduction the provider would
otherwise do, and never goes finer. The element tree, which is the agent's authoritative source for
addressing (`id`/`label`), is untouched. `tap_point` uses **normalized [0,1] coordinates**
(`bajutsu/claude_agent.py`), independent of pixel dimensions, so a downscaled image maps to exactly
the same tap. There is therefore no screen on which a right-sized image changes the agent's decision
relative to the full-resolution one.

## Detailed design

### 1. A shared downscale helper, applied before the image reaches the model

Add a small helper that takes the captured PNG bytes and returns bytes whose long edge is at most a
constant `MAX_IMAGE_LONG_EDGE` (defaulting to the provider's useful maximum, ~1568px), downscaling
only — never upscaling a smaller image, and passing an already-small image through untouched. It is
applied on the authoring side (where the `Observation`/`ImagePart` is built), not inside the
vendor-neutral adapter, so every provider receives the same right-sized image and the adapter stays a
pure translator (BE-0104). Aspect ratio is preserved.

### 2. Format stays PNG (a deliberate non-change)

The image is kept as PNG. Because Anthropic bills by pixel dimensions rather than byte size,
re-encoding to JPEG would **not** reduce the token count — it would only shrink the upload and risk
blurring small on-screen text and thin controls that the agent reads. Downscaling the dimensions is
the entire token lever; the format is left alone to protect legibility. (This is called out explicitly
so a future reader does not "optimize" by switching to JPEG expecting a token saving.)

### 3. Where the resize runs, per backend

- **iOS (idb / Simulator)** — the capture is a macOS `simctl` screenshot, so the downscale runs on the
  captured PNG bytes. The implementation may use a lightweight image step; the dependency question is
  in *Alternatives considered*.
- **Web (Playwright)** — the backend can capture at a target size directly, so the same
  `MAX_IMAGE_LONG_EDGE` bound is applied at capture rather than as a post-step. Either way the byte
  payload handed to the authoring path already respects the cap.

The helper's contract (bytes in → bounded-long-edge bytes out) is backend-agnostic; how each backend
produces the bytes is a backend detail.

### 4. The cap is a constant, not per-app config

`MAX_IMAGE_LONG_EDGE` is a single module constant, identical for every target. It is not a
`targets.<name>` setting: the right resolution is a property of the *model's* image handling, not of
the app under test, so making it app-specific would violate the app-agnostic prime directive for no
benefit. (A global `ai.*` knob could be added later if a provider's useful maximum ever differs, but
that is out of scope here.)

### Determinism, prime-directive compliance, and the gate

Strictly **Tier 1 (record only)**: no model call is added to `run` or CI (prime directive 1). The
resize is a deterministic pixel operation with no `sleep` and does not touch selector resolution
(prime directive 2). The cap is a global constant with no per-app branch (prime directive 3). The
recorded scenario artifact is unchanged — the screenshot is an input to the authoring model, never
part of the emitted YAML — so `run`, `codegen`, the report, and their tests are unaffected.

### Test strategy (fits the Linux `make check` gate, no Simulator)

- **Downscale helper unit tests**: an oversized image is reduced so its long edge equals
  `MAX_IMAGE_LONG_EDGE` with aspect ratio preserved; an already-small image passes through byte-for-byte
  (no upscale); a square and both landscape/portrait orientations each cap the correct edge.
- **Integration on the authoring path**: the bytes reaching `ImagePart` respect the cap (assert on a
  synthetic oversized PNG through a fake driver — no Simulator needed).
- **tap_point invariance**: a recorded `tapPoint` from a downscaled-image turn is byte-identical to one
  from the full-resolution turn (coordinates are normalized), guarding the accuracy claim.

### Scope & non-goals

**In scope:** the client-side downscale helper and its `MAX_IMAGE_LONG_EDGE` cap; wiring it into the
iOS and web authoring capture paths; keeping the format PNG.

**Non-goals:** deciding *whether* to send an image at all (the `record-vision-on-demand` proposal);
leaning the element-tree text (the `record-turn-payload-diet` proposal); reducing the number of turns
(BE-0178); changing image handling for `crawl` or for the alert locator / triage vision paths (they can
adopt the same helper later, but are out of scope here); any scenario-schema or `run`/replay change.

## Alternatives considered

- **Rely on the provider's server-side downscale (do nothing).** This is today's behavior and is why
  the *cost* is already bounded on the Anthropic API. Rejected: it is the provider's guarantee, not
  ours; it still uploads oversized bytes every turn; and it does not hold uniformly across Bedrock or a
  future backend. Owning the resolution makes the cost deterministic and provider-independent.
- **Re-encode as JPEG to shrink the payload.** Rejected: token cost is dimension-based, so JPEG saves
  no tokens; it only shrinks bytes while risking blur on small text the agent must read — a pure
  accuracy risk for a non-token benefit.
- **Downscale below the model's useful maximum (aggressive shrink).** Rejected: going finer than the
  model's internal reduction would start discarding pixels the agent does read, trading accuracy for
  tokens — the opposite of this item's premise.
- **Capture the Simulator screenshot at a lower resolution up front.** `simctl io screenshot` has no
  size parameter, so this is not available on the iOS path without a post-capture resize anyway; the
  web path already captures at a target size (design section 3).

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Shared downscale helper with a `MAX_IMAGE_LONG_EDGE` cap (downscale-only, aspect preserved).
- [x] Wire it into the iOS authoring capture path (post-capture resize of the `simctl` PNG).
- [x] Wire it into the web authoring capture path (capture at the bounded size).
- [x] Tests: helper unit tests; authoring-path integration; `tap_point` normalized-coordinate invariance.

**Log**

- [#784](https://github.com/bajutsu-e2e/bajutsu/pull/784) — `downscale_png` added to `bajutsu/visual.py` (downscale-only, aspect preserved, PNG kept);
  applied via `_downscaled` in `_screenshot_bytes` (`bajutsu/record.py`), the capture choke point shared by
  both backends, so iOS and web are covered in one place above the vendor-neutral adapter (BE-0104) rather
  than by a per-backend branch. `MAX_IMAGE_LONG_EDGE = 1568` is a module constant. Pillow (the `visual`
  extra) is imported lazily and, when absent, the full-resolution bytes pass through unchanged. Tests in
  `tests/test_downscale.py`.

## References

`bajutsu/record.py` (`_screenshot_bytes`), `bajutsu/drivers/idb.py` (`screenshot_cmd`, the `simctl`
capture), `bajutsu/claude_agent.py` (`ImagePart`, `tap_point` normalized coordinates), `bajutsu/ai/anthropic.py`
(the `ImagePart` → base64 translation), `bajutsu/ai/base.py` (`ImagePart`, `media_type`),
`docs/recording.md` (the authoring-agent architecture).

**Dependencies / related items:**
the sibling `record-vision-on-demand` proposal (cuts how often an image is sent; complementary — this
item caps how large each sent image is), the sibling `record-turn-payload-diet` proposal (leans the
element-tree text; complementary),
[BE-0178](../BE-0178-record-multi-action-turn/BE-0178-record-multi-action-turn.md) (reduces the number
of turns; independent), [BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md)
(Amazon Bedrock provider — a case where the client-side cap makes the token cost provider-independent),
[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) (the vendor-neutral
seam the resize must sit *above*, so the adapter stays a pure translator).
