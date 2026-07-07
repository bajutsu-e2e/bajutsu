**English** · [日本語](BE-0040-ai-assertions-ja.md)

# BE-0040 — AI assertions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0040](BE-0040-ai-assertions.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal (deferred)** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0040") |
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

AI evaluates a natural-language expectation against the current screen state. This must never be part of the CI gate, because it is non-deterministic. Use is limited to draft assistance in record and triage.

## Motivation

MagicPod offers AI assertions: you write an expectation in natural language ("the cart shows two items", "an error banner is visible") and a model judges whether the current screen satisfies it. The appeal is authoring speed — you describe intent instead of hunting for the exact identifier and value to assert on. For some screens, especially visual or content-heavy ones, a deterministic assertion is genuinely awkward to write by hand. The motivation is to capture that authoring convenience. The difficulty, and the reason this item is deferred, is that the obvious implementation puts a non-deterministic model in the place where the verdict is decided.

## Detailed design

This is a proposal at design altitude; the tension with prime directive #1 is the central question.

**Why it is deferred.** Prime directive #1 says the `run`/CI gate is fully deterministic — pass/fail comes only from machine-checkable assertions, never an LLM. An AI assertion in its literal form is an LLM deciding pass/fail, which is exactly what the gate forbids. The same model on the same screen can answer differently across runs (and across model versions), so a suite built on AI assertions would have a non-reproducible verdict and could drift silently. That is disqualifying for the Tier-2 gate, so the literal feature cannot be adopted as-is.

**The only shape that could fit: an authoring aid that lowers to a deterministic check.** The path that respects the boundary is to keep the natural-language expectation strictly on the *author* side (record / triage), never in `run`. There, a model would read the expectation against the captured screen and the accessibility element tree and *propose* a concrete deterministic assertion — an `expect` over element existence, a trait, or a value (the same DSL §6.4 already runs deterministically). The human reviews that proposed assertion as a diff, exactly like any other AI-authored step (DESIGN §3.1, §6.5: "AI output is always a proposed diff"). What is committed and what `run` evaluates is the lowered, machine-checkable assertion — never the natural-language sentence, and never a live model call. The natural-language text can be kept as a provenance comment (`# from:`) for readability.

If lowering is not possible for a given expectation — the screen genuinely cannot be pinned to an identifier, trait, or value — then there is no deterministic assertion to commit, and the right answer is to surface that to the author rather than fall back to a live LLM judgement. The feature, if taken up, is therefore an authoring assist, not a new kind of runtime check.

## Alternatives considered

* **A live AI assertion evaluated inside `run` (the MagicPod shape).** Rejected: it is an LLM in the pass/fail gate, violating prime directive #1, and it makes the verdict non-reproducible. This is the reason for deferral, not a viable design.
* **A live AI assertion gated behind a flag, excluded from CI.** Still rejected. Even if kept out of the CI gate, a "pass/fail" that sometimes comes from a model blurs the one line the project keeps sharp; a result that is sometimes deterministic and sometimes not is worse than a clear split between author-side proposal and deterministic check.
* **Do nothing.** Acceptable today — this item is deferred precisely because the deterministic DSL covers the cases that can be pinned down. The lowering approach is recorded so that if AI-assisted authoring of assertions is taken up, it is built as a proposal aid, not a runtime judge.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] TBD — enumerate the work breakdown (MECE) here once scoped.

## References

[DESIGN §2 / §3.1](../../DESIGN.md)
