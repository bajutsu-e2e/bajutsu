**English** · [日本語](BE-0024-doctor-onboarding-ja.md)

# BE-0024 — doctor / onboarding

* Proposal: [BE-0024](BE-0024-doctor-onboarding.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: doctor / onboarding

## Introduction

The doctor feasibility gate (the CLI (command-line interface) suite + a check for a booted Simulator) is implemented ([architecture.md](../../../docs/architecture.md#implementation-status)). This topic is a placeholder — add new onboarding candidates here as they come up.

## Motivation

The first thing that decides whether someone succeeds with Bajutsu is whether their *setup* is
sound: the right CLIs (command-line interface tools) installed, a Simulator booted, and an app
whose screens carry enough accessibility ids to be addressable. Bajutsu already answers both
halves of that — `preflight.runnability` checks the environment (Xcode's `xcrun`, the backend's
CLIs, a booted Simulator) and `doctor.score` grades a screen's convention readiness (id coverage,
namespace conformance, duplicate ids) into Ready / Partial / Blocked. Together they are the
onboarding surface: the checks that catch a problem *before* a run fails for a confusing reason.

Onboarding is not a one-time feature, though — it accretes. Each time a new way to get stuck
surfaces (a missing dependency, an unhelpful default, a check that would have saved someone an
hour), the fix usually belongs in `doctor`/`preflight` rather than in a feature of its own. This
item exists so those small, related improvements have a home and an ID to land against, instead of
each spawning a throwaway proposal.

## Detailed design

This is a deliberate **placeholder** topic, not a single concrete spec. The implemented
onboarding surface is already documented in [architecture.md](../../../docs/architecture.md#implementation-status):
`preflight.py` (the environment runnability gate) and `doctor.py` (the convention score). The
principle for this item is how *new* candidates get added, not a feature to build now:

- A new onboarding/doctor candidate is a small, focused improvement to the diagnose-before-you-run
  surface — a new runnability check, a clearer remedy string, a sharper `doctor` signal, a better
  first-run default. It is recorded here as a sub-item with its own short rationale, rather than
  promoted to a standalone BE proposal, when it is too small to justify one on its own.
- A candidate is **in scope** when it helps a user discover and fix a setup or convention problem
  *before* a run fails confusingly. It is **out of scope** — and should become its own BE item —
  when it changes the run loop, adds a driver capability, or otherwise reaches beyond diagnosis.
- Every candidate inherits the prime directives unchanged: the checks stay deterministic and
  LLM-free (`doctor.score` is "AI is not involved" by construction), and per-app specifics (id
  namespaces, backend) come from config, keeping the checks app-agnostic.

When a candidate grows large enough to deserve its own design discussion, it graduates to a
dedicated BE item and is removed from here. Until then, this item keeps the onboarding backlog
visible in one place.

## Alternatives considered

**Fold each onboarding idea into an existing item.** Some candidates do have a natural home — a
runnability check for a new backend belongs with that backend's work. But many are cross-cutting
(a clearer remedy string, a first-run default) with no single owner; without a catch-all they fall
through the cracks. A standalone placeholder gives them somewhere to land.

**Open a fresh BE proposal for every onboarding tweak.** Rejected as too heavy. Most of these are
one-line diagnostics or message improvements; a full proposal per change would bury small,
obviously-good fixes under process. This item lets them accumulate cheaply and graduate only the
ones that genuinely warrant their own design.

**Write no proposal and just improve `doctor` ad hoc.** Rejected: the roadmap is the project's
shared memory. Without an ID, the rationale for each onboarding improvement is lost, and there is
no single place to see what has been considered. A visible placeholder is the lightest way to keep
that history.

## References

[architecture.md](../../../docs/architecture.md#implementation-status)
