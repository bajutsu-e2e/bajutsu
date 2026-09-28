**English** · [日本語](BE-XXXX-report-video-expand-ja.md)

# BE-XXXX — Expand the video and sync it with a step panel in report.html

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-report-video-expand.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2071](https://github.com/bajutsu-e2e/bajutsu/pull/2071) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

report.html embeds each scenario's screen recording in a 300px-wide fixed column beside its Result
panel ([report.css:50](../../bajutsu/templates/report.css)). This item adds an expand control to
every recording. Pressing it opens a modal that shows the video large, with that scenario's step
list cloned beside it. The step list already highlights the step in progress and lets a click seek
the video. This item carries that same synchronization into the enlarged view.

## Motivation

A reader diagnosing why a scenario failed needs to see what happened on screen. report.html's video
column stays 300px wide regardless of the recording's own resolution
([report.css:64](../../bajutsu/templates/report.css)). A Web (Playwright) recording's resolution
often exceeds that width. Its column then renders small enough that button labels and icons become
hard to make out.

The step list already carries the synchronization a reader needs. Each step row stores its recording
offset in a `data-t` attribute. A click seeks the video there, and playback highlights the row in
progress
([report.html.j2:45](../../bajutsu/templates/report.html.j2),
[report.js:796](../../bajutsu/templates/report.js)–[report.js:825](../../bajutsu/templates/report.js)).
Nothing in the report offers a place to view the recording larger without losing that context.

After this item ships, pressing a recording's new expand button opens a modal with the recording
enlarged and the same step list, cloned, beside it. Clicking a cloned step seeks the enlarged video,
the same way a click seeks the video in the compact view today.

## Detailed design

### Adding the expand control

Add a button to each player's control bar (`.vctl`,
[report.html.j2:129](../../bajutsu/templates/report.html.j2)), beside the existing `.vplay`:

```html
<button class="vexpand" type="button" aria-label="expand video">⤢</button>
```

Style it in `report.css`, matching `.vplay`'s size and color
([report.css:66](../../bajutsu/templates/report.css)–[report.css:68](../../bajutsu/templates/report.css)).
Wire its click through the existing delegation pattern (`ROOT.addEventListener('click', ...)`,
[report.js:9](../../bajutsu/templates/report.js) onward). Find the button with
`e.target.closest('.vexpand')`, walk up to its `.player`, and call `vzOpen(player)`.

### The modal's skeleton

Add one new singleton to `report.html.j2`, alongside `.tv` and `.imgz`
([report.html.j2:131](../../bajutsu/templates/report.html.j2)):

```html
<div class="vz" id="vz">
  <div class="vz-box">
    <button class="vz-close" type="button" aria-label="close">✕</button>
    <div class="vz-tabs" hidden></div>
    <div class="vz-video"></div>
    <div class="vz-steps"></div>
  </div>
</div>
```

`report.css` gives it the same shape as `.tv` / `.tv-box`. `.vz` is a fixed, full-screen backdrop
like `.tv` ([report.css:148](../../bajutsu/templates/report.css)). `.vz-box` lays `.vz-video` and
`.vz-steps` out in a row: the video takes the flexible width (`flex:1`), and the step list takes a
fixed 320–360px. Showing the recording large is the point, so `.vz-box` gets a wider cap than
`.tv-box`'s `width:min(94vw,1040px)` ([report.css:195](../../bajutsu/templates/report.css)) —
something like `width:min(96vw,1400px);height:min(92vh,900px)`.

`.vz-tabs` gets its own button class, `.vz-tab`, styled to look like `.tab` without sharing its
name. `.tab`'s delegated click handler assumes `t.closest('.scn')`
([report.js:9](../../bajutsu/templates/report.js)–[report.js:14](../../bajutsu/templates/report.js)),
and `.vz-tabs` sits outside any `.scn`. Reusing `.tab` itself would let that handler misfire and
throw on the null `closest('.scn')`.

A fixed 320–360px step column leaves the video a sliver once `.vz-box` itself shrinks to `96vw`, on
a phone-width screen. Below 760px — the same breakpoint the existing `.body` grid already falls
back at ([report.css:51](../../bajutsu/templates/report.css)) — `.vz-box` switches to a column
layout, and `.vz-steps` takes `width:auto` and grows to fill the remaining height instead.

### Moving the video, not copying it

report.js's per-player setup loop
([report.js:545](../../bajutsu/templates/report.js)–[report.js:772](../../bajutsu/templates/report.js))
is a `ROOT.querySelectorAll('.player').forEach(function(p){ ... })`. Declare one new map right
before that `forEach`:

```js
var videoHome = new WeakMap();   // <video> -> its original .player element
```

Inside the loop, right after it resolves each player's `<video>` into a variable `v`, record where
that video came from:

```js
videoHome.set(v, p);
```

