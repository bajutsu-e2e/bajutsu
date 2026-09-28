**English** · [日本語](BE-0442-report-video-expand-ja.md)

# BE-0442 — Expand the video and sync it with a step panel in report.html

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0442](BE-0442-report-video-expand.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0442") |
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
[report.js:858](../../bajutsu/templates/report.js)–[report.js:885](../../bajutsu/templates/report.js)).
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
([report.js:582](../../bajutsu/templates/report.js)–[report.js:822](../../bajutsu/templates/report.js))
is a `ROOT.querySelectorAll('.player').forEach(function(p){ ... })`. Declare one new map right
before that `forEach`:

```js
var videoHome = new WeakMap();   // <video> -> its original .player element
```

Inside the loop, right after it resolves each player's `<video>` into a variable `v`, and before the
loop's own early return for a player missing `.vplay` / `.vseek` / `.vtime`, record where that video
came from:

```js
if(v) videoHome.set(v, p);
```

The `if(v)` guard matters: `WeakMap.set` takes an object key alone, and `v` is `null` on a player with
no `<video>` at all — calling `set` on that key throws. Registering ahead of the early return matters
too: a player can carry a `<video>` while missing one of the three controls the return checks for,
and `vzMount` never consults those controls itself — it touches merely the video and its `.vctl`.
Registering after the return would leave such a video with no recorded home. Closing the modal would
then drop the recording instead of restoring it.

`vzMount(player)` moves that player's `<video>` *and* its `.vctl` control bar into `.vz-video`,
both with a plain `appendChild` — a DOM move, not a clone. Moving `.vctl` along with the video is
what keeps play/pause and the scrubber usable at the enlarged size; moving the video alone would
strand the controls on the now-empty player, behind the modal. Playback position, pause state, and
every listener already attached (`play` / `pause` / `timeupdate` / `syncSiblings`,
[report.js:796](../../bajutsu/templates/report.js)–[report.js:822](../../bajutsu/templates/report.js))
survive untouched — reparenting a node does not detach its listeners. The moved bar's own
`.vexpand` button finds no `.player` ancestor to reopen once it sits inside the modal it already
opened, so `vzMount` hides it there rather than leave a control that does nothing. `.vexpand`
carries its own `display:flex`, which outranks the UA stylesheet's `[hidden]{display:none}`
regardless of specificity — `report.css` restates the rule as `.vexpand[hidden]{display:none}`, the
same way `.vz-tabs[hidden]` and `.sttbl>tbody>tr[hidden]` already do for their own `display`
declarations. `player` itself gets `hidden` once both children have moved out.

`vzRestore()` reverses this: `home.appendChild(vzActive)` puts the video back, then the `.vctl`
still sitting in `.vz-video` — its `.vexpand` unhidden first — goes back the same way. Appending
both, in order, recreates `player`'s original child order. A video and its `.vctl` are the two
children `vzMount` ever takes out. Clearing `hidden` finishes the restore. Toggling a
player's `hidden` changes `.players`' own height, so `vzRestore` also calls the existing
`syncResultHeight(scn)`
([report.js:529](../../bajutsu/templates/report.js)–[report.js:541](../../bajutsu/templates/report.js))
to keep the Result tab's height bound current.

A multi-target scenario's other recordings
([BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md))
stay where they are and keep playing in sync. `syncSiblings`
([report.js:639](../../bajutsu/templates/report.js)) tracks the same `<video>` node, regardless of
which parent holds it right now.

### Cloning the step list

`rich()` (report.html.j2) renders up to three `.steps-sec` blocks per scenario — `before`, the
scenario's own steps, `after` — each with its own `.deflbl` heading and its own `.sttbl`, whose row
numbers restart at 0. Flattening all three into one table would collide two different steps both
numbered "0" and drop which phase a row belongs to. So `vzBuildSteps(player)` walks the scenario's
own `.steps-sec` blocks and builds one `.vz-sttbl` per section instead of one shared table for the
whole scenario.

Within each section's loop, `vzBuildSteps` reads that section's own `table.sttbl`
(`section.querySelector('table.sttbl')`) and collects
`srcTable.querySelectorAll('tr.srow[data-target], tr.skip[data-target]')` in document order.
`[data-target]` matters on both halves of the selector, the same guard `rowsFor` itself applies —
scoping `srcTable.querySelectorAll` to one `.steps-sec`'s own `table.sttbl` is what keeps an
unevaluated `expect`'s own `tr.skip` (the expectations table's `.extbl`, the `exrow` macro,
[report.html.j2:61](../../bajutsu/templates/report.html.j2)) out of the clone, not this attribute:
that row carries no `data-target` of its own either way, but `.extbl` sits outside every
`.steps-sec` — a sibling of `.rich-scroll` in `rich()` — so it is never in scope to begin with.
Dropping the guard on `tr.skip` would still be wrong, though: without it, a future row type that
does carry a `data-target` and does live inside a `.steps-sec` could reach this query unfiltered.

