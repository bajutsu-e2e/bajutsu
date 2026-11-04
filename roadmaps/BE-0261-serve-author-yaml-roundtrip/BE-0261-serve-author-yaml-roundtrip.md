**English** · [日本語](BE-0261-serve-author-yaml-roundtrip-ja.md)

# BE-0261 — Round-trip Author YAML edits through the serializer

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0261](BE-0261-serve-author-yaml-roundtrip.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0261") |
| Implementing PR | [#1079](https://github.com/bajutsu-e2e/bajutsu/pull/1079) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

The Author editor edits scenario YAML by hand, as text, in JavaScript. It does so in two cases:

- Edit's Apply writes a picked [selector](../../docs/glossary.md#scenario-authoring) into a step.
- Enrich's Accept inserts proposed assertions and a settle wait.

The Apply click handler (`#au-apply`) and `enrichApply` in `bajutsu/templates/serve.author.js` work
as follows:

- They split the textarea on newlines.
- They scan for a step's line with prefix matching in the style of `trimStart()` and
  `startsWith('- tap:')`.
- They compute indentation with a regex.
- They splice replacement lines back in.

No parser sits in the loop. The frontend re-derives YAML structure from string shape.

This item replaces that string surgery with a proper parse → mutate → serialize round-trip. The
round-trip reuses the scenario model and serializer that the backend already owns:
`dump_scenario_file` in `bajutsu/scenario/serialize.py`, the function that Capture's save already
uses.

## Motivation

Editing YAML as flat text is brittle by construction. The current matchers assume canonical
formatting:

- one step per `- action:` line
- block style
- predictable indentation
- no interleaved comments
- a target scenario found by scanning `- name:` lines

A scenario that deviates from this can make Apply or Enrich edit the wrong line, corrupt
indentation, or do nothing without warning. Examples of deviation are a flow-style step (`- tap: { id: … }`
across lines), a comment between steps, a non-standard indent, two scenarios in one file, and a
selector value that contains `:` or `#`. The write is also unvalidated text. A bad edit surfaces
merely when the debounced lint runs, not at the point of the mutation.

The project already has the right tool. Scenarios have a Pydantic model and a canonical serializer,
and Capture's save path round-trips through `dump_scenario_file`. The Author *edit* paths
reimplement a worse, lossy version of that tool in the browser.

Consolidating on parse → mutate → serialize makes Apply and Enrich structurally correct regardless
of the source formatting. It also removes a chunk of fragile string-scanning JavaScript. Beyond
correctness, this is a win in affinity: one serializer, not two notions of "how a step looks".

The one real tension is formatting preservation. A naive re-serialize can reflow the author's whole
file and discard comments. The design must decide the mutation boundary (see below) so that an Apply
does not rewrite unrelated lines.

No prime directive changes. This is authoring-side editing, and the deterministic `run` reads
the saved YAML as it always has.

## Detailed design

1. **Choose the round-trip boundary.** Decide where parse → serialize happens, so that a single-step
   edit does not reflow the entire document or drop comments. The work evaluates two options:
   - (a) A serialize endpoint that mutates the parsed model and returns canonical YAML for the whole
     file. This is the simplest option and reuses `dump_scenario_file`, but it reflows formatting
     and comments.
   - (b) A scoped serializer that emits the changed step or expect block alone. The frontend splices
     the block in at a span that a parser identified. This preserves the surrounding text but takes
     more work.

   The item picks one option and records why.
2. **Apply (Edit) through the model.** Replace the Apply click handler's line splice with three
   operations: locate the step in the parsed model, set its selector, and serialize. The
   selector-to-YAML helper (`auSelectorYaml`) and its manual quoting become unnecessary once the
   serializer owns the quoting of values that contain `:` or `#`.
3. **Accept (Enrich) through the model.** Replace `enrichApply`'s expect and settle line insertion
   with a model mutation, then serialize. The mutation appends the assertions and inserts the settle
   wait after the last step. This removes the `_extractName`, `stepsEnd`, and `expectStart`
   line-hunting.
4. **Keep the editor's live validation.** The result still flows into the textarea and the existing
   lint, audit, and gutter refresh. A human reviews the result and saves it. Nothing here bypasses
   `save_scenario`.
5. **Tests.** Cover round-trip correctness on scenarios that today's string matchers mishandle:
   flow-style steps, comments between steps, a selector value with `:` or `#`, and a two-scenario
   file. Assert that the right step and scenario are mutated, and that the rest is preserved to the
   guarantee of the chosen boundary.

## Alternatives considered

- **Harden the string matchers in place.** This piles more special cases (flow style, comments,
  quoting) onto a fundamentally text-shaped approach. The approach never reaches the correctness
  that a parser gives for free, and it keeps two divergent ideas of scenario structure.
- **Full-file canonical re-serialize, accepting the reflow.** This is the simplest implementation.
  Reformatting an author's file and dropping their comments on every Apply is a poor authoring
  experience, though. It is acceptable only if the design explicitly chooses it and the UI signals it.
- **Do nothing and rely on lint to catch damage.** Lint flags an invalid *result*, but it cannot
  recover the author's intent after a mis-spliced edit. Catching damage after the fact is worse than
  not corrupting the file.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — decide + document the round-trip boundary (full-file vs scoped block).
- [x] Unit 2 — Apply (Edit) through the parsed model + serializer.
- [x] Unit 3 — Accept (Enrich) through the parsed model + serializer.
- [x] Unit 4 — preserve live lint/audit review before Save.
- [x] Unit 5 — round-trip tests over the formats the string matchers mishandle.

**Boundary chosen (Unit 1): scoped-block splice, PyYAML only.** The editor loads the raw file text
with its comments intact. A full-file re-serialize would therefore drop every comment and reflow
unrelated scenarios on each Apply. Instead, the backend works in four steps:

- It parses the file with PyYAML.
- It locates the changed step or expect span through `yaml.compose` source marks.
- It re-serializes only that block.
- It splices the block into the raw text.

Comments outside the block survive. A comment-preserving round-trip through `ruamel` was rejected,
because it would add a second YAML engine whose `on`/`off` boolean semantics differ from
`bajutsu/_yaml.py`.

Log (oldest first):

- Implemented in [#1079](https://github.com/bajutsu-e2e/bajutsu/pull/1079).
  - New `bajutsu/scenario/edit.py` (`apply_selector` / `apply_enrichment`) and a `dump_block`
    serializer helper.
  - Two AI-free serve endpoints (`/api/scenario/apply-selector`, `/api/scenario/enrich-apply`) in
    `bajutsu/serve/operations/author_edit.py`.
  - The Author editor's `#au-apply` handler and `enrichApply` now POST to the endpoints. They delete
    `auSelectorYaml`, `enrichAssertionYaml`, `_extractName`, and all line-scanning.
  - Round-trip tests cover flow-style steps, comments between steps, a `:`/`#` selector value, and a
    two-scenario file.

## References

- [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) (Apply)
- [BE-0014 — Demarcation from the existing AI record](../BE-0014-record-demarcation/BE-0014-record-demarcation.md) (Enrich)
- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface.md)
- `bajutsu/templates/serve.author.js` (the `#au-apply` Apply click handler, `enrichApply`, `auSelectorYaml`, `enrichAssertionYaml`), `bajutsu/scenario/serialize.py` (`dump_scenario_file`)