`vzMount(player)` moves that player's `<video>` *and* its `.vctl` control bar into `.vz-video`,
both with a plain `appendChild` — a DOM move, not a clone. Moving `.vctl` along with the video is
what keeps play/pause and the scrubber usable at the enlarged size; moving the video alone would
strand the controls on the now-empty player, behind the modal. Playback position, pause state, and
every listener already attached (`play` / `pause` / `timeupdate` / `syncSiblings`,
[report.js:746](../../bajutsu/templates/report.js)–[report.js:771](../../bajutsu/templates/report.js))
survive untouched — reparenting a node does not detach its listeners. The moved bar's own
`.vexpand` button finds no `.player` ancestor to reopen once it sits inside the modal it already
opened, so `vzMount` hides it there rather than leave a control that does nothing. `player` itself
gets `hidden` once both children have moved out.

`vzRestore()` reverses this: `home.appendChild(vzActive)` puts the video back, then the `.vctl`
still sitting in `.vz-video` — its `.vexpand` unhidden first — goes back the same way. Appending
both, in order, recreates `player`'s original child order. A video and its `.vctl` are the two
children `vzMount` ever takes out. Clearing `hidden` finishes the restore. Toggling a
player's `hidden` changes `.players`' own height, so `vzRestore` also calls the existing
`syncResultHeight(scn)`
([report.js:492](../../bajutsu/templates/report.js)–[report.js:504](../../bajutsu/templates/report.js))
to keep the Result tab's height bound current.

A multi-target scenario's other recordings
([BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md))
stay where they are and keep playing in sync. `syncSiblings`
([report.js:589](../../bajutsu/templates/report.js)) tracks the same `<video>` node, regardless of
which parent holds it right now.

### Cloning the step list

`vzBuildSteps(player)` clones the rows the existing `rowsFor(scn, target)` helper already scopes to
this player ([report.js:528](../../bajutsu/templates/report.js)). A single-target scenario clones
every row; a multi-target one clones the matching target's rows alone. `rowsFor` returns body rows
alone (`tr.srow[data-target]`), never a row's own companion rows (`.alertrow` / `.actrow` /
`.genrow` — a step can emit up to all three, [report.html.j2:45](../../bajutsu/templates/report.html.j2)).
So `vzBuildSteps` also clones each returned row's own following companion rows, where any exist.

Building the clone needs an explicit `<table class="sttbl vz-sttbl"><tbody></tbody></table>`, not
appended `<tr>`s alone. Every step-row rule scopes itself to `.sttbl>tbody>tr`
([report.css:342](../../bajutsu/templates/report.css)–[report.css:416](../../bajutsu/templates/report.css)).
The HTML parser is what inserts a `<tbody>` automatically; `appendChild`ing rows onto a bare
`<table>` does not.

The clone drops one thing and keeps another. It empties, rather than removes, the screenshot /
element-tree cell (`class="ev"`) on every cloned row: `td.textContent = ''`, not `td.remove()`.
`.sttbl`'s rows are a CSS grid keyed by `td:nth-child`
([report.css:383](../../bajutsu/templates/report.css)–[report.css:416](../../bajutsu/templates/report.css)).
Removing the cell shifts every later column into the wrong grid slot; emptying it leaves the column
in place, and the existing `table.sttbl>…>td:empty{display:none}` rule
([report.css:372](../../bajutsu/templates/report.css)) hides it. Emptying the cell also clears the
`class="shot"` / `class="treebtn"` markers it carries, which would otherwise reopen the existing
Element Viewer (`.tv`) on top of this modal
([report.js:274](../../bajutsu/templates/report.js)).

It keeps a folded group's rows, `hidden` attribute and all. A hidden row's presence in the clone
matches what report.js's own `timeupdate` highlighter already does in the compact view: the
highlighter can mark a hidden row "playing" and show no visible highlight until playback reaches
the next visible row. The existing `.sttbl>tbody>tr[hidden]{display:none!important}` rule
([report.css:357](../../bajutsu/templates/report.css)) already covers `.vz-sttbl` too.

One more step matters: stripping `data-group-id` from every cloned row. A `.grouphead` row itself
(the one carrying `.grouptoggle`) has neither `class="srow"` nor a `data-target`. `rowsFor` never
returns it, so no `.grouptoggle` button ever reaches the clone. A cloned body or companion row does
keep the `data-group-id` its original carries, though. The global delegated handler
([report.js:26](../../bajutsu/templates/report.js)–[report.js:34](../../bajutsu/templates/report.js))
matches every row sharing that id anywhere in `ROOT`, clone included. Left in place, that attribute
would let a click on the compact view's fold heading fold or unfold the clone's rows along with the
real ones. Stripping it rules that out. A fold the compact view toggles later, while the modal
stays open, does not reach the clone until the reader reopens the modal.