`vzBuildSteps` keeps each candidate row when `r.classList.contains('skip') || mine.indexOf(r) !== -1`,
where `mine` is the result of the existing `rowsFor(scn, target)` helper
([report.js:565](../../bajutsu/templates/report.js)), called once before the section loop starts. It
keeps a skip row — a step that never ran, execution stopped at an earlier failure
([rows.py](../../bajutsu/common/report/rows.py)'s `_step_skip_row`) — unconditionally: the row
carries no `target` of its own (the step that would have run it never got the chance to name one),
which rules out scoping it to one player's tab the way an executed row can. Keeping every one matches
what the compact table already shows — the same skipped steps, once, regardless of which target
was next in line. A single-target scenario's own `rowsFor` call returns every row it has; a
multi-target one returns the matching target's own alone. `rowsFor` returns body rows alone
(`tr.srow[data-target]`), never a row's own companion rows (`.alertrow` / `.actrow` / `.genrow` — a
step can emit up to all three, [report.html.j2:45](../../bajutsu/templates/report.html.j2)). So
`vzBuildSteps` also clones each kept row's own following companion rows, where any exist. A skip
row carries no `data-t`. `vzBuildSteps` clones it, but adds body rows alone
(`r.classList.contains('srow')`) to `cloneRows`, the highlight/seek array.

A multi-target scenario can leave every step of one target skipped, when another target's step fails
first. That target's own player has no `tr.srow` at all, yet the scenario's shared skip rows still
belong in its modal. Within one section, `vzBuildSteps` skips building a table at all once that
section's own `tbody` ends up with no children — checking that count, not `mine`'s length alone.
Across every section, it bails out of the whole function only when it has cloned nothing anywhere.

Each section that did clone at least one row gets its own `<span class="deflbl">`, its text copied
from the section's own heading, followed by its own
`<table class="sttbl vz-sttbl"><tbody>…</tbody></table>`. Building the clone needs this explicit
`tbody`, not appended `<tr>`s alone. Every step-row rule scopes itself to `.sttbl>tbody>tr`
([report.css:342](../../bajutsu/templates/report.css)–[report.css:416](../../bajutsu/templates/report.css)).
The HTML parser is what inserts a `<tbody>` automatically; `appendChild`ing rows onto a bare
`<table>` does not.

The clone drops one thing and keeps another. It empties, rather than removes, the screenshot /
element-tree cell (`class="ev"`) on every cloned row: `evCell.textContent = ''`, not
`evCell.remove()`. `.sttbl`'s rows are a CSS grid keyed by `td:nth-child`
([report.css:383](../../bajutsu/templates/report.css)–[report.css:416](../../bajutsu/templates/report.css)).
Removing the cell shifts every later column into the wrong grid slot; emptying it leaves the column
in place, and the existing `table.sttbl>…>td:empty{display:none}` rule
([report.css:372](../../bajutsu/templates/report.css)) hides it. Emptying the cell also clears the
`class="shot"` / `class="treebtn"` markers it carries, which would otherwise reopen the existing
Element Viewer (`.tv`) on top of this modal.

It always shows a folded `group:` block's rows, clearing `hidden` on every cloned body and companion
row (`clone.hidden = false`). The compact view folds such a block by default; its member rows carry
`hidden` there. The modal has no fold control of its own — `.grouphead`, the heading row carrying
`.grouptoggle`, has neither `class="srow"` nor a `data-target`, which rules it out as a candidate — it
never clones. A row left `hidden` in the clone would be unreachable rather than merely folded, since
nothing in the modal can unfold it. Showing it unconditionally avoids that dead end.

One more step matters: stripping `data-group-id` from every cloned row. A cloned body or companion
row keeps the `data-group-id` its original carries unless this step removes it. The global delegated
handler ([report.js:26](../../bajutsu/templates/report.js)–[report.js:34](../../bajutsu/templates/report.js))
matches every row sharing that id anywhere in `ROOT`, clone included. Left in place, that attribute
would let a click on the compact view's fold heading fold or unfold the clone's rows along with the
real ones. Stripping it rules that out.

The clone gets its own click wiring: one delegated handler on `.vz-steps` itself, added once per
`vzBuildSteps` call (and removed before the next one rebuilds the container). A single container
listener, rather than one per row, follows from the per-section rebuild above — `vzBuildSteps`
throws away and recreates every row's markup on each call, so wiring the container once is simpler
than re-wiring every row. The handler checks `.stepjump` first and calls `e.stopPropagation()` before
falling through to the row itself, the same ordering the compact view's own per-row handlers use, so
a jump button's click never also re-seeks to the row's own start right after. A row click or a
`.stepjump` button inside it seeks the mounted video (`vzActive`, standing in for the compact view's
own `v`).

Highlighting and autoscroll mirror the compact view's own `timeupdate` handler, applied to
`cloneRows`. Which row counts as "playing" at a given time is itself shared: `pickPlayingRow(rows,
currentTime)` ([report.js:837](../../bajutsu/templates/report.js)–[report.js:844](../../bajutsu/templates/report.js))
returns the row with the latest `data-t` at or before `currentTime`, and both the compact view's own
`timeupdate` handler and `vzBuildSteps`'s call the same function — keeping that rule in one place
rules out the compact view and the modal drifting apart if it changes later. Autoscroll targets
`.vz-steps` itself, through the existing `scrollIntoBox` helper
([report.js:826](../../bajutsu/templates/report.js)), with `.vz-steps` in place of the compact
view's own scroll container. `vzBuildSteps` clears every cloned row's `hidden`, so no row that
playback marks "playing" ever reads a zero-sized rect from `scrollIntoBox` — the quirk a hidden row
would have caused does not arise here.

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
  `.tv` viewer's own previous/next controls.
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
