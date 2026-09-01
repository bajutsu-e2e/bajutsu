# Step 7 — the self-review pass in full

Read this at step 7, once the diff exists. It expands the step's summary in the skill body: the
two-role procedure, the two inputs that differ from `ideation`'s, the specialized lenses, and the
loop's cap.

## The two roles

Run the review CI runs on the PR, but locally on your diff: follow
[`ideation`](../../../../.apm/skills/ideation/SKILL.md) step 5's two-role procedure — a fresh review/plan pass that
classifies findings and never edits, then an implement pass that applies its instructions
(BE-0347) — against the
[`.github/claude-review-prompt.md`](../../../../.github/claude-review-prompt.md) contract. Read
that contract and hand it to the **review/plan** pass as text, so no review command is invoked.

Both roles run as separate, freshly-spawned Agent-tool subagents with non-overlapping
instructions — judge-only and apply-only: `opus` for the review/plan pass, and for the implement
pass `sonnet` when the fix stays within `roadmaps/` or `docs/`, `opus` when it touches product
code. For a product-code fix the two land on the same model, so it is the separate cold spawn, not
a model difference, that keeps the judge from patching its own finding.

**Where the host has no Agent tool or no `pr-review-toolkit`** — a subagent running `implement-be`
has neither — keep the two roles as two passes of your own rather than dropping the procedure: judge
the diff against the contract first and write the findings down, then fix them in a second pass, and
read the lenses below as a checklist you apply yourself. Say in your report that the pass ran
single-handed, so the reader knows which findings had no independent second opinion behind them.

Two inputs differ from `ideation`'s procedure:

- **Don't scope the staged diff to `roadmaps/`.** This skill's changes land wherever the item needs
  them, so stage what you touched (`git add <paths>` — until they are staged, new files are
  invisible to `git diff` at all, the same reason `ideation` stages first) and diff exactly those
  paths from the branch point: `git diff $(git merge-base HEAD origin/main) -- <paths>`.
- **Say that steps 8 and 10 are still pending**, so the review/plan pass doesn't spend a round
  flagging the item's un-flipped `Status` and its missing `Implementing PR` row — the `Status` flip
  lands at step 8, and the row is filled at step 10, once the PR number exists.

The implement pass needs neither input: it applies the instructions it is given without re-judging
them.

## The specialized lenses

Then review the diff through the `code-simplifier` agent. Apply justified fixes before the gate. For
a non-trivial change, run the `pr-review-toolkit` plugin's lenses as fresh, independent agents:

- **`silent-failure-hunter`** — swallowed errors and weak fallbacks. This *is* "determinism first,
  fail loudly": a test tool that hides failures is worse than none.
- **`type-design-analyzer`** — type invariants and encapsulation under strict `mypy`.
- **`pr-test-analyzer`** — whether the regression-net tests actually cover the new logic.

Weigh every suggestion against the prime directives and the surrounding code before taking it; drop
anything that fights the codebase grain.

## The loop and its cap

**Don't open the PR (step 10) until this pass is clear.** Keep fixing and re-running a fresh
review/plan pass against the updated diff until it comes back empty, per `ideation` step 5's
loop-until-empty rule and its 3-round cap. What's left standing may only be a finding you judged a
false positive or a deliberate, already-noted trade-off — never an unresolved real finding. Route
anything that calls for a genuine design change to the user instead of the PR, the same escalation
`ideation` step 5 uses. And if the 3rd round still returns a real finding, the cap has been reached:
stop there, leave the PR unopened, and let the user make the call rather than looping further.