The clone gets its own click wiring, mirroring the existing per-row handlers
([report.js:796](../../bajutsu/templates/report.js)–[report.js:816](../../bajutsu/templates/report.js))
with `vzActive` standing in for the original `v`. A row click, or a `.stepjump` button inside it,
seeks the mounted video. Highlighting and autoscroll mirror the existing `timeupdate` handler too
([report.js:817](../../bajutsu/templates/report.js)–[report.js:825](../../bajutsu/templates/report.js)).
They scroll within `.vz-steps`, through the existing `scrollIntoBox` helper
([report.js:776](../../bajutsu/templates/report.js)). When playback marks a hidden, folded row
"playing", that helper reads a zero-sized rect for it — the same quirk the compact view's own
scrolling already has, and not something this item fixes.

### Tabs for a multi-target scenario

`vzOpen(player)` collects every player in the scenario (`scn.querySelectorAll('.player')`). A
single-target scenario hides `.vz-tabs` and mounts that one player. Two or more players get one tab
each, labeled by `data-target`. The button pressed to open the modal picks the starting tab.
Switching tabs calls `vzMount` again, which restores the previous target's video first. One
recording ever sits inside the modal at a time; every other target's player stays visible and
in sync in its ordinary compact form, the same way each one looked before the modal opened.

### Closing

`vzClose()` restores the video, clears the cloned steps and tabs, and drops `.open`. It wires the
same three exits `.tv` / `.imgz` already use
([report.js:278](../../bajutsu/templates/report.js)–[report.js:308](../../bajutsu/templates/report.js)):
a backdrop click (`e.target === vz`), the close button, and Escape. Escape defers to `.tv` / `.imgz`
when either is already open. This item never opens more than one of the three at once.

### Out of scope

- The Network / Device Log / App Trace tabs stay untouched. So does arrow-key step navigation, the
  `.tv` viewer's own previous/next controls. So does any mobile-specific modal layout: the modal
  reuses `.tv-box`'s own plain mobile behavior.
- Preconditions and expectations do not clone into the modal. Neither one carries a recording
  offset. Neither one takes part in the synchronization this item adds.

## Alternatives considered

| Option | Summary | Why not |
|---|---|---|
| Call the browser's native `video.requestFullscreen()` | A few lines, and it rides the OS's own enlarged playback | Fullscreen claims the video alone; the step list has nowhere to go. That leaves the motivating problem — reading the video and the step sequence together on a large screen — unsolved |
| Widen the video column in place, inside the existing `.body` grid, instead of a modal | No new modal skeleton; a `grid-template-columns` change on the existing layout ([report.css:50](../../bajutsu/templates/report.css)) would do it | The rest of the page — other scenario cards, the header — stays where it was, cramping the view a modal gives for free. The existing `.tv` viewer already chose a modal for the same reason |
| Move the original step rows into the modal instead of cloning them | One DOM copy of each row; no listener duplication to manage | A multi-target tab switch would then repeatedly pull rows out of, and back into, the live Result tab — a real risk of a row going missing mid-switch, or the Result tab reading empty for a moment. Cloning keeps the Result tab always complete while the modal is open |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the `.vexpand` button to `.vctl` and style it in `report.css`
- [x] Add the `.vz` modal skeleton to `report.html.j2` and its CSS to `report.css`, including a
      phone-width fallback that stacks the video and the step list instead of squeezing both into
      one row
- [x] Add `videoHome`, `vzMount`, and `vzRestore` to `report.js` — moving the player's `.vctl` bar
      along with its video, so the enlarged view keeps play/pause and the scrubber
- [x] Add `vzBuildSteps` (row cloning, seek wiring, highlight, and autoscroll)
- [x] Add multi-target tab switching to `vzOpen`
- [x] Wire the three close paths (backdrop, close button, Escape) with the `.tv` / `.imgz` precedence
- [x] Confirm dark mode needs no new color overrides — the new surfaces reuse the existing
      `--card` / `--ink` / `--line` tokens, and the chrome (`.vexpand`, `.vz-tab`, `.vz-close`)
      copies `.vplay`'s own fixed dark colors
- [x] Update `docs/reporting.md` and `docs/ja/reporting.md`
- [x] `make check` passes

## References

- [docs/specs/report-video-expand.md](../../docs/specs/report-video-expand.md) — the detailed
  technical design (Japanese) this item summarizes
- [BE-0428 — Multi-target scenario execution](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md)
  — the case of two or more recordings in one scenario, which this item's tabs handle
- [BE-0439 — Group steps into named sections, folded in report.html](../BE-0439-step-groups-report-folding/BE-0439-step-groups-report-folding.md)
  — the fold control this item's cloned step list keeps working around, and how
- [docs/reporting.md](../../docs/reporting.md#reporthtml) — the doc this item updates
