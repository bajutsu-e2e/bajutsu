# Claude review (local)

Mirror the "Claude review" GitHub Actions workflow's judgment (BE-0203) on demand, against any
diff — a pre-push working diff, or an already-open pull request — so the same contract audits a
change before it ever reaches CI, or again on a live PR beyond what `@claude review`'s own trigger
rules cover (BE-0347). This skill only **judges**; it never edits a file. Whatever invokes it — a
human, or another skill's own self-review step — decides what to do with its findings: apply them,
escalate them, or note them as a deliberate trade-off.

This packages the same judge-only review/plan pass [`ideation`](../ideation/workflow.md) step 5
describes inline as its canonical procedure — the two restate the same cap, taxonomy, and dedup
rule independently, so keep them in sync if either changes.

## When to use this directly

Invoke it yourself when you want the CI reviewer's exact read on a change without waiting for a push
and a bot round-trip: mid-session, on a working diff nobody has committed yet, or against an open PR
when you want a fresh contract pass right now rather than posting `@claude review` and waiting.

## Inputs

- **The diff.** Either:
  - a **working diff** — uncommitted or unpushed local changes. Stage anything untracked first
    (`git add -N <paths>` — an untracked file is invisible to a bare `git diff`, and `-N` records
    the path without staging its content, so this judge-only pass never changes what a later commit
    would pick up), then diff against the
    branch point: `git diff $(git merge-base HEAD origin/main) -- <paths>`. Scope `<paths>` to
    whatever's actually in play — the whole tree by default, narrower when the caller's own remit is
    narrower.
  - a **pull request** — `gh pr diff <PR>` for the full current diff.
- **Whether a live PR exists.** A pull request carries discussion and a history of findings this
  pass must not repeat; a pre-push working diff carries neither.

## Steps

### 1. Read the contract

Read [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) in full — the same
file the CI job reads. It is the source of truth for every rule below: the severity floor (functional
impact only — no nitpicks, no praise), the prime-directive lenses, the design/security/semantic/test
lenses, and the two prose-quality lenses with their own stricter floor. Read it fresh each time
rather than from memory, since it changes independently of this skill.

### 2. Read the existing discussion (live PR only)

Skip this step for a pre-push working diff — there is no discussion yet.

For a live PR, read `gh pr view <PR> --comments` for the conversation, and the inline review
comments already posted (`gh api --paginate repos/{owner}/{repo}/pulls/<PR>/comments`) so you know
what is already on the thread — `--paginate` matters once a PR has accumulated more than a page of
prior comments. Hold yourself to the contract's own dedup rule: never restate a point a
human, GitHub's native reviewer, or an earlier pass already made; treat a thread a human already
resolved as settled; and still raise a genuinely new problem anywhere in the diff, even on code an
earlier pass overlooked — dedupe by suppressing repeats, never by narrowing what you read.

### 3. Review cold, against the whole diff, in one pass

Apply every lens in the contract to the entire diff named in the Inputs section above — the prime directives,
design/architecture, security, the silent-failures/type-design/test-coverage lenses, the semantic
bug classes, test quality, and the two prose lenses. The prose lenses' own scope is the contract's,
not a path filter: the Japanese lens covers any Japanese the diff adds or edits, including Japanese
in a code comment, and the English lens covers `docs/*.md` and roadmap `BE-NNNN-<slug>.md` prose.
Only the `(non-blocking, prose)` *marker* in step 5 is restricted to `docs/` and `roadmaps/` files.
Read every changed file in full before finishing; a shallow first pass that leaves the rest for
"next time" is the dribble the contract explicitly forbids, and there may be no next time.

If this pass runs as a fresh subagent spawned by another skill's self-review step, rather than the
interactive session the user is driving directly, give it **only** the contract text and the diff —
not the calling skill's authoring conversation. The CI reviewer runs cold, with no memory of why a
line was written; a subagent that inherited that context would not reproduce its judgment.

### 4. Classify every finding that clears the severity floor

This pass **never edits a file** — it classifies. Every finding that clears the contract's severity
floor comes back as one of:

- a **fix instruction** — the file, the exact location, and the exact change to make;
- an **escalation** — a finding that calls for a genuine design change, which is the caller's (or the
  user's) call, never this pass's own;
- **noted and left as-is** — a false positive, or a deliberate trade-off the diff or its PR
  description already explains, recorded with its rationale so a later round doesn't re-raise it.

### 5. Report or post, depending on the target

- **Working diff, no live PR:** return the classified findings as structured text to whoever
  invoked this skill (another skill's implement pass, or the user directly). Nothing gets posted
  anywhere.
- **Live PR:** post every new fix instruction, escalation, and question as an inline PR review comment — never a
  top-level summary (a fresh summary on every run leaves stale, contradictory overviews across a
  PR's several passes). Follow the contract's exact format: prefix every comment
  `🤖 **Claude Code** — `, then a [Conventional Comments](https://conventionalcomments.org/) label
  (`issue` / `suggestion` / `question`) and the `(non-blocking)` decoration — `(non-blocking, prose)`
  instead, on the two prose lenses' findings, and only in a `docs/` or `roadmaps/` file — and attach
  a GitHub `suggestion` block wherever the fix is mechanical enough to express as replacement lines.
  Post via `gh api repos/{owner}/{repo}/pulls/<PR>/comments` with `-f path=…`, `-F line=…`,
  `-f side=RIGHT`, `-f commit_id=…`, `-f body=…` — `line` goes through `-F`, which serializes it as
  a JSON number; `-f` would send `"42"` and the endpoint rejects the call with a 422. Fetch
  `commit_id` from the PR's current head first
  (`gh api repos/{owner}/{repo}/pulls/<PR> --jq .head.sha`), since a stale or unrelated SHA gets
  rejected or anchors the comment to the wrong diff context. When nothing clears the floor, post
  nothing — silence is a complete review.

## Looping until clean (no live PR)

When there is no PR yet, re-run this whole procedure against the *updated* diff after your own
implement pass applies its fix instructions, carrying forward this round's noted-and-left-as-is
findings (with their rationale) into the next round's input, since each round is spawned cold with
no memory of earlier dispositions and would otherwise re-flag them forever. Stop when a pass returns
nothing (an empty pass is a complete review, per the contract's own closing rule) or once you've run
3 review/plan passes, whichever comes first — an LLM-based reviewer is not fully deterministic and
could keep surfacing a fresh marginal finding every round, possibly one its own previous fix just
introduced. On the 3rd round still returning a real finding, stop and let the user decide rather
than looping further. The cap counts review/plan passes (this skill's own invocations), not fix
attempts.

## What this skill does NOT do

- Edit any file. Applying a fix instruction is the caller's job (its own implement pass), never
  this one's.
- Decide whether a PR merges. This is advisory review only (prime directive 1) — the deterministic
  `check` / E2E gates are the only merge arbiters.
- Post a top-level summary comment on a live PR.
- Re-post a finding already on a PR's thread, or reopen a thread a human already resolved.
