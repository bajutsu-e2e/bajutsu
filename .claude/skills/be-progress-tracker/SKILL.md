---
name: be-progress-tracker
model: haiku
description: Create or update a per-BE-item status Artifact (overview, implementation progress, work log). Called by ideation, implement-be, or propose-and-build at their own step boundaries — not run standalone.
---

# BE progress tracker

Maintain one glanceable, continuously updated status page per BE item while another BE workflow
(`ideation`, `implement-be`, `propose-and-build`, or a future one) does the real work. This skill
only records and displays; it never authors code, proposals, roadmap files, or PR content, and it
never decides anything the calling workflow hasn't already decided itself.

## Scope: transcription, not judgment

This is deliberately a **cheap, mechanical** skill: every call formats decisions the calling
workflow already made into a short status document. If it ever finds itself deciding something —
which step comes next, whether a design is sound, what the item's `Status` should be — that is a
sign it has drifted into the calling workflow's job; stop and hand the decision back. Keep the
document terse: it is a live dashboard for the human watching the session, not a rewrite of the
roadmap item or the PR body, and it is never the source of truth for either.

## When it runs

Called by another BE workflow's own step boundaries — never started for its own sake. The calling
workflow hands it, at each checkpoint:

- the BE id, or the `BE-XXXX` placeholder before allocation;
- the calling workflow's name and the step it just completed or is now entering;
- one short sentence of what happened — the work-log line;
- the roadmap item's own repo-relative path (`roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md`), once it
  exists — omitted before allocation, since the file (and its slug) don't exist yet;
- the handle of the page an earlier checkpoint already created for this id, when there is one, so
  this call updates that page instead of starting a second one.

A calling workflow decides for itself which of its own steps are worth a checkpoint — typically the
same boundaries that already warrant a user-facing update (a branch created, a plan confirmed, code
written, a review pass clear, the gate green, a PR opened, one follow-up-loop iteration). Skipping a
checkpoint that would add no new information is expected, not an error.

Dispatch each checkpoint through the Agent tool with `model: "haiku"` passed explicitly — a
subagent call does not inherit this skill's own frontmatter model. The job is transcription and
formatting of decisions the caller already made, not new judgment, so the cheapest capable model is
the right default; a caller can still upshift for an item whose overview is unusually hard to
summarize.

## The template (verbatim, every call)

The page is exactly this shape — four headers, in this order, nothing added and nothing renamed.
Copy the structure below literally, `<style>` block included; the only things that change between
calls are the placeholder content, the one status class, and whether the roadmap-link button is
present, all noted in the field rules.

