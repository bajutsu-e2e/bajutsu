**English** · [日本語](BE-XXXX-ja-register-drift-detector-ja.md)

# BE-XXXX — Detect 敬体 register drift in existing Japanese docs and convert them file by file

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ja-register-drift-detector.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Contributor workflow |
| Related | [BE-0278](../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a detector for **register drift** in Japanese prose. `CLAUDE.md` and the
[`japanese-document-writing`](../../.apm/skills/japanese-document-writing/SKILL.md) skill require
敬体 (ですます調) for every `*-ja.md` roadmap item and every page under `docs/ja/`. The detector
flags a sentence whose final predicate breaks that rule.

The detector works by exclusion, not enumeration. It matches a sentence's final predicate against a
fixed allowlist of 敬体 forms. Anything that does not match gets flagged.
[GitHub Issue #1842](https://github.com/bajutsu-e2e/bajutsu/issues/1842) already tried enumerating
常体 endings instead. That attempt never converged: three passes over the same corpus returned three
different counts.

This item also narrows a rule commit `5a586e4` stated without exception: "unify the whole document
in 敬体, bullet lists included." A `## Progress` / `## 進捗` checklist bullet (`- [ ]` / `- [x]`)
gets a named carve-out from that line, on the same footing as the heading and 体言止め-label
exemption `japanese-document-writing` already has — a work-item bullet names a task, not prose a
reader follows.

The implementation converts one file in full:
[BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md). Issue
#1842 names its Japanese side as 94% 常体. Converting it proves the detector and the conversion
recipe hold together on a real file.

Converting the rest of the corpus is **not** part of this item. About 362 more files still need the
same treatment after that. This item leaves that work untracked on purpose: Issue #1842 stays open
as the pointer to it, and a contributor converts one file, confirms it with the detector, and opens
a small PR whenever time allows.

## Motivation

Commit `c34ed6110` (2026-06-24) converted every roadmap `*-ja.md` file that existed then, 78 files,
to 敬体, and codified the rule in five places: `CLAUDE.md` and the Japanese writing skill (now
`japanese-document-writing`) among them.
[BE-0278](../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill.md) later placed that skill
under the umbrella `document-writing` norm, and commit `5a586e4` extended the rule explicitly to
bullet lists. The rule has been in force for three months.

The corpus grew past it anyway. `main` carries 425 roadmap `*-ja.md` files today; about 347 of them
were written after `c34ed6110`, under a rule that already named 敬体 as the required register.
Issue #1842 measured the result: 2,193 lines across 363 files end a sentence without a 敬体 marker,
excluding code fences, tables, headings, and block quotes. BE-0089's Japanese side, written three
days after the conversion commit, reports 94% 常体, and reads as internally consistent throughout.
The gap is not old debt a rule arrived too late to cover. It is new prose that the rule already
governed, written non-compliant anyway.

A reliable detector matters because an unreliable one produces the exact failure this item exists
to avoid. A past attempt at converting part of BE-0089 turned a consistently 常体 document into a
mixed one, worse than leaving it untouched, and that edit had to be reverted. A detector that misses
real drift invites the same partial edit again. One that reports legitimate 常体 (a 連体修飾節, a
conditional form, a 体言止め label) as a confirmed violation sends a contributor rewriting correct
prose instead of fixing real drift. Either failure mode defeats the rule this item's design depends
on: finish a file, or leave it alone.

Once this item ships, a converted file's own detector report is the observable proof. Today, the
detector flags most of BE-0089's Japanese sentences, since the file is 94% 常体. After this item's
own implementation converts that file, the detector's report for it holds only the candidates a
human discarded on purpose. No genuine violation remains.

## Detailed design

### The detector

`scripts/ja_register_check.py` scans a set of files and reports every candidate register-drift
sentence, grouped by file and line number. It targets `*-ja.md` roadmap items and every page under
`docs/ja/`, the same scope `japanese-document-writing` already names.

The scan excludes fenced code blocks, tables, headings, block quotes, and `## Progress` / `## 進捗`
checklist bullets before it looks at a single sentence — the same exclusion set Issue #1842's own
manual measurement used, plus the checklist carve-out this item adds. It then joins each
paragraph's hard-wrapped lines into one block, since this repository wraps prose at roughly 100
columns and only a paragraph's last line carries `。`; splitting on raw lines instead would flag
nearly every wrapped continuation line as an unterminated sentence. It splits what remains into
sentences on `。`, `！`, and `？`; a block with no terminal mark (a 体言止め label, most often)
counts as one sentence.

Detection works by exclusion, not enumeration. Each sentence goes through a Japanese morphological
analyzer. A plain suffix match cannot do this reliably: a trailing parenthetical group (skipped
whole, from the closing bracket back to its matching opening bracket, so "（BE-0089）" does not
hide the predicate before it), sentence-final particles (か, ね, よ), and multi-morpheme auxiliary
chains (ました splits into まし + た) all sit after the morpheme that actually carries 敬体. The
detector walks the sentence's trailing morphemes back past those, and flags the sentence as a
candidate unless that trailing span contains a です or ます morpheme, matched by lemma, or ends in
ください (lemma 下さる).

The detector over-flags on purpose. `japanese-document-writing` already exempts a 連体修飾節 and a
conditional or conjunctive form (〜する場合, 〜すると, 〜であり) mid-sentence, since the
sentence-final predicate alone carries the register — a plain morpheme walk from the sentence's end
cannot see these, since they sit mid-sentence, not at the end. What it does surface as a candidate
is a genuine 体言止め label, since headings are already excluded but an inline label is not. The
detector reports these as candidates, not as confirmed violations. Converting a file means
discarding the legitimate ones by hand — the same two-stage design, detect broadly and then filter,
that Issue #1842's own proposed direction already settled on.

The detector runs through `uv run scripts/ja_register_check.py <path>...`, wrapped by
`make ja-register-check ARGS="<path>..."` for consistency with this repository's other `make`
targets. It stays out of `make check`: the corpus already carries about 2,193 candidate lines, and
gating on a number that large would fail the whole gate for every contributor until the backlog
clears. This matches how [`document-writing`](../../.apm/skills/document-writing/SKILL.md) already
treats prose quality, as a review-time norm rather than a gate.

### An ephemeral dependency, not a project one

The morphological analyzer (`sudachipy`, plus a dictionary package) never joins `pyproject.toml` or
`uv.lock`. `scripts/ja_register_check.py` carries its own dependency list in a Python Enhancement
Proposal (PEP) 723 inline-script metadata block (a `# /// script` header). `uv run` reads that block
and sets up an isolated environment for the one invocation. The base install and every other
`uv sync` stay the same. Nothing at runtime imports the script, so it needs no package extra
either. It is dev tooling, reached only by a contributor who runs it directly.

`mypy` runs in strict mode over the whole repository, `scripts/` included, so an unresolved
`sudachipy` import still needs an answer. A `[[tool.mypy.overrides]]` entry —
`ignore_missing_imports = true` for `sudachipy` and its dictionary package — uses the same
mechanism `boto3` and `google-cloud-storage` already rely on, there because those SDKs ship no type
stubs. That mechanism works the same way for a package `make check` never installs at all: it keeps
the `typecheck` step green without installing the analyzer into the pinned dev environment.

The same absence shapes how the script is tested. The dev environment never installs `sudachipy`,
so the analyzer sits behind a lazily imported seam, called only from the one function that walks a
sentence's trailing morphemes. `tests/test_ja_register_check.py` covers the pre-scan exclusion, the
sentence split, and the candidate logic against a fake tokenizer, the way
[`pyproject.toml`](../../pyproject.toml) already notes for the cloud-SDK seams around `boto3` and
`google-cloud-storage`: "the seam adapters around them are typed and tested."

### The policy change: Progress checklists are exempt

Commit `5a586e4` states the rule with no exception: "unify the whole document in 敬体, bullet lists
included." At the sentence level, `japanese-document-writing` already carries three exemptions from
敬体 — a heading, a pure 体言止め label, and a non-final clause (a 連体修飾節, a conditional or
conjunctive form) — but none of those cover a `## Progress` / `## 進捗` checklist bullet
(`- [ ]` / `- [x]`), which is running prose under the current text.

This item adds that fourth exemption anyway, on the same footing as the label exemption a heading
already has: a work-item bullet names a task, not an argument a reader follows, and the heading and
体言止め-label exemptions turn on exactly that distinction between a label and prose. Every
already-`Implemented` item this proposal surveyed —
[BE-0343](../BE-0343-prose-companion-pr/BE-0343-prose-companion-pr-ja.md),
[BE-0384](../BE-0384-record-issue-skill/BE-0384-record-issue-skill-ja.md) — already writes its
checklist this way, so the carve-out states what the corpus already does rather than changing it,
though the two disagree on form: BE-0343 writes each bullet as a task label, while BE-0384 writes a
full 常体 sentence. The exemption covers both, since neither is 敬体 prose a reader follows.
Converting those already-shipped checklists to match the letter of the current rule would touch a
finished work log for no reader's benefit.

Two sentences in `japanese-document-writing` need the edit, not one: the bullet-list line in the
textlint section, and the no-mixing sentence in the 文体 section ("do not mix 敬体 and 常体 within
one document"), which otherwise still reads a 常体 checklist bullet as mixing register with the
rest of a 敬体 document.

### Proving the recipe: BE-0089

The implementation converts one file in full:
[BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md)'s
Japanese side, the file Issue #1842 names as 94% 常体. The detector's report for that file, taken
before and after the conversion, is the proof. Before, it flags most of the file's prose sentences.
After, only the candidates a human discarded on purpose remain in the report, and every genuine
violation is gone.

### Not in scope

Converting the remaining 362 or so files stays outside this item. [Alternatives
considered](#alternatives-considered) explains why the work gets no dedicated tracking mechanism
here: it is one-time retroactive cleanup, not a recurring drift. Issue #1842 stays open as the
informal pointer to it. A contributor converts one file at a time, confirms it with the detector,
and opens a small PR whenever time allows.

This item also adds no guard against a converted file drifting back: the register norm already
governed BE-0089's Japanese side when it was written non-compliant, and nothing here changes that.
The detector over-flags by design, so its raw candidate count cannot gate a file the way
`coverage-floors.json` gates a coverage number; a per-file candidate-count ratchet is a real option,
left for a later item if regression turns out to be a real problem rather than a hypothetical one.

## Alternatives considered

| Alternative | Summary | Why not chosen |
|---|---|---|
| Leave it | Rely on the register norm holding for new writing; let existing files drift toward 敬体 only when someone happens to edit them. | The corpus is 2,193 lines across 363 files. Opportunistic edits touch a tiny fraction of that per year, so the inconsistency stays visible for years. |
| Fix opportunistically, in whatever file a PR already touches | Skip building a detector. Convert a file's register by eye whenever a PR happens to touch it. | Without a reliable way to find every non-敬体 line, a partial, by-eye edit risks the outcome a past BE-0089 edit already produced: a consistently 常体 document turned into a mixed one. |
| Detect 常体 by enumerating its endings | Keep the detector regex-only, listing known 常体 endings instead of an allowlist of 敬体 ones. No new dependency, not even an ephemeral one. | Issue #1842 tried this same approach and could not make it converge. Three attempts on the same corpus returned 1,254, 214, and 424 lines, before a fourth, exclusion-based attempt returned 2,193. |
| Scheduled automation that files Draft PRs against the backlog | Extend [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr.md)'s daily docs-refresh pattern: an AI-authored workflow converts a batch of files on a schedule and opens its own Draft PR. | Issue #1842 frames this as one-time retroactive cleanup, not the recurring drift BE-0222's targets are. Standing infrastructure — a workflow, a credential, an in-job gate — for a backlog that reaches zero and then never runs again costs more than the time it would save. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add `scripts/ja_register_check.py` — the exclusion-based detector, its ephemeral
      `sudachipy` dependency via PEP 723, the `[[tool.mypy.overrides]]` entry, and the
      `make ja-register-check` wrapper.
- [ ] Amend `japanese-document-writing`'s 文体 no-mixing sentence and its textlint bullet-list
      line to exempt `## Progress` / `## 進捗` checklist bullets from 敬体, alongside the existing
      heading and 体言止め-label exemption.
- [ ] Convert [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md)'s
      Japanese side to 敬体 completely, checked against the detector's report, as the first proof
      of the recipe.

## References

- [`CLAUDE.md`](../../CLAUDE.md) — the source of the 敬体 requirement for `*-ja.md` and `docs/ja/`.
- [`japanese-document-writing`](../../.apm/skills/japanese-document-writing/SKILL.md) — the skill
  this item amends.
- [GitHub Issue #1842](https://github.com/bajutsu-e2e/bajutsu/issues/1842) — the corpus measurement,
  the enumeration attempts, and the two constraints (exclusion over enumeration, file-unit
  completion) this item's design follows.
- [BE-0278](../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill.md) — placed the Japanese
  writing skill, which has carried the 敬体 requirement since commit `c34ed6110`, under the
  umbrella `document-writing` norm.
- [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md) — the
  file this item's implementation converts first.
