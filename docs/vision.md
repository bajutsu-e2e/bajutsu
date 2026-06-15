**English** · [日本語](ja/vision.md)

# Future vision (the north star)

> Forward-looking — the **shape** of where Bajutsu is going, and the one constraint every
> direction must respect. This page is the strategic umbrella over the individual forward-looking
> pages; the granular, prioritized backlog lives in [roadmap](roadmap/README.md), and the *why* behind
> today's design is [`DESIGN.md`](../DESIGN.md). Read this to understand **how the pieces add up**,
> then follow the links for each plan.

Related: [concepts](concepts.md) · [roadmap](roadmap/README.md) · [multi-platform](multi-platform.md) · [cloud-hosting](cloud-hosting.md) · [self-hosting](self-hosting.md)

---

## The invariant: what never changes

Every future direction is evaluated against the **prime directives**
([CLAUDE.md](../CLAUDE.md) · [concepts](concepts.md) · [DESIGN §2](../DESIGN.md)). They are the
fixed point the whole vision rotates around:

1. **AI is the author and the failure investigator, never the judge.** No future feature may put
   an LLM into the Tier-2 `run`/CI gate. Pass/fail stays machine-checkable, always.
2. **Determinism first.** No fixed sleeps; an ambiguous selector fails fast. Every new platform,
   host, or authoring tool inherits this — it is not negotiable for reach or convenience.
3. **App-agnostic / backend-agnostic.** Per-app and per-platform differences live in config and
   behind the `Driver` / environment seams; the deterministic core stays the same everywhere.

> The test of any roadmap item is simple: *does it keep AI out of the gate and the gate
> deterministic?* If not, it belongs in **Tier 1 (authoring) or triage (investigation)** —
> outside the gate — or it does not belong in Bajutsu.

---

## Three axes of growth

Bajutsu expands along three independent axes. They compose — none blocks the others — and each
maps to concrete pages.

```
                 ▲ REACH (more platforms / surfaces)
                 │   Web · Android · Flutter / hybrid
                 │   → multi-platform.md
                 │
   AUTHORING ────┼───────────────▶ SCALE & COLLABORATION
   & MAINTENANCE │                 hosted / self-hosted service · MCP
   GUI editor ·  │                 → cloud-hosting.md · self-hosting.md
   capture ·     │
   visual-regression ·
   self-healing triage
   → roadmap §3, §6, §10
```

### 1. Reach — more platforms and surfaces

The `Driver` / environment / id-convention seams were built to be replaced, not just configured.
The vision: **the same deterministic core drives iOS, Android, and the Web**, with each platform
adding only its own actuator + environment + stable-id convention. The full concrete plan —
selector-portability mapping, per-platform backends, phasing (Web first, because it runs on the
existing Linux gate) — is in **[multi-platform](multi-platform.md)**. A second iOS actuator
(XCUITest) is the same move within one OS ([roadmap → Backend expansion](roadmap/README.md#backend-expansion-ios-actuators)).

### 2. Scale & collaboration — from local tool to shared service

`bajutsu serve` is a local, single-user launcher today. The vision is a **shared service**: a
cheap Linux control plane (auth, history, queue, report viewer) split from an
expensive device-worker pool, so a team runs and reviews from a browser.

- **[cloud-hosting](cloud-hosting.md)** — public / multi-tenant: control-plane ⇄ macOS worker pool
  split, the `subprocess.Popen` → job-queue refactor, and the security hardening that public
  exposure mandates.
- **[self-hosting](self-hosting.md)** — your own Mac(s): a today-ready single-Mac setup and a
  fully self-hosted multi-tenant topology.
- **MCP integration** ([roadmap → Integration & automation](roadmap/README.md#integration--automation-mcp)) — expose `run`/`doctor`/`record`/
  `codegen` as MCP tools and evidence as MCP resources, so agents drive Bajutsu directly. This
  rides the Tier-1 boundary cleanly: agents *author and investigate*, the gate stays deterministic.

### 3. Authoring & maintenance — lower the cost of owning tests

The scenario is just YAML owned by humans; this axis makes *writing and keeping* it cheap without
ever softening the gate.

- **GUI editor & non-AI action capture** ([roadmap → Authoring experience](roadmap/README.md#authoring-experience-record--gui-editor)) —
  visually edit scenarios, pick selectors on a screenshot, and capture real taps/types into a
  scenario without an LLM. `bajutsu serve` is the first step.
- **Visual-regression assertions** ([roadmap: BE-0029](roadmap/BE-0029-visual-regression-assertions.md)) — a new
  *deterministic* assertion type (baseline diff). It fits precisely because it is machine-checked,
  not AI-judged.
- **Self-healing triage** ([roadmap: BE-0021](roadmap/BE-0021-ai-triage.md)) — already shipped: AI reads
  failure evidence and proposes a **minimal diff**, which a human reviews and applies with
  `--write`. The guardrail — *never auto-soften a committed test* — is what keeps this inside the
  directives.

---

## What stays fixed across all three

Everything in the table below is **shared, deterministic, and platform-/host-neutral** — it does
not fork as Bajutsu grows. This is what makes the three axes independent: they extend the edges,
never the core.

| Fixed core | Where |
|---|---|
| Scenario DSL & grammar | [scenarios](scenarios.md) · [dsl-grammar](dsl-grammar.md) |
| Selector model & deterministic resolution | [selectors](selectors.md) |
| Machine assertions (the only judges) | `assertions.py` · [concepts](concepts.md) |
| observe → act → verify orchestrator | [run-loop](run-loop.md) |
| Evidence subsystem (capturePolicy / manifest) | [evidence](evidence.md) |
| Reporter (manifest / JUnit / HTML) | [reporting](reporting.md) |
| Config layering (`defaults × apps`) | [configuration](configuration.md) |

New platforms add backends behind the `Driver` seam; new hosting moves *where `run` is invoked*,
not *what it does*; new authoring produces the same YAML. The core is the constant.

---

## Near-term north star (a recommendation, not a commitment)

If forced to sequence the vision, the highest-leverage next steps — each de-risking a later one at
the lowest cost — are:

1. **Web via Playwright** ([multi-platform](multi-platform.md), Phase 1). It proves the core is
   truly platform-neutral **inside the existing Linux gate** ([ci](ci.md)) — no Mac, no emulator —
   and exercises the rich end of the capability model (native network/video/semantic).
2. **MCP server** ([roadmap → Integration & automation](roadmap/README.md#integration--automation-mcp)). Low surface area, high leverage for
   the Tier-1 authoring loop, and it does not touch the gate.
3. **Visual-regression assertions** ([roadmap: BE-0029](roadmap/BE-0029-visual-regression-assertions.md)). A
   deterministic capability competitors gate behind AI — a differentiator that *strengthens* the
   directives instead of straining them.

The hosting axis ([cloud-hosting](cloud-hosting.md) / [self-hosting](self-hosting.md)) is a larger,
separable investment; pursue it when the demand is collaborative rather than individual.

> **How this relates to [roadmap](roadmap/README.md):** this page is the *why and the shape* (the north
> star); the roadmap is the *prioritized, living backlog* (the next concrete items). When an item
> here becomes actionable, it appears there with a priority and status; when it ships, it moves to
> the [architecture status table](architecture.md#implementation-status). Keep the three in sync.
