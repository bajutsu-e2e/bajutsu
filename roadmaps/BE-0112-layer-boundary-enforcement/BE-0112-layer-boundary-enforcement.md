**English** · [日本語](BE-0112-layer-boundary-enforcement-ja.md)

# BE-0112 — Enforce core / contract / periphery layer boundaries in the gate

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0112](BE-0112-layer-boundary-enforcement.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0112") |
| Implementing PR | [#642](https://github.com/bajutsu-e2e/bajutsu/pull/642) |
| Topic | Contributor workflow |
| Related | [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md), [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

Make the layering rule that separates Bajutsu's deterministic core from its periphery an
**executable check in the gate**. Today the rule holds only by the good behavior of the current
code: nothing prevents a core module (`orchestrator/`, `drivers/`, `runner/`, …) from importing a
periphery module (`serve/`, the AI / agent modules, codegen emitters). Declare the layers and their
allowed dependencies with an import-linter-style contract and run it in `make check`, so a
forbidden import fails the gate rather than surviving until someone notices.

## Motivation

The three-layer model behind the codebase is clear enough to state precisely:

1. **Deterministic core** — the path that derives a verdict and evidence deterministically:
   `orchestrator/`, `drivers/base`, `assertions`, `evidence`, `report`, `config`, `runner/`, `env`,
   `preflight`, `doctor`, `lint`. It carries the prime directives and can only live in Bajutsu.
2. **Contract** — the surfaces where the core meets the outside world: the scenario schema
   (`bajutsu schema`), the `Driver` Protocol, and `manifest.json` (versioned by `schemaVersion`).
3. **Periphery** — the consumers of the contract: `serve/`, `mcp/`, the codegen emitters, the AI
   provider paths, webhook notifications, the GitHub helpers. Each depends only on the contract and
   is removable behind an extra.

The periphery is heavy and growing. `serve/` alone is roughly 6,500 lines of Python, over a fifth
of the package, and it carries an operational stack unlike the E2E core: FastAPI, Redis / RQ,
SQLAlchemy / Alembic, OAuth. Hosting (BE-0015 / BE-0016) will grow it further. Each of those
dependencies is already isolated by an extra and an import guard, but the *layering* rule itself —
"the core does not depend on `serve/`; the periphery depends only on the contract" — exists nowhere
as an executable check. It is upheld by the manners of the present implementation, which a future
refactor can quietly break without any check objecting.

Making the rule a gate check turns a convention into a contract. It also de-risks a later decision:
if hosting demand eventually justifies splitting `serve/` into a separate distribution
(`bajutsu-serve`), that split is low-risk only when the dependency direction is already guaranteed.
The rule should come first; the distribution split, if it ever happens, can follow.

## Detailed design

The work is MECE along the five pieces below.

### 1. State the layers explicitly

Write down the membership of each layer (the module lists in *Motivation*) in a form the checker
reads: which packages are core, which constitute the contract, which are periphery. This list is
the single place the architecture is declared.

### 2. Declare the forbidden dependency contracts

Express the directional rules: the deterministic core must not import any periphery package
(`serve/`, the AI / agent modules, codegen emitters, webhook / GitHub helpers); the periphery
reaches the core only through the contract (the `Driver` Protocol, the scenario schema, the
manifest), not the core's internals. Encode these as forbidden / layered contracts.

### 3. Wire the check into the gate

Add the checker (import-linter is the natural fit — it expresses layered and forbidden contracts
declaratively and follows transitive imports) as a `make` target and fold it into `make check` and
CI, alongside the existing lint / typecheck / test steps. It needs no Simulator and runs on Linux,
like the rest of the gate.

### 4. Baseline any existing violations

Run the check against `main` and resolve whatever it flags: either fix the coupling, or record a
narrow, commented allowlist entry for a violation that is intentional and cannot be removed now, so
the gate goes green without hiding real coupling. The allowlist is the exception log, not a
dumping ground.

### 5. Document the layer model and the check

Document the three-layer model and the enforced boundaries in the developer docs (`docs/` +
`docs/ja/` mirror), so a contributor understands why an import fails the gate and where a new
module belongs.

### Machine-checkable outcome

`make check` fails when a deterministic-core module imports a periphery module (or otherwise
violates a declared contract). The check is static and deterministic; no LLM is involved. This is
directive #1 and directive #3 expressed as an architectural contract rather than a convention.

### Prime-directive compliance

The check is static analysis on the import graph — no model, no runtime, nothing on the `run` / CI
verdict path beyond a deterministic pass/fail. It defends the directives structurally: keeping the
core independent of the periphery is what keeps the deterministic verdict path free of the AI and
serve stacks.

## Alternatives considered

- **Rely on the existing import-guard tests.** Rejected as insufficient: those tests lock, per
  subsystem, that a heavy dependency is *not imported on the default runtime path* — a runtime
  behavior for one module. They do not express a directional layering rule across the whole
  package, and they say nothing about a core module importing a periphery module on a non-default
  path. This item adds the missing static, architecture-wide contract; the two are complementary.
- **Split `serve/` into a separate distribution now (`bajutsu-serve`).** Rejected as premature: the
  rule should come first. Once the dependency direction is enforced, in-repo separation already
  gives the design benefit, and the distribution split can wait until hosting demand is confirmed.
  A physical package split is a strictly stronger guarantee than a lint check — the periphery
  becomes literally unimportable from the core's environment rather than merely disallowed by a
  rule someone has to keep green — but it trades that for real packaging cost (a second
  `pyproject.toml`, independent versioning, a release process, cross-package dependency
  management) that is hard to reverse once taken. Revisit the split, as a follow-up item, once one
  of BE-0015 / BE-0016 produces a concrete forcing function — e.g. `serve/` needing a release
  cadence independent of the CLI/core, or a hosting deployment that wants to ship the periphery
  without the core's test/driver surface — rather than splitting speculatively ahead of that
  demand.
- **A hand-rolled grep / AST check.** Rejected: import-linter already expresses layered and
  forbidden contracts declaratively and resolves transitive imports; a bespoke grep is brittle and
  would re-implement, worse, what an established tool does.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] State the layers explicitly (core / contract / periphery membership)
- [x] Declare the forbidden dependency contracts (core ↛ periphery; the contract stays a portable inner layer)
- [x] Wire the checker into a `make` target, `make check`, and CI
- [x] Baseline existing violations (fixed the three misplaced helpers; no allowlist needed)
- [x] Document the layer model and the enforced boundaries (both languages)

Log:

- 2026-07-04: Shipped the check — `import-linter` in `[tool.importlinter]` (pyproject) with two
  contracts: the deterministic core does not import the periphery, and the scenario schema /
  `Driver` Protocol stay a portable inner contract. Wired as `make lint-imports`, folded into
  `make check` and CI. Baselining surfaced three misplaced helpers, fixed rather than allowlisted:
  `screen_size_from_elements` / `shows_app_ui` moved from the record paths to the new core module
  `bajutsu/elements.py`, and `AiConfig` moved from `anthropic_client` to `config` (re-exported for
  the AI paths). Documented the three-layer model and the enforced boundaries in
  `docs/architecture.md` (+ `docs/ja` mirror). Both contracts pass with no allowlist.

## References

`bajutsu/` package layout (the module lists that define the three layers — `orchestrator/`,
`drivers/`, `runner/`, `assertions`, `evidence`, `report`, `config` for the core; `serve/`, `mcp/`,
the codegen emitters and AI paths for the periphery), `tests/serve/test_import_guard.py` (the
per-subsystem runtime import guards this item complements with a static contract), `Makefile` and
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) (the gate this check joins),
[DESIGN.md](../../DESIGN.md) and [docs/architecture.md](../../docs/architecture.md) (the core
/ contract / periphery model this encodes),
[BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
(prior gate-hardening this extends),
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md),
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (the hosting
work that grows the periphery and raises the value of the boundary).
