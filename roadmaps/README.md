**English** · [日本語](README-ja.md)

# Bajutsu roadmap / backlog

> [!IMPORTANT]
> **Ownership of open items lives in GitHub Issues, not in this file.** Every open item (`Status`
> `Proposal` or `In progress`) has a matching GitHub issue, and that issue's **Assignees** are the
> single source of truth for who, if anyone, is working on it — no field in this repo tracks that.
> Browse [issues labeled `roadmap-tracking`](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+is%3Aopen+label%3Aroadmap-tracking):
> `no:assignee` for the unclaimed backlog, `assignee:<user>` for one person's plate. See
> [BE-0109](BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) for
> details.

> This document tracks features planned for future implementation. Each item has its own file
> (one BE ID per item). Add unformed thoughts to [Unsorted ideas](#unsorted-ideas) first, then
> promote them to a numbered item once the scope is clear.
>
> - **Every item's status** — Implemented, In progress, Proposal, or Deferred — lives on the
>   [roadmap dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html), not on this page:
>   browse, filter, and search every item there, grouped by topic with live progress bars. This
>   page covers what a roadmap item *is* and how to add one, never a snapshot of who stands where.
> - **What exists today**, as prose rather than a per-item list, is
>   [architecture.md#implementation-status](../docs/architecture.md#implementation-status).
> - The design rationale is in [`DESIGN.md`](../DESIGN.md).
> - **The overall strategic direction** is in [vision.md](../docs/vision.md).

## Adding a roadmap item — BE IDs

Every roadmap item lives under `roadmaps/`. The full procedure — directory layout, ID allocation,
both language files, format — is the single source of truth in
[`docs/ai-development.md`](../docs/ai-development.md#roadmap-items-be-ids-strict). Once an item
exists, its `Status` field alone decides where it shows up on the
[dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html) — nothing on this page needs
editing to reflect it.

---

## Not adopting (already covered / out of scope)

- **Change history / version management** — already covered, since scenarios are YAML under git.
- **Cloud device farm / real-device execution as the *default*** — the deterministic core stays local-first and CI-friendly (Simulator, headless browser, emulator), not real hardware or device clouds ([DESIGN §1](../DESIGN.md)). Hosted device-cloud execution is not the default, but it is no longer flatly out of scope: it is tracked as opt-in proposals under *Device-cloud execution*. Multi-platform likewise lives under the *Platform support* items.
- **Per-step screenshots / UI tree on error / device logs** — already covered by the evidence subsystem (capturePolicy + the `result:error` safety net).
- **NL→test generation (Autopilot equivalent)** — overlaps with the existing `record` + the *Authoring experience* items.
- **Scheduling / Slack / TestRail integration** — the domain of the CI / notification layer. Low priority (separately, if needed).
- **Automatic retry of failed tests** — in tension with determinism-first (no fixed sleeps, condition waits). It can hide flakiness, so if adopted at all it should be limited to quarantine use and needs careful consideration.

---

## Unsorted ideas

> Add unformed thoughts here. Promote them to a numbered BE item later.

-
