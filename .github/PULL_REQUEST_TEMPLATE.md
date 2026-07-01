<!--
  Bajutsu PR body template.

  Fill the sections that apply and DELETE the rest — depth proportional to the diff.
  A one-file fix needs only `## Summary` + `## Verification`; a cross-cutting feature
  earns the full set. Write present-tense prose describing what the change *is*, not a
  narration of how you got there. Keep **bold** for the few nouns that carry the change.

  The TITLE lives outside this body: one scoped Conventional-Commits subject in English,
  imperative, no trailing period — `[BE-NNNN] type(scope): summary`. The `[BE-NNNN]`
  prefix appears only when the PR is tied to an already-numbered roadmap item; a PR that
  *introduces* a new item carries no prefix (the id is allocated on `main` after merge).

  Body and title are ALWAYS English, whatever language the work happened in.
  Full rule: docs/ai-development.md#pull-requests-title-and-body
-->

## Summary

<!--
  MANDATORY. One to three short paragraphs (or tight bullets): what this PR does and
  *why it matters*, key nouns in **bold**. Open with the change itself, not its history.
  If this is one slice of a larger BE item, name the slice and say what merging it does
  to the item's Status (e.g. "moves **BE-NNNN** to *In progress*").
-->

## What changed

<!--
  One bullet per file or component — **path or component** in bold, an em-dash, then what
  it does *and why this seam* (the design choice, not just the edit). Mark new files `(new)`.
  Group by component, not by commit. Delete this section if `## Summary` already covers it.
-->

- **`path/to/file`** — …

## Scope

<!--
  What is deliberately NOT in this PR, so a reviewer never has to infer the boundary.
  For a slice of a larger item, list what later slices still owe. Delete if nothing to bound.
-->

<!--
  ─────────────────────────────────────────────────────────────────────────────
  RECURRING SECTIONS — the boilerplate below appears on (nearly) every code PR.
  It lives together here, at the bottom, so the repeated statements stay in one
  place instead of scattering through the body above. Both are effectively
  mandatory for a change that touches runtime behavior.
  ─────────────────────────────────────────────────────────────────────────────
-->

## Prime-directive compliance

<!--
  Keep only the lines the change actually bears on; delete the others. A docs-only or
  pure-infrastructure PR can replace this whole block with a single sentence saying so.
  Reference: CLAUDE.md#prime-directives-do-not-violate
-->

- **AI never judges** — no LLM is consulted on the verdict; the `run` / CI gate stays deterministic.
- **Determinism first** — no fixed `sleep` (condition waits only); an ambiguous selector still fails immediately.
- **App-agnostic** — per-target differences stay in `targets.<name>` config; tool, drivers, and runner are unchanged.

## Verification

<!--
  MANDATORY in some form. Paste `make check` green with the concrete numbers it printed,
  then a sentence on what any new tests cover. Call out anything the gate CANNOT exercise
  (a Simulator-only path, a workflow's runtime) — accuracy is the point; don't claim a path
  was tested when it wasn't.
-->

```
make check
  <format-check / ruff / mypy Success / N passed, coverage X% (floor 87%)>
```

<!--
  ─────────────────────────────────────────────────────────────────────────────
  OPTIONAL SECTIONS — add as the change warrants; delete when unused.

  ## Notes
    Caveats, a related or competing open PR, an expected merge conflict and how to
    resolve it. Reserve GitHub `> [!NOTE]` callouts for something a reviewer must not miss.

  For a roadmap PROPOSAL PR, also add:
  ## Files
    - roadmaps/proposals/BE-XXXX-<slug>/BE-XXXX-<slug>.md + its -ja.md mirror (bilingual pair)
  ## BE ID allocation
    BE-XXXX is a placeholder — CI allocates the real id on `main` after merge. Don't hand-edit it.

  Close the body with reference-style links for anything cited, e.g.
  [BE-NNNN]: roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>.md

  When Claude Code drafts the PR, end the body with:
  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  ─────────────────────────────────────────────────────────────────────────────
-->
