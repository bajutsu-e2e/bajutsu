**English** · [日本語](ja/contributor-workflow-tutorial.md)

# Contributor workflow tutorial

> A hands-on walkthrough of *contributing to* Bajutsu. By the end, you will have taken one idea from
> a rough sentence through `/ideation` to a merged proposal. You will have watched CI allocate its
> permanent `BE-NNNN` id. You will have shipped the idea with `/implement-be` to a merged pull
> request. The [getting-started tutorial](getting-started/index.md) does this for *running*
> Bajutsu, and this page does the analogous thing for *building* it. The reference pages tell you
> what each rule is. This page puts your hands on the keyboard, in order.

Related: [roadmap-workflow](roadmap-workflow.md) · [ai-development](ai-development.md) · [`CONTRIBUTING.md`](../CONTRIBUTING.md) · [roadmaps/README](../roadmaps/README.md)

> **What this is not.** This page does not re-explain the BE-ID lifecycle, the model tiers, or the
> PR template. [ai-development](ai-development.md) and [roadmap-workflow](roadmap-workflow.md) own
> those topics, and this tutorial links to them instead of copying them. Read this page first to
> *do* your first contribution, and reach for those pages when you want the full rule.

---

## Before you start

You need a working checkout and a green gate, exactly as in
[getting-started](getting-started/index.md#step-1--install):

```bash
uv sync --group dev   # .venv (Python 3.13) + deps + dev tools
make setup            # the above, plus wiring the tracked git hooks (run once on a fresh clone)
make check            # the deterministic gate; must be green (full step list in CLAUDE.md)
```

`make check` is the **contract**: it mirrors CI exactly, needs no Simulator, and runs anywhere. You
will run it again at the end of both halves below. Nothing in this tutorial needs an API key. The
skills run in your editor session, and the deterministic gate reaches no model.

The whole journey has two halves, each with a dedicated skill:

| Half | Skill | You start with | You end with |
|---|---|---|---|
| **Author** | `/ideation` | a rough idea | a merged proposal, with a CI-allocated `BE-NNNN` |
| **Ship** | `/implement-be BE-NNNN` | the merged proposal | a merged implementation PR, item flipped to `Implemented` |

The two skills are deliberate counterparts: one fills the [roadmap](../roadmaps/README.md), and the
other empties it. The conceptual "why two skills, not one" is in
[roadmap-workflow](roadmap-workflow.md#why-two-skills-not-one). Here we walk through both skills.

---

## Part A — Author a proposal with `/ideation`

### Step A1 — Bring an idea, however rough

You do not need a finished design, and a sentence is enough. For this walkthrough, imagine that you
keep hitting a step that occasionally fails on a slow simulator, and you think:

> "Bajutsu should retry a step that flakes instead of failing the whole run."

That sentence is deliberately underspecified. Part of what `/ideation` does is *sharpen* it. The
[worked example](#a-worked-example-a-vague-idea-becomes-a-scoped-proposal) below shows the
before and after.

### Step A2 — Run `/ideation` and let it ground itself

In your session, invoke the skill:

```
/ideation
```

The skill is a **sounding board**, not a blank page. Before it proposes anything, it reads:

- [`roadmaps/README.md`](../roadmaps/README.md): what is already out of scope, and unsorted ideas.
- The [implementation-status table](architecture.md#implementation-status), so it never "proposes"
  something already shipped.
- The BE items near your topic.

Then it ideates *with* you. It offers bounded shapes and asks the questions that sharpen scope: who
the idea is for, and which [tier](glossary.md#the-two-tiers) it touches. Above all, it asks **what
the machine-checkable outcome is**, meaning what a deterministic `run` or unit test could verify. An
idea that cannot be built inside the
[prime directives](../CLAUDE.md#prime-directives-do-not-violate) is reshaped to fit and never
dropped.

The skill classifies each surviving idea into one of three landings and tells you which it chose:

- **overlaps an existing item** → it augments that item instead of duplicating it;
- **novel and scoped** → it drafts a new item;
- **still unformed** → it parks a bullet under *Unsorted ideas* to promote later.

### Step A3 — It drafts the item with a placeholder id

When an idea lands as "novel and scoped", the skill scaffolds the files:

```bash
make new-roadmap-item SLUG=retry-flaky-step TITLE="Bounded retry for transiently-blocked steps"
```

This command creates `roadmaps/BE-XXXX-retry-flaky-step/` with **both** language files in the
canonical Swift-Evolution format. The skill then fills the `TBD` sections (Introduction,
Motivation, the MECE `Detailed design`, `Alternatives considered`). It also rewrites the Japanese
side as natural Japanese.

The literal `BE-XXXX` is intentional: **you never pick the number by hand.** IDs are permanent and
monotonic, and many branches are in flight at once. Picking a number by hand races two PRs onto the
same number. CI allocates the number at merge time (Step A5).

### Step A4 — Self-review, verify, and open the proposal PR

Before committing, mirror the CI "Claude review" workflow locally. Do not wait for the PR to catch
anything. A fresh subagent applies the contract at
[`.github/claude-review-prompt.md`](../.github/claude-review-prompt.md) to the staged diff. That
subagent is blind to the authoring conversation, the same way the actual CI reviewer is. It fixes
every finding, with two exceptions:

- A false positive or an already-explained trade-off: the skill notes it and leaves it as-is.
- A finding that calls for a genuine design change: the skill escalates it to you instead.

The review runs for at most 3 rounds. See [`ideation`](../.apm/skills/ideation/SKILL.md) step 5
for the exact procedure.

The gate stays green even for a docs-only change:

```bash
make check
```

Then push your branch and **open the PR**. A proposal PR is purely documentation, so per the
[working agreement](../CLAUDE.md) it opens **Ready for review** (not Draft) with the
`steering-committee` team as reviewer:

```bash
gh pr create --reviewer bajutsu-e2e/steering-committee \
  --title "docs(roadmap): propose bounded retry for transiently-blocked steps" \
  --body "…"
```

The title carries **no** `[BE-NNNN]` prefix, because the id does not exist yet. A PR that
*introduces* a roadmap item keeps the plain scoped subject.

> **`/ideation` will not auto-open this PR for you.** A proposal is a human checkpoint, so the
> authoring skills stop at pushing the branch. You (the human) open the PR. `/implement-be` is the
> opposite: it *does* auto-open its PR (Step B5), because its output is always gate-green code.

### Step A5 — merge; CI allocates the real `BE-NNNN` on `main`

Once review is happy, merge the PR as it is, with `BE-XXXX` intact. Allocation happens after the
merge, not while the PR is still open. The [`roadmap-id`](../.github/workflows/roadmap-id.yml)
workflow triggers on every push to `main`. It runs
[`scripts/allocate_roadmap_ids.py`](../scripts/allocate_roadmap_ids.py) against `main`'s own tree.

The workflow claims the next free id in merge order. It renames `BE-XXXX` → `BE-NNNN`
**everywhere** (the directory, both files, cross-links) and commits the result directly to `main`.
Finally, it posts a comment on your merged PR announcing the allocated id
([BE-0089](../roadmaps/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md)).
The item now exists at its permanent path with `Status: Approved`, and its number is allocated for
good. That number is the input to Part B.

---

## A worked example: a vague idea becomes a scoped proposal

The fastest way to learn "scoped enough" is to see a weak idea and its reshaped form side by side.
This side-by-side comparison shows what `/ideation`'s questions do to the Step A1 sentence.

**❌ Before — an underspecified one-liner:**

> "Add retry to flaky steps."

A reviewer cannot act on this sentence, and it brushes a prime directive. Three gaps explain why:

- **No bound.** "Retry" how many times? A blind retry loop hides flakiness instead of surfacing it.
  That fights **determinism first**: a step that passes on the third attempt alone is a real signal,
  not noise to paper over.
- **No trigger.** Retry *what* condition? Any failure, or just a transient, self-clearing blocker?
  The two are different features.
- **No machine-checkable outcome.** What would a deterministic test assert to prove it works? The
  sentence leaves that unstated.

**✅ After — the shape `/ideation` steers you toward:**

> **Bounded retry for transiently-blocked steps.** When a step fails *because a known,
> self-clearing blocker was present* (e.g. a system alert that `on_blocked` dismisses), retry the
> step **exactly once** after the blocker clears — never a general "keep trying until it passes"
> loop. **Tier:** the deterministic run loop. **Machine-checkable outcome:** a unit test with a fake
> driver that fails a step once with a clearable blocker, then succeeds, asserts the run passes in
> exactly two attempts; a step that fails for any *other* reason still fails immediately, asserted by
> a second test. **Prime-directive check:** the retry is condition-gated and bounded, so it stays
> deterministic — it does not mask flakiness, it recovers from a *named* transient state. No LLM on
> the `run` path.

The reshaped version names its scope, the tier it touches, and the exact assertion that proves it.
It also names the tension it had with a directive and how that tension resolved. That version is a
spec you can hand to `/implement-be`. (This is, in fact, roughly how the real
["retry once after `on_blocked` clears a blocker"](run-loop.md) behavior in the run loop is
bounded.)

**What "good" looks like on real, merged items — click through them:**

- [BE-0214 — Web-only beginner tutorial](../roadmaps/BE-0214-web-only-beginner-tutorial/BE-0214-web-only-beginner-tutorial.md)
  is a **docs-shaped** item. Read its `Detailed design` to see how a documentation change still
  breaks into a MECE checklist. Follow its `Implementing PR` link to the real diff.
- [BE-0017 — MCP server](../roadmaps/BE-0017-mcp-server/BE-0017-mcp-server.md) is a **code-shaped**
  item. Its `Detailed design` enumerates the surface it adds. Its PR (`[BE-0017] feat(mcp): add
  MCP server`) shows the title convention and the item→PR back-link in practice.

Reading a merged item's proposal, its `Progress` log, and the PR it links is the most reliable way
to calibrate your own proposal before you open it.

---

## Part B — Ship it with `/implement-be`

Now the proposal is merged and carries a real id, say `BE-0300`. The proposal's **Detailed design**
is your spec. The deterministic gate is the judge, never an LLM.

### Step B1 — Start the skill

```
/implement-be BE-0300
```

The skill accepts a full id, a bare number (`300`), or a slug fragment. First it **explains the item
back to you**: id, title, a plain-language summary, and current state. Then it notes that the item
was already accepted at its review-gated merge (two approvals, or one under the
`single-approver proposal` waiver). Implementing it **starts** it, and this PR will flip it to
`Implemented`.

### Step B2 — It claims the tracking issue

Every open item has a GitHub tracking issue labeled `roadmap-tracking`. The skill checks who is
assigned. If someone else already holds the issue, the skill **stops** and tells you, rather than
colliding with their work. If the issue is free (or already yours), the skill self-assigns it and
continues. This self-assignment is how parallel sessions signal ownership.

### Step B3 — It grounds, plans, and asks before writing code

The skill reads the Detailed design and *Alternatives considered*. The latter records paths already
rejected, often for directive reasons, so the skill does not re-propose them. The skill opens every
file the proposal links. It also checks that no prerequisite item is itself still a proposal.

A whole roadmap item is large and hard to reverse. So the skill **presents a concrete plan and
waits for your go-ahead**. The plan covers:

- the files it will touch
- the machine-checkable outcome that proves it works, and where AI is and is not allowed to sit
- the tests
- the docs that must move in both languages
- any tension with the prime directives

You confirm before the skill writes a line of code.

### Step B4 — It implements, reviews, flips the item, and runs the gate

Implementation matches the codebase grain: strict `mypy`, configured `ruff`, condition waits
instead of `sleep`, and new knobs in `targets.<name>` config. Any behavior change gets a test as
the regression net.

The skill then refines the diff with two authoring aids that advise and never judge: the built-in
`simplify` skill, and a review pass against the `.github/claude-review-prompt.md` contract. It
re-runs the contract pass against the updated diff until the pass comes back clean. The cap is 3
rounds, like Step A4 above. If a real finding still stands, the skill escalates to you and leaves
the PR unopened.

Only then does the skill flip the item to `Status: Implemented` in both language files and add the
`Implementing PR` row. The dashboard picks up the new status straight from that metadata, with
nothing else to regenerate. Then comes the gate:

```bash
make check   # must be green; never push red
```

### Step B5 — It auto-opens the PR; you merge

Unlike `/ideation`, `/implement-be` **opens its own PR**. Its output is always a self-contained,
gate-green change with every review finding already resolved, so there is nothing to wait for. The
PR is **Draft by default**. Its title carries the id
(`[BE-0300] feat(run): bounded retry for transiently-blocked steps`), and its body is thorough,
following the [template](../.github/PULL_REQUEST_TEMPLATE.md). (A *doc-only* item is the exception.
It opens Ready with the `steering-committee` reviewer, exactly like the proposal PR did.)

From there, a paced follow-up loop drives the mechanical tail: fixing CI and replying to review
comments. The human alone marks a Draft PR ready (`gh pr ready`) and merges it. That sign-off is
deliberately yours. When the PR merges, the item is shipped, and its `Implementing PR` row points
at the PR that shipped it.

---

## When to reach for `propose-and-build` instead

Parts A and B above are the **serial path**: propose, merge, allocate, implement. The serial path
is the default for two reasons. It forces a design to clear review *before* any code is written.
It also keeps the `BE-NNNN` sequence contiguous, by spending a number on items that ship, and on no others.

Consider a **small, well-scoped item whose design you do not expect review to reshape**. For such
an item, the serial path's latency is pure overhead. That latency is the dead time between
"proposal opened" and "id allocated". The
[`propose-and-build`](../.apm/skills/propose-and-build/SKILL.md) skill exists for this case:

```
/propose-and-build
```

The skill composes the same two skills. It authors and implements together on **one branch**, and
lands both as a single BE-creation PR. That PR carries the roadmap item, the code, and the tests at
once. From your side as the contributor, the flow looks like this:

1. You author the proposal and build the implementation on the same branch. The item keeps the
   `BE-XXXX` placeholder and reaches `Status: Implemented` in the PR, because `Status` and the PR
   number do not depend on the not-yet-allocated id.
2. You open one PR. It is a BE-creation PR with a plain scoped title and no `[BE-NNNN]` prefix.
3. A human merges the PR, and CI allocates the real `BE-NNNN`, exactly as in Step A5. CI renames
   the item directory and rewrites `BE-XXXX` → `BE-NNNN` inside the item's own files. The item
   lands numbered and Implemented, with no post-merge fixup.

One rule needs respect: the `BE-XXXX` placeholder must appear nowhere but the item's own files.
The allocator rewrites that directory alone, so an id written into code or a comment would land
stale on `main`.

Take our worked example. *If* "bounded retry for transiently-blocked steps" had been small and
settled enough, you could have proposed and built it in one PR instead of waiting for the merge.
That requires a design you were confident review would not reshape. The cost is real, though. One
PR fuses the design checkpoint with code review. If review *does* change the proposal, you rework
the code in the same PR. The rule of thumb is simple:

> **Serial by default. Reach for `propose-and-build` only when you are confident the design is
> settled.** When a design is genuinely uncertain, the serial path's "review before code" is a
> feature, not overhead.

The skill's own mechanics are documented in
[BE-0216](../roadmaps/BE-0216-propose-and-build-parallel-skill/BE-0216-propose-and-build-parallel-skill.md).
This section covers the *when*, from a contributor's seat.

---

## Where to go next

You now have the full contribution loop: idea → `/ideation` → merged proposal → `/implement-be` →
merged PR. You also know when to fold the loop into a single PR with `/propose-and-build`. The
reference pages cover each piece in depth:

- [roadmap-workflow](roadmap-workflow.md): the conceptual overview of the two-skill loop, and *why*
  authoring and shipping are kept separate.
- [ai-development](ai-development.md): the full operational rules. They cover the gate, one-topic
  branches, the pre-push hook, worktrees, the strict BE-ID lifecycle, model tiers, and the PR
  title/body template.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md): the human-contributor entry point and the environment
  setup.
- [roadmaps/README](../roadmaps/README.md): how to add a roadmap item, what is already out of
  scope, and unsorted ideas awaiting a BE number.
- [`CLAUDE.md`](../CLAUDE.md): the working agreement and the three prime directives every change
  honors.