```html
<title>{ID} — {Title}</title>
<style>
  :root{
    --bg:#fafafa; --fg:#18181b; --muted:#71717a; --border:#e4e4e7; --accent:#4f46e5;
    --done-bg:#dcfce7; --done-fg:#15803d;
    --progress-bg:#fef3c7; --progress-fg:#b45309;
    --neutral-bg:#e0e7ff; --neutral-fg:#3730a3;
    --mark-done:#16a34a; --mark-progress:#d97706; --mark-pending:#a1a1aa;
  }
  @media (prefers-color-scheme: dark){
    :root:not([data-theme="light"]){
      --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa; --border:#3f3f46; --accent:#a5b4fc;
      --done-bg:#14532d; --done-fg:#86efac;
      --progress-bg:#78350f; --progress-fg:#fcd34d;
      --neutral-bg:#312e81; --neutral-fg:#c7d2fe;
      --mark-done:#4ade80; --mark-progress:#fbbf24; --mark-pending:#71717a;
    }
  }
  :root[data-theme="dark"]{
    --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa; --border:#3f3f46; --accent:#a5b4fc;
    --done-bg:#14532d; --done-fg:#86efac;
    --progress-bg:#78350f; --progress-fg:#fcd34d;
    --neutral-bg:#312e81; --neutral-fg:#c7d2fe;
    --mark-done:#4ade80; --mark-progress:#fbbf24; --mark-pending:#71717a;
  }
  body{background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.5;padding:24px 28px;max-width:760px;margin:0 auto;}
  h1{font-size:1.4em;margin:0 0 10px;}
  h2{font-size:1.05em;margin:28px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--border);}
  .meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:0.9em;color:var(--muted);}
  .badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:0.85em;font-weight:600;}
  .badge.done{background:var(--done-bg);color:var(--done-fg);}
  .badge.progress{background:var(--progress-bg);color:var(--progress-fg);}
  .badge.neutral{background:var(--neutral-bg);color:var(--neutral-fg);}
  .link-btn{display:inline-flex;align-items:center;gap:4px;padding:2px 10px;border-radius:999px;border:1px solid var(--accent);color:var(--accent);text-decoration:none;font-size:0.85em;font-weight:600;}
  .link-btn:hover{background:var(--accent);color:var(--bg);}
  .progress-list{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px;}
  .progress-list li{display:flex;gap:8px;}
  .progress-list .mark{flex:none;}
  .progress-list .done .mark{color:var(--mark-done);}
  .progress-list .active{color:var(--fg);font-weight:600;}
  .progress-list .active .mark{color:var(--mark-progress);}
  .progress-list .pending{color:var(--muted);}
  .progress-list .pending .mark{color:var(--mark-pending);}
  .worklog{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px;}
  .worklog li{font-size:0.92em;}
  .worklog time{color:var(--muted);font-family:ui-monospace,monospace;margin-right:8px;}
</style>

<h1>{ID} — {Title}</h1>
<div class="meta">
  <span class="badge {status-class}">{Status}</span>
  <a class="link-btn" href="{roadmap GitHub URL}">Roadmap item ↗</a>
  <span>· {calling workflow name}</span>
  <span>· updated {timestamp}</span>
</div>

<h2>Overview</h2>
<p>{One paragraph, 1-3 sentences, plain language. No sub-headers, no bullets.}</p>

<h2>Progress</h2>
<ul class="progress-list">
  <li class="done"><span class="mark">✓</span> 1. {step title, copied verbatim from the calling workflow's own step list}</li>
  <li class="active"><span class="mark">▶</span> 2. {step title} — in progress</li>
  <li class="pending"><span class="mark">○</span> 3. {step title}</li>
</ul>

<h2>Work log</h2>
<ul class="worklog">
  <li><time>{timestamp}</time>{one sentence, ending in a period.}</li>
  <li><time>{timestamp}</time>{earlier entry, unchanged from the last call}</li>
</ul>
```

Field rules, all mandatory:

- **`{ID}`** — the BE id (`BE-0123`) or the `BE-XXXX` placeholder, verbatim, always plain text in
  the `<h1>`. **`{Title}`** — the roadmap item's own title once it exists, else the calling
  workflow's current working title.
- **Roadmap-link button** — once the calling workflow has handed over the roadmap item's
  repo-relative path, include the `<a class="link-btn" href="https://github.com/bajutsu-e2e/bajutsu/blob/main/{that path}">Roadmap item ↗</a>`
  button verbatim, right after the status badge — it needs its own border/hover styling to read as
  clickable, unlike the badge next to it. Before allocation — no path handed over yet — drop the
  button entirely rather than pointing it at a path that doesn't exist on `main` yet.
- **Meta line** — the badge, the roadmap-link button when present, then exactly the two remaining
  fields in that order, joined by ` · `, nothing else ever added to it.
  - `Status` — the roadmap item's own `Status:` field, copied verbatim, once
    `roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md` exists; before allocation, literally
    `Proposal (pre-allocation)`. Its badge gets exactly one of three classes, chosen only by the
    text itself — never a judgment call: `done` when `Status` is exactly `Implemented`, `progress`
    when it is exactly `In progress`, `neutral` for every other value (`Proposal`,
    `Proposal (pre-allocation)`, `Deferred`, `Rejected`).
  - `Workflow` — the name of the workflow making *this* call (`implement-be`, `ideation`, …), not
    the skill that ends up shipping the item — this field changes mid-page when
    `propose-and-build` hands off between the two.
  - `{timestamp}` — always `YYYY-MM-DD HH:MM UTC`, from `date -u +"%Y-%m-%d %H:%M UTC"`. Never a
    relative time ("5 min ago"), never a different format, never the local timezone.
