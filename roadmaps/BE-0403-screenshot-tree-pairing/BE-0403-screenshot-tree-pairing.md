**English** · [日本語](BE-0403-screenshot-tree-pairing-ja.md)

# BE-0403 — Pair a step's screenshot with the element tree that describes the same screen

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0403](BE-0403-screenshot-tree-pairing.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0403") |
| Implementing PR | [#1834](https://github.com/bajutsu-e2e/bajutsu/pull/1834) |
| Topic | Verification & coverage |
| Related | [BE-0341](../BE-0341-pre-action-evidence-capture/BE-0341-pre-action-evidence-capture.md), [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md) |
<!-- /BE-METADATA -->

## Introduction

A step's report entry shows one screenshot beside one element tree, and a reader is invited to read
them as one screen: hovering a row of the tree draws that element's frame onto the image. Three
situations break that promise today, and none of them is visible to the reader. This item records
on every artifact **which screen it depicts**, and makes each viewer draw frames only when the
image and the tree agree — showing the pair without frames, rather than a frame in the wrong place,
whenever they do not.

## Motivation

### What a viewer shows for a step

Every step records two screenshots and one tree. The pre-step baseline writes `before.png` and the
pre-action tree before the step acts, and the post-step capture writes `after.png` and the
post-action tree once it has
([BE-0341](../BE-0341-pre-action-evidence-capture/BE-0341-pre-action-evidence-capture.md)). The
tree has a single filename, `elements.json`, so the second write replaces the first: the file a run
keeps holds the post-action tree, which is the screen `after.png` shows.

Three consumers turn that into one image per step. The HTML report's element viewer
([`bajutsu/report/rows.py`](../../bajutsu/report/rows.py)) draws a hovered element's frame onto the
image; the serve editor's element picker
([`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py)) resolves a click on
the image into a selector through the tree; and the triage context
([`bajutsu/triage.py`](../../bajutsu/triage.py)) hands both to a failure investigator. All three
resolve the image through one shared helper, `displayed_screenshot`
([`bajutsu/evidence/core.py`](../../bajutsu/evidence/core.py)), which prefers `after.png` and falls
back to whichever screenshot the step has. All three take the tree from the manifest's `elements`
entry.

That resolution is a guess dressed as a pairing. The manifest records `Artifact(name, kind,
provider)` — what the file is and who supplied it, never which screen it shows — so no consumer can
check whether the image it chose and the tree beside it describe the same moment, or even the same
device. The guess is right on the ordinary path, and wrong in the three situations below.

### Three ways the pair comes apart

**A `web` block pairs a native image with a WebView tree.** Inside a `web` block the run loop
resolves selectors against a `WebContextDriver`
([`bajutsu/webview.py`](../../bajutsu/webview.py)), whose tree carries frames in the WebView's own
coordinate space rather than the device screen's. The screenshot cannot come from that driver — a
`WebContextDriver` raises `UnsupportedAction` for `screenshot` — so it is taken from the native
driver and shows the whole device screen, status bar and native chrome included. The element
viewer then derives its scale from the tree's own bounding box and maps every frame onto that
native image, placing each box at coordinates that never described it. The run loop already refuses
this pairing for one artifact: it drops a `rawTree` request inside a `web` block rather than write
the native driver's last read next to a WebView tree, on the stated grounds that no artifact beats
a mismatched one. The same reasoning applies to the screenshot the viewer draws on, and today it is
not applied.

**A missing `after.png` silently promotes `before.png`.** Each of the three consumers filters its
candidates to the files the run actually holds, because a manifest can name a screenshot the store
no longer has — a run restored from Trash, or one synced into an object store that never received
the last write. When `after.png` is the missing one, the fallback selects `before.png` and pairs
the pre-action image with the post-action tree that replaced the pre-action one. The fallback is
right about the image: `before.png` is the only picture of the step there is. It is wrong to keep
drawing frames on it.

**An older stored run can hold a pre-action tree beside a post-action image.** Before BE-0341 the
post-step `elements` write was not unconditional, so a run recorded under a narrowed `capture` list
can hold a pre-action `elements.json` next to an `after.png`. The docstring of
`displayed_screenshot` records this case and defers the fix, naming the reason it cannot be made:
`kind` records the artifact, not which side of the action it was taken on, so nothing in the
manifest distinguishes the two. Runs recorded before this item stay undecidable — the fact was
never written down, and no later reader can recover it — but every run recorded after it carries
the answer.

### The outcome to check

After this item, a step whose screenshot and tree describe different screens shows the image and
the element list with no frames drawn, in the HTML report and in the serve editor alike, and the
triage context carries no image for such a step at all. A reader can check the change on a scenario
with a `web` block: today its element frames land on the wrong pixels, and afterwards no frame is
drawn on that step. Every ordinary step keeps the frames it has today.

## Detailed design

### What each artifact depicts

`Artifact` gains one field, `depicts`, recording the screen a file shows as `"<driver>:<moment>"` —
the name of the driver whose reading produced the file, and which side of the step's action it was
taken on (`before` or `after`). A native step's `before.png` carries `"xcuitest:before"`; its tree
and `after.png` carry `"xcuitest:after"`; a `web` block step's tree carries `"webview:after"` while
its screenshot still carries the native driver's name.

Two artifacts describe the same screen exactly when their `depicts` values are equal. That equality
is the whole contract, which is why one string carries both halves rather than two fields carrying
one each: a consumer compares, and never parses.

`depicts` is `None` for an artifact that shows no screen — an interval recording, the first-wait
timeout diagnostic — and on every run recorded before this item. A consumer that finds `None` on
either side of a pair falls back to exactly today's behavior, so an older run renders as it does
now rather than losing frames it has always drawn.

### Where the value comes from

`capture()` builds `depicts` from what it already receives. The moment is the capture token's
modifier, which the token grammar already carries: `screenshot.before` gives `before`, and a bare
`elements` or `screenshot` gives `after`. The driver is the capture driver's own `name` for a
screenshot, and the source of the tree for `elements` and `rawTree` — a new keyword argument,
`elements_source`, naming the driver the passed-in `elements` were read from and defaulting to the
capture driver's name.

The run loop passes `elements_source=active_driver.name` at both of its capture calls, because both
hand over a tree that came from the active driver. The pre-step baseline additionally asks for
`elements.before` rather than a bare `elements`, so the moment recorded for the baseline tree is
the one it was read at. The modifier does not reach the filename — `write_elements` ignores it, as
it does today — so the baseline still writes `elements.json` and the post-step write still replaces
it.

### Resolving the pair

A new function in `bajutsu/evidence/core.py` replaces `displayed_screenshot` at all three call
sites, taking a step's artifact entries as `(kind, name, depicts)` triples plus a predicate that
says whether the store holds a named file, and returning the screenshot, the tree, and whether the
two are paired.

The tree is the **last** existing `elements` entry, not the first: the file has one fixed name, so
the last write is the one that survives, and its `depicts` is the only one describing the file's
content. The screenshot is the existing candidate whose `depicts` equals the tree's. When no
candidate matches, the resolver returns today's choice — `after.png`, else the first recorded — and
reports the pair as unpaired, so the caller keeps an image to show and knows not to draw on it.

### Work breakdown (MECE)

1. **Record `depicts` on every captured artifact.** Add the field to `Artifact`, compute it in
   `capture()` from the token's modifier and the tree's source, and thread `elements_source`
   through `EvidenceSink` and its two implementations.
2. **Tag the run loop's two capture calls.** Pass `elements_source=active_driver.name` from both,
   and ask the pre-step baseline for `elements.before`.
3. **Resolve the pair in one place.** Add the resolver described above and retire
   `displayed_screenshot`.
4. **Honor the resolver in the three viewers.** The HTML report omits the screen extent when the
   pair is unpaired, which is already all `report.js` needs to draw no frame, and states on the
   tree button why frames are absent. The serve editor withholds the screenshot for an unpaired
   step and says why, rather than offering a picker that would resolve a click against the wrong
   pixels. The triage context takes no screenshot from such a step; the existing backward scan
   already stops at a step that recorded screenshots it cannot use, so it does not reach back for
   an earlier step's image.
5. **Bump the manifest schema and the documentation.** `SCHEMA_VERSION` rises to 9 with the note
   the file's history keeps, and [`docs/evidence.md`](../../docs/evidence.md) and
   [`docs/reporting.md`](../../docs/reporting.md) — with their Japanese mirrors — describe the new
   field and what a viewer does when a pair does not match.

### Machine-checkable outcome

`make check` green, with tests that pin: `capture()` writing the expected `depicts` for each token
and source; the resolver preferring the matching screenshot, falling back unpaired when none
matches, and reproducing today's choice for entries carrying no `depicts`; a `web` block step
resolving as unpaired end to end through the run loop; and each of the three viewers degrading as
described above.

### Prime directives preserved

The change is confined to evidence and its readers. No new tree read is issued — `depicts` is
derived from arguments the capture path already holds — so BE-0234's read budget is untouched, and
`tests/orchestrator/test_read_count.py` pins that. Nothing here reaches a verdict: the artifacts a
step records and the assertions it evaluates stay independent, so `run` remains deterministic and
free of any LLM (prime directive 1). The field is backend-agnostic, carrying whatever `name` a
driver already reports (prime directive 3).

## Alternatives considered

**Keep the pre-action tree as its own file.** Writing the baseline tree to a second filename would
give `before.png` a tree of its own, so the fallback to `before.png` would keep its frames instead
of losing them. We rejected it because it doubles the tree bytes every step writes — the largest
per-step artifact after video — to serve a fallback that fires only when a stored run has lost a
file, and it fixes neither of the other two situations. Recording what an artifact depicts is what
all three need.

**Record the moment alone, without the driver.** A `before` / `after` field would settle the two
temporal situations and leave the `web` block one, where both artifacts are post-action and still
describe different screens. Naming the driver as well costs one string and covers all three.

**Fix the `web` block by projecting WebView coordinates onto the device screen.** The WebView's
origin and scale within the native screen would have to come from the bridge, which does not report
them, and any projection would be a second source of truth about where an element is. Refusing to
draw is honest and needs no new device data; a projection is a feature for its own item.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — record `depicts` on every captured artifact.
- [x] Unit 2 — tag the run loop's two capture calls.
- [x] Unit 3 — resolve the pair in one place.
- [x] Unit 4 — honor the resolver in the three viewers.
- [x] Unit 5 — bump the manifest schema and the documentation.

### Log

- Proposal and implementation landed together in one pull request (`propose-and-build`): the
  `depicts` field, the `step_view` resolver behind all three viewers, the manifest schema bump to
  version 9, and the documentation for both.

## References

- [BE-0341 — Capture per-step report evidence before the step acts](../BE-0341-pre-action-evidence-capture/BE-0341-pre-action-evidence-capture.md)
  — the item that gave every step a pre-action baseline, and whose deferred note this item settles.
- [BE-0234 — Speed up adb scenario runs (uiautomator dump bottleneck)](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md)
  — the per-step read budget this item leaves untouched.
- [`docs/evidence.md`](../../docs/evidence.md) — the evidence subsystem, including artifact
  provenance.
- [`docs/reporting.md`](../../docs/reporting.md) — `manifest.json` and the HTML report's element
  viewer.
