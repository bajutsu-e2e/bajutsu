**English** · [日本語](ja/vision.md)

# Future vision

> Forward-looking — the overall direction Bajutsu is heading, and the one constraint every
> direction must respect. This page gives the strategic overview across the individual roadmap
> items; the granular, prioritized backlog is in [roadmap](../roadmaps/README.md), and the rationale behind
> today's design is in [`DESIGN.md`](../DESIGN.md). Read this to understand how the pieces fit together,
> then follow the links for each plan.

Related: [concepts](concepts.md) · [roadmap](../roadmaps/README.md) · [multi-platform](multi-platform.md) · [roadmap → Hosting](../roadmaps/README.md#hosting-the-web-ui-cloud--self-hosted)

---

## The invariant: what never changes

Every future direction is evaluated against the **prime directives**
([CLAUDE.md](../CLAUDE.md) · [concepts](concepts.md) · [DESIGN §2](../DESIGN.md)). They stay fixed
across every direction below:

1. **AI is the author and the failure investigator, never the judge.** No future feature may put
   an LLM (large language model) into the Tier-2 `run`/CI (continuous integration) gate. Pass/fail
   stays machine-checkable, always.
2. **Determinism first.** No fixed sleeps; an ambiguous selector fails fast. Every new platform,
   host, or authoring tool inherits this — it is not negotiable for reach or convenience.
3. **App-agnostic / backend-agnostic.** Per-app and per-platform differences live in config and
   behind the `Driver` / environment seams; the deterministic core stays the same everywhere.

> The test of any roadmap item is whether it keeps AI out of the gate and the gate
> deterministic. If not, it belongs in **Tier 1 (authoring) or triage (investigation)**,
> outside the gate, or it does not belong in Bajutsu.

---

## Three axes of growth

Bajutsu expands along three independent axes. They compose (none blocks the others), and each
maps to concrete pages.

```
                 ▲ REACH (more platforms / surfaces)
                 │   Web · Android · Flutter / hybrid
                 │   → multi-platform.md
                 │
   AUTHORING ────┼───────────────▶ SCALE & COLLABORATION
   & MAINTENANCE │                 hosted / self-hosted service · MCP
   GUI editor ·  │                 → roadmap: Hosting (BE-0015 / BE-0016)
   capture ·     │
   visual-regression ·
   self-healing triage
   → roadmap §3, §6, §10
```

### 1. Reach — more platforms and surfaces

The `Driver` / environment / id-convention seams were built to be replaced, not just configured.
The goal is for **the same deterministic core to drive iOS, Android, and the Web**, with each platform
adding only its own actuator + environment + stable-id convention. The full concrete plan
(selector-portability mapping, per-platform backends, phasing — Web first, because it runs on the
existing Linux gate) is in **[multi-platform](multi-platform.md)**. A second iOS actuator
(XCUITest) is the same change within one OS ([roadmap → Backend expansion](../roadmaps/README.md#backend-expansion-ios-actuators)).

### 2. Scale & collaboration — from local tool to shared service

`bajutsu serve` is a local, single-user launcher today. The goal is a **shared service**: a
cheap Linux control plane (auth, history, queue, report viewer) split from an
expensive device-worker pool, so a team runs and reviews from a browser.

- **[BE-0015 — public / cloud hosting](../roadmaps/in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)** — public / multi-tenant: control-plane ⇄ macOS worker pool
  split, the `subprocess.Popen` → job-queue refactor, and the security hardening that public
  exposure mandates.
- **[BE-0016 — self-hosting](../roadmaps/in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)** — your own Mac(s): a today-ready single-Mac setup and a
  fully self-hosted multi-tenant topology.
- **MCP (Model Context Protocol) integration** ([roadmap → Integration & automation](../roadmaps/README.md#integration--automation-mcp)) — expose `run`/`doctor`/`record`/
  `codegen` as MCP tools and evidence as MCP resources, so agents drive Bajutsu directly. This
  stays on the Tier-1 side of the boundary: agents author and investigate, the gate stays deterministic.

### 3. Authoring & maintenance — lower the cost of owning tests

The scenario is just YAML owned by humans; this axis makes writing and maintaining it cheaper without
softening the gate.

- **GUI (graphical user interface) editor & non-AI action capture** ([roadmap → Authoring experience](../roadmaps/README.md#authoring-experience-record--gui-editor)) —
  visually edit scenarios, pick selectors on a screenshot, and capture real taps/types into a
  scenario without an LLM. `bajutsu serve` is the first step.
- **Visual-regression assertions** ([roadmap: BE-0029](../roadmaps/implemented/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md)) — a new
  deterministic assertion type (baseline diff). It fits because it is machine-checked,
  not AI-judged.
- **Self-healing triage** ([roadmap: BE-0021](../roadmaps/implemented/BE-0021-ai-triage/BE-0021-ai-triage.md)) — already shipped: AI reads
  failure evidence and proposes a **minimal diff**, which a human reviews and applies with
  `--write`. The guardrail (never auto-soften a committed test) is what keeps this inside the
  directives.

---

## What stays fixed across all three

Everything in the table below is **shared, deterministic, and platform-/host-neutral**, and it does
not fork as Bajutsu grows. This is what makes the three axes independent: they extend the edges,
not the core.

| Fixed core | Where |
|---|---|
| Scenario DSL (domain-specific language) & grammar | [scenarios](scenarios.md) · [dsl-grammar](dsl-grammar.md) |
| Selector model & deterministic resolution | [selectors](selectors.md) |
| Machine assertions (the only judges) | `assertions.py` · [concepts](concepts.md) |
| observe → act → verify orchestrator | [run-loop](run-loop.md) |
| Evidence subsystem (capturePolicy / manifest) | [evidence](evidence.md) |
| Reporter (manifest / JUnit / HTML) | [reporting](reporting.md) |
| Config layering (`defaults × targets`) | [configuration](configuration.md) |

New platforms add backends behind the `Driver` seam; new hosting changes where `run` is invoked,
not what it does; new authoring produces the same YAML. The core stays constant.

---

## Recommended near-term sequence (a recommendation, not a commitment)

If the vision has to be sequenced, the highest-leverage next steps, each reducing the risk of a
later one at low cost, are:

1. **Web via Playwright** ([multi-platform](multi-platform.md), Phase 1). It demonstrates that the
   core is platform-neutral **inside the existing Linux gate** ([ci](ci.md)), with no Mac and no
   emulator, and exercises the rich end of the capability model (native network/video/semantic).
2. **MCP server** ([roadmap → Integration & automation](../roadmaps/README.md#integration--automation-mcp)). Low surface area, high leverage for
   the Tier-1 authoring loop, and it does not touch the gate.
3. **Visual-regression assertions** ([roadmap: BE-0029](../roadmaps/implemented/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md)). A
   deterministic capability that competitors gate behind AI; it strengthens the
   directives rather than straining them.

The hosting axis ([BE-0015](../roadmaps/in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) / [BE-0016](../roadmaps/in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) is a larger,
separable investment; pursue it when the demand is collaborative rather than individual.

> **How this relates to [roadmap](../roadmaps/README.md):** this page covers the rationale and the overall
> direction; the roadmap is the prioritized, living backlog (the next concrete items). When an item
> here becomes actionable, it appears there with a priority and status; when it ships, it moves to
> the [architecture status table](architecture.md#implementation-status). Keep the three in sync.