- **Overview** — rewritten in full from the current source (the roadmap file, or the calling
  workflow's draft) each call; it is a snapshot, not an append log. Read from
  `roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md` once it exists; before allocation, read from
  whatever draft the calling workflow has produced so far.
- **Progress** — one `<li>` per step of the calling workflow's own step list (e.g. `implement-be`'s
  numbered steps, or `ideation`'s), numbered to match that list, with the step's title copied
  verbatim — never paraphrased or shortened. Exactly three line shapes, no other suffix, class, or
  annotation is ever added:
  - done: `<li class="done"><span class="mark">✓</span> {n}. {title}</li>`
  - in progress: `<li class="active"><span class="mark">▶</span> {n}. {title} — in progress</li>`
  - pending: `<li class="pending"><span class="mark">○</span> {n}. {title}</li>`
- **A checkpoint that reports a *unit within* a step** — `implement-be`'s step 6 checks in once
  per plan unit — leaves that step's line at `active` / `— in progress` and records the unit in the
  Work log alone. Never add a sub-list, a unit count, or any extra Progress line for it.
- **Work log** — newest entry first (prepend, don't append at the bottom). One `<li>` per
  checkpoint, each shaped exactly `<li><time>{timestamp}</time>{one sentence}.</li>` — a single
  sentence, past tense, ending in a period, no line breaks inside it. Never rewrite or delete a
  past entry.

## How to update

- **First call for a given BE id in a session** — read the roadmap item if it exists yet, for the
  Overview; seed Progress with every step of the calling workflow, ticking only the ones already
  done; start the Work log with one entry.
- **Every later call carries no memory of the earlier ones** — each checkpoint runs as its own
  subagent, so it knows only what this call was handed, not what an earlier checkpoint wrote. Read
  the existing page first (`Artifact({action: "read", url})`) and carry its Progress `<li>`
  classes and Work log entries forward verbatim, advancing Progress and prepending exactly one new
  Work log `<li>` above them. Rebuilding the page from this call's input alone would silently drop
  every earlier entry — that is the one failure this step exists to prevent. When the existing page
  can't be read, say so as a Work log line — for example
  `<li><time>{timestamp}</time>Could not read the existing page; entries before this point may be missing.</li>`
  — rather than quietly starting a fresh log.
- **Never invent status.** If the calling workflow hasn't reported a step as done, leave it pending
  — don't infer it from what "usually" happens next.
- **Never add a section, a field, or a line shape not in the template above.** If a call has
  information that doesn't fit an Overview/Progress/Work-log line, drop it rather than growing the
  template — a caller with a real recurring need should change this skill, not the one call.

## Output

The page is an HTML Artifact, published and later redeployed with the Artifact tool. Load
`artifact-design` before the first write. The template above is already the whole design — a
color-coded status badge and progress marks so the human watching the session can scan state at a
glance; fill its placeholders and don't add anything beyond them. It is still a live dashboard, not
a polished deliverable: color and decoration exist to speed up scanning, not to grow the page.

**One Artifact per BE item, updated in place.** On the first call for a BE id in a session, check
whether one already exists — `Artifact({action: "list"})`, or a URL the calling workflow cached at
an earlier checkpoint — before publishing a new one; every later call redeploys to that same URL by
passing the same `file_path` rather than creating a fresh Artifact per checkpoint. Hand the URL back
to the calling workflow so it can reuse it next time. Pick the `favicon` on the first call and keep
it stable across every redeploy of the same item, per the Artifact tool's own rule; only a hard
pivot in what the item is — which shouldn't happen mid-implementation — would justify changing it.
The `<title>` tag in the template already supplies the Artifact's title each redeploy, so no
separate `title` param is needed.

## Non-goals

- **Never a source of truth.** The roadmap item's own files remain canonical for `Status` and
  `Implementing PR`; the PR remains canonical for the code and its review state. If this page and
  either of those ever disagree, the roadmap item and the PR are right, not this page.
- **Never blocks the calling workflow.** If a checkpoint call fails, or the host has no cheap way to
  make it, the calling workflow notes that tracking didn't update and continues regardless — this
  skill is a convenience, never a gate.
- **Never edits** roadmap files, code, commits, or the PR. It only ever writes its own status page.

## References

- [`implement-be`](../../../.apm/skills/implement-be/SKILL.md), [`ideation`](../../../.apm/skills/ideation/SKILL.md),
  [`propose-and-build`](../../../.apm/skills/propose-and-build/SKILL.md) — the workflows that call this one at
  their own step boundaries; `propose-and-build` inherits the checkpoints of the two it delegates
  to rather than defining its own.
