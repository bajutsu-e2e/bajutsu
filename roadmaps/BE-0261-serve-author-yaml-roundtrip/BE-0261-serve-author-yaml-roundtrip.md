**English** · [日本語](BE-0261-serve-author-yaml-roundtrip-ja.md)

# BE-0261 — Round-trip Author YAML edits through the serializer

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0261](BE-0261-serve-author-yaml-roundtrip.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0261") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

When the Author editor applies a change to the scenario — writing a picked selector into a step
(Edit's Apply) or inserting proposed assertions and a settle wait (Enrich's Accept) — it edits the
YAML by hand, in JavaScript, as text. The Edit-mode Apply click handler (`#au-apply`) and `enrichApply` in
`bajutsu/templates/serve.author.js` split the textarea on newlines, scan for a step's line with
`trimStart()`/`startsWith('- tap:')`-style
prefix matching, compute indentation with a regex, and splice replacement lines back in. There is no
parser in the loop: the frontend re-derives YAML structure from string shape.

This item replaces that string surgery with a proper parse → mutate → serialize round-trip, reusing the
same scenario model and serializer the backend already owns (`bajutsu/scenario/serialize.py`'s
`dump_scenario_file`, the function Capture's save already uses).

## Motivation

Editing YAML as flat text is brittle by construction. The current matchers assume canonical formatting:
one step per `- action:` line, block style, predictable indentation, no interleaved comments, the
target scenario found by scanning `- name:` lines. Any scenario that deviates — a flow-style step
(`- tap: { id: … }` across lines), a comment between steps, a non-standard indent, two scenarios in one
file, a selector value containing a `:` or `#` — can cause Apply/Enrich to edit the wrong line, corrupt
indentation, or silently no-op. And because the write is unvalidated text, a bad edit only surfaces
later when the debounced lint runs, not at the point of the mutation.

The project already has the right tool: scenarios have a Pydantic model and a canonical serializer, and
Capture's save path round-trips through `dump_scenario_file`. The Author *edit* paths reimplement a
worse, lossy version of that in the browser. Consolidating on parse → mutate → serialize makes Apply and
Enrich structurally correct regardless of the source formatting, and removes a chunk of fragile
string-scanning JS — an affinity win (one serializer, not two notions of "how a step looks") on top of
the correctness win.

The one real tension is formatting preservation: a naive re-serialize can reflow the author's whole file
and discard comments. The design must decide the mutation boundary (see below) so an Apply doesn't
rewrite unrelated lines. No prime directive is affected — this is authoring-side editing; the deterministic
`run` reads the saved YAML as it always has.

## Detailed design

1. **Choose the round-trip boundary.** Decide where parse→serialize happens so a single-step edit does
   not reflow the entire document or drop comments. Options, to be evaluated in the work:
   (a) a serialize endpoint that mutates the parsed model and returns canonical YAML for the whole file
   (simplest, reuses `dump_scenario_file`, but reflows formatting/comments); (b) a scoped serializer that
   emits only the changed step/expect block for the frontend to splice at a parser-identified span
   (preserves surrounding text, more work). The item picks one and records why.
2. **Apply (Edit) through the model.** Replace the Apply click handler's line-splice with: locate the step in the parsed
   model, set its selector, serialize. The selector-to-YAML helper (`auSelectorYaml`) and its manual
   quoting become unnecessary once the serializer owns quoting of `:`/`#`-bearing values.
3. **Accept (Enrich) through the model.** Replace `enrichApply`'s expect/settle line insertion with a
   model mutation (append assertions, insert the settle wait after the last step) then serialize, removing
   the `_extractName`/`stepsEnd`/`expectStart` line-hunting.
4. **Keep the editor's live validation.** The result still flows into the textarea and the existing
   lint/audit/gutter refresh, so a human reviews and Saves; nothing here bypasses `save_scenario`.
5. **Tests.** Round-trip correctness on scenarios that today's string matchers mishandle — flow-style
   steps, comments between steps, a selector value with `:`/`#`, and a two-scenario file — asserting the
   right step/scenario is mutated and the rest is preserved to the chosen boundary's guarantee.

## Alternatives considered

- **Harden the string matchers in place.** More special cases (flow style, comments, quoting) piled onto
  a fundamentally text-shaped approach; it never reaches the correctness a parser gives for free and
  keeps two divergent ideas of scenario structure.
- **Full-file canonical re-serialize, accept the reflow.** The simplest implementation, but reformatting
  an author's file and dropping their comments on every Apply is a poor authoring experience; acceptable
  only if the design explicitly chooses it and the UI signals it.
- **Do nothing (rely on lint to catch damage).** Lint flags an invalid *result* but cannot recover the
  author's intent after a mis-spliced edit; catching it after the fact is worse than not corrupting it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — decide + document the round-trip boundary (full-file vs scoped block).
- [x] Unit 2 — Apply (Edit) through the parsed model + serializer.
- [x] Unit 3 — Accept (Enrich) through the parsed model + serializer.
- [x] Unit 4 — preserve live lint/audit review before Save.
- [x] Unit 5 — round-trip tests over the formats the string matchers mishandle.

**Boundary chosen (Unit 1): scoped-block splice, PyYAML-only.** The editor loads the raw file text
(comments intact), so a full-file re-serialize would drop every comment and reflow unrelated
scenarios on each Apply. Instead the backend parses with PyYAML, locates the changed step / expect
span via `yaml.compose` source marks, re-serializes only that block, and splices it into the raw
text — comments outside the block survive. A comment-preserving round-trip via `ruamel` was rejected
to avoid a second YAML engine with different `on/off`-bool semantics than `bajutsu/_yaml.py`.

Log (oldest first):

- Implemented: new `bajutsu/scenario/edit.py` (`apply_selector` / `apply_enrichment`) and a
  `dump_block` serializer helper; two AI-free serve endpoints
  (`/api/scenario/apply-selector`, `/api/scenario/enrich-apply`) in
  `bajutsu/serve/operations/author_edit.py`; the Author editor's `#au-apply` handler and
  `enrichApply` now POST to them, deleting `auSelectorYaml` / `enrichAssertionYaml` / `_extractName`
  and all line-scanning. Round-trip tests cover flow-style steps, comments between steps, a `:`/`#`
  selector value, and a two-scenario file.

## References

- [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) (Apply)
- [BE-0014 — Demarcation from the existing AI record](../BE-0014-record-demarcation/BE-0014-record-demarcation.md) (Enrich)
- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface.md)
- `bajutsu/templates/serve.author.js` (the `#au-apply` Apply click handler, `enrichApply`, `auSelectorYaml`, `enrichAssertionYaml`), `bajutsu/scenario/serialize.py` (`dump_scenario_file`)
