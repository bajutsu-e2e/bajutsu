**English** · [日本語](BE-0191-pluggable-theme-system-serve-ui-ja.md)

# BE-0191 — Pluggable theme system for the serve Web UI (visual tokens and swappable transitions)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0191](BE-0191-pluggable-theme-system-serve-ui.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0191") |
| Implementing PR | [#826](https://github.com/bajutsu-e2e/bajutsu/pull/826), [#837](https://github.com/bajutsu-e2e/bajutsu/pull/837), [#855](https://github.com/bajutsu-e2e/bajutsu/pull/855), [#859](https://github.com/bajutsu-e2e/bajutsu/pull/859), [#881](https://github.com/bajutsu-e2e/bajutsu/pull/881), [#883](https://github.com/bajutsu-e2e/bajutsu/pull/883), [#900](https://github.com/bajutsu-e2e/bajutsu/pull/900) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

Give the serve Web UI a first-class, **pluggable theme system**. A theme is a named bundle
of two things — a set of visual design tokens (colors, and the surface/line/accent palette)
**and** a set of screen-transition definitions (how views, modals, panes, and run-step
progress animate). Themes are selected by `[data-theme="<name>"]` on `<html>`, and — this is
the point — an operator can **drop in their own theme** without touching the source, exactly
the way scenarios and config are dropped in today. The built-in dark/light pair
(`midnight` / `daylight`) becomes just the first two entries in an open registry. A theme can
also be **authored from inside the UI** — a guided editor with live preview, saved locally or
uploaded to the serve instance — so a drop-in file is one authoring path, not the only one.

This item is scoped to the live single-page application (SPA) served by
[`bajutsu serve`](../../README.md) (`bajutsu/templates/serve.html.j2`). The standalone
report / coverage / stats / crawl pages, which use a separate and older `prefers-color-scheme`
mechanism, are explicitly out of scope here (see *Alternatives considered*).

## Motivation

The serve UI already ships a working dark/light mechanism, but it grew ad hoc — without its
own roadmap item — and it hard-codes the assumption that there are exactly two looks:

- **The look is not swappable.** Themes live in `bajutsu/templates/serve.themes.css` as two
  fixed blocks of CSS custom properties keyed by `[data-theme]`, and the header control is a
  **binary** dark/light toggle. The file's own opening comment states the constraint plainly:
  *"Adding a third theme means replacing that switch with a wider picker."* There is no way
  for a user to add a look of their own — no discovery mechanism, no picker, no config hook.
- **The token contract is incomplete, so themes can't be trusted to be total.** `serve.css`
  references custom properties that the registry never defines — `--accent`, `--muted`,
  `--bad` — while the registry defines `--acc`, `--mut`, `--ng` instead. Those rules back live
  UI (the Author tab's step list, enrich accept/dismiss, the coverage sub-hint) and currently
  render with an unset/inherited color rather than the intended themed one. Until every color
  is a defined token, a drop-in theme can't be complete, and fallback can't be relied on.
- **Transitions are not part of the look at all.** View switches happen via an instant
  `replaceChildren`; there is no notion of a themable enter/leave animation for views, modals,
  responsive pane reconstruction (BE-0072), or run-step progress. Motion is a large part of a
  UI's identity, and today it is fixed and invisible to any theme.
- **There is no way to create a theme without filesystem access.** Even once themes are
  drop-in files, authoring one means editing raw CSS on disk and knowing the exact token names
  by heart. A user who just wants to nudge the accent color or slow the view transition has no
  in-product path — the token contract is documented in a comment, not exposed as something you
  can edit and see change.

A pluggable theme system turns the look-and-feel into a **backend-agnostic interface** — the
same design principle the driver layer is built on. The tool defines the token/transition
*contract*; a theme is an *implementation* of it that can be swapped freely, and per-app
differences (an org's brand look) live in a dropped-in theme + config, not in the core
(prime directive 3).

None of this touches the deterministic path. Theming is a pure presentation layer: it never
enters the Tier-2 `run` / CI verdict (prime directive 1), and it introduces no fixed `sleep`
(prime directive 2) — animations are driven by CSS and are disabled under
`prefers-reduced-motion`, so the dogfooded serve-UI regression net stays condition-wait-based
and deterministic.

## Detailed design

The work is decomposed MECE into six units. Units 1–3 deliver a pluggable *visual* theme;
unit 4 adds the swappable *transition* layer; unit 5 keeps the change deterministic and
dogfood-safe; unit 6 adds the in-UI editor + upload so a theme can be authored without
filesystem access.

### 1. Complete the design-token contract (prerequisite)

Make **every** color and surface style in `serve.css` resolve to a defined custom property,
so a theme is a complete, self-contained contract and partial themes fall back predictably.

- Reconcile the naming drift: settle on one canonical set and update all consumers so no
  rule references an undefined variable (`--accent` / `--muted` / `--bad` today resolve to
  nothing). Either alias the missing names in the registry or rewrite the `serve.css`
  consumers — one direction, applied consistently.
- Audit the raw hex literals in `serve.css` (≈15, mostly `#fff` on colored backgrounds) and
  route each through a token (e.g. `--on-acc`, `--on-run`) so no color bypasses the system.
- Document the token contract inline in `serve.themes.css` (the required token names and what
  each means) — this comment *is* the theme-authoring API surface.
- Fallback rule: any token a theme omits inherits from the `:root` default block
  (`midnight`), so an incomplete drop-in theme degrades gracefully rather than breaking.

### 2. Pluggable theme discovery and registration

Turn the fixed two-block registry into an open set discovered at serve startup.

- **Source of themes:** a themes directory, resolved from a new `--themes <dir>` CLI flag on
  `bajutsu serve` (mirroring the existing `--scenarios` / `--runs` / `--baselines` dir flags),
  with an optional `ui.default_theme` field in config for the initial selection. Built-in
  themes ship in-repo as today; discovered themes extend, never replace, them.
- **Theme shape (the plug):** a theme is **declarative only** — a CSS block of token values
  (and, per unit 4, transition definitions) plus a small manifest (display name, `dark` |
  `light` kind). **No arbitrary JavaScript** in a theme. This keeps the trust boundary at the
  same level as scenarios/config (the operator drops the file in themselves) while limiting
  the attack surface to CSS (see *Alternatives considered* on the security trade-off).
- **Registration:** serve scans the directory once at startup, concatenates the discovered
  CSS into the inlined theme stylesheet, and exposes the theme manifest to the client so the
  picker can render the options. Themes are static for the process lifetime, so the existing
  `functools.lru_cache` on `_asset` / `_index_html` in `bajutsu/serve/handler.py` stays valid;
  the discovered set is folded into the cached render. (Live theme reload is out of scope.)

### 3. Theme picker UI

Replace the binary header toggle with a picker, as the registry comment foretold.

- A header dropdown/menu listing built-in + discovered themes, grouped or labeled by dark/light
  kind, replacing the moon/sun checkbox.
- Persist the explicit choice to `localStorage['bajutsu-theme']` (unchanged key), seeded before
  first paint by the existing inline `<script>` to avoid a flash of the wrong theme; an OS
  scheme change still live-updates only while the user has made no manual choice.
- Follow the BE-0058 `data-testid` convention (e.g. `nav.theme-picker`) with disambiguated
  selectors for the header control cluster.
- **Placement:** the picker lives in the `<header>` (like today's toggle) or in a modal — it
  must **not** be a direct child of a `main#view-*` element, because the desktop tiler's
  `rebuild()` calls `replaceChildren` on those and would wipe it on every tile rebuild.

### 4. Swappable, theme-defined transitions

Make screen transitions a themable part of the look, across the four surfaces the discussion
identified: **view switches, modal enter/leave, responsive pane reconstruction (BE-0072), and
run-step progress**.

- **Separation of concerns:** JavaScript applies only *semantic state classes* (e.g.
  `.is-entering` / `.is-leaving` / `.view-switching`) at each transition point; the **theme's
  CSS decides what those states look like**, via `--transition-*` tokens (duration / easing)
  and keyframes. This keeps the JS theme-agnostic and makes the animation itself fully
  pluggable — a theme with no transition rules renders instantly (today's behavior).
- **Tiler refactor (the main cost):** view switches currently destroy the old subtree
  immediately via `replaceChildren`, leaving no element to animate out. Enabling a leave
  animation requires retaining the outgoing node for the duration of its transition — a
  double-buffer step in `rebuild()` — or, as a lighter first cut, animating only the incoming
  view. This is the riskiest part of the change and should be landed behind the reduced-motion
  guard below so it can degrade to the current instant behavior.
- **Reduced motion:** under `prefers-reduced-motion: reduce`, all transitions collapse to
  instant regardless of the active theme. This is both an accessibility requirement and the
  determinism lever for unit 5.

### 5. Determinism and dogfood alignment

- The serve UI is itself dogfooded (BE-0058, `demos/serve-ui/`). The reduced-motion collapse
  (unit 4) is the guarantee that added motion never introduces a race or a need for a fixed
  `sleep`: with motion off, transitions are instant and the existing condition-wait assertions
  hold unchanged.
- `demos/serve-ui/scenarios/theme.yaml` currently taps `nav.theme-toggle` and asserts the
  `checked` state flips. The binary→picker change (unit 3) breaks that assumption, so the
  scenario must be updated in lockstep to drive the picker and assert `[data-theme]`.
- No LLM is anywhere on this path (prime directive 1): theme selection and rendering are
  entirely deterministic client/server code.

### 6. In-UI theme editor, live preview, and upload / export

Add an in-product authoring path so a theme can be created and shared without touching the
filesystem, building directly on the token contract of unit 1.

- **Editor derived from the contract:** a form generated from the documented required tokens
  — a color input per color token, and duration / easing / effect controls per `--transition-*`
  token. Because the contract *is* the API (unit 1), the editor's fields fall out of it rather
  than being hand-maintained.
- **Live preview, client-side:** applying an edit injects a `<style>` block that overrides the
  active `[data-theme]`'s custom properties in the running SPA, so the change is visible
  immediately with no server round trip. Placement follows unit 3 — a modal (like
  `#settingsmodal`), never a direct child of a `main#view-*` element the tiler would wipe.
- **Two persistence tiers:**
  - *Local draft* — the edited theme is stored in `localStorage` as a `custom` theme and
    appears in the picker (unit 3) at once. Browser-only; it adds no server surface or new
    trust boundary, and is the zero-friction path for iterating on a look.
  - *Upload to the serve instance* — writes the theme into the `--themes` directory (unit 2)
    so it becomes a discoverable drop-in, shared across sessions on that instance. This reuses
    the existing upload seam (BE-0073). It is the **one** place that must invalidate the theme
    `lru_cache`: a bounded, explicit re-scan on that write, distinct from the general live
    reload unit 2 excludes.
- **Export / import round-trip:** because a theme is declarative CSS + manifest, "download this
  theme" emits a file that round-trips with the drop-in mechanism (edit in the UI → export →
  commit to a repo or share), and "import" ingests such a file. This closes the loop between the
  two authoring paths.
- **Trust and determinism unchanged:** an uploaded theme is still declarative CSS + manifest
  with no JavaScript (unit 2). The uploader is the authenticated operator acting on their own
  localhost serve, so an uploaded theme carries the same trust as a dropped-in file; the residual
  CSS surface (e.g. a theme referencing an external URL) is accepted at that trust level and
  called out rather than sandboxed. No LLM and no `run`-path impact.

## Alternatives considered

- **Introduce a build step / CSS framework (Sass, Tailwind, a bundler).** Rejected. The serve
  UI is deliberately build-free vanilla JS/CSS read straight off disk; a bundler would violate
  that constraint for a feature that CSS custom properties already express natively.
- **Arbitrary CSS injection via an API or `localStorage` (no files).** Rejected. It complicates
  the asset cache and, more importantly, moves the trust boundary — a dropped-in file sits at
  the same trust level as the operator's scenarios and config, whereas a remote/paste-in CSS
  channel invites injection from a wider surface. Drop-in theme files are the trust boundary.
- **Let themes carry arbitrary JavaScript** (fully programmable themes). Rejected for the same
  reason: a declarative CSS + manifest theme is expressive enough for look and motion while
  keeping the attack surface to CSS only.
- **In-UI editor as client-only (`localStorage`), no server upload.** Rejected as the whole
  story: a browser-local custom theme is great for iterating but is trapped in one browser.
  Both tiers are kept — the local draft for zero-friction editing and an optional upload for
  persistence and sharing across a serve instance.
- **A hosted theme marketplace / remote theme registry.** Not adopted. A serve instance
  discovers local drop-in and uploaded themes; it does not fetch themes over the network. Theme
  sharing rides the export/import file round-trip (and version control), not a central service.
- **Config-only or picker-only (not both).** Rejected in favor of two thin layers: config
  (`ui.default_theme` + `--themes`) sets the deployment/per-target default, and the client
  picker lets a user override locally — matching how the OS-default-then-manual-override
  toggle already behaves.
- **Unify the standalone report / coverage / stats / crawl pages into the same theme system.**
  Deferred, not adopted now — this item is scoped to the SPA. Those pages use a separate,
  older `prefers-color-scheme` mechanism with a different variable set (`--ink` etc.) and no
  toggle; unifying them is worthwhile but is a distinct, larger change. Left as a related
  follow-up.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1. Complete the design-token contract (fix `--accent`/`--muted`/`--bad` drift; route raw hex through tokens; document the contract; define fallback).
- [x] 2. Pluggable theme discovery and registration (`--themes` flag, `ui.default_theme`, declarative theme + manifest, startup scan folded into the cached render).
- [x] 3. Theme picker UI (header dropdown replacing the binary toggle, persistence + pre-paint seeding, `data-testid` convention, tiler-safe placement).
- [x] 4. Swappable, theme-defined transitions (semantic state classes + `--motion-*` tokens across the four surfaces; tiler `rebuild()` refactor; `prefers-reduced-motion` collapse).
- [x] 5. Determinism and dogfood alignment (reduced-motion guarantees condition-wait safety; update `demos/serve-ui/scenarios/theme.yaml` for the picker).
- [x] 6. In-UI theme editor, live preview, and upload / export (contract-derived form, client-side live preview, local-draft + server-upload persistence tiers via a dedicated `POST /api/theme` endpoint, export/import round-trip).

### Log

- Unit 1 — completed the design-token contract: reconciled the `--accent`/`--muted`/`--bad`/`--border`
  naming drift onto the canonical `--acc`/`--mut`/`--ng`/`--line` tokens, routed every raw hex literal
  in `serve.css` through a token (adding `--canvas`, `--on-ok`, `--on-ng`, `--on-mut`, `--on-rung`, and
  the `--rung-*` legend tokens; defined the previously-undefined `--mono` global), documented the token
  contract inline in `serve.themes.css`, and made `:root` (midnight) the fallback source so a partial
  theme degrades gracefully. Added `tests/serve/test_theme_tokens.py` to guard the invariant. ([#826](https://github.com/bajutsu-e2e/bajutsu/pull/826))
- Unit 2 — pluggable theme discovery and registration. Added `bajutsu/serve/themes.py` (a declarative
  theme = a `*.css` block of tokens + a leading `/* bajutsu-theme name:/kind: */` manifest, no
  JavaScript), a `bajutsu serve --themes <dir>` flag scanned once at startup and folded into the
  cached `_index_html` render, and a serve-only `ui.default_theme` config key (dropped from the core
  `Config` like `orgs:`, read in `themes.read_default_theme`). The registered manifest (built-in +
  discovered) and the configured default are exposed to the client as `window.__bajutsuThemes` /
  `window.__bajutsuDefaultTheme` for the picker (unit 3); the pre-paint seed now prefers the
  configured default before the OS scheme. Tests: `tests/serve/test_themes.py`,
  `tests/serve/test_http_themes.py`, and a core `ui:`-drop test. ([#837](https://github.com/bajutsu-e2e/bajutsu/pull/837))
- Unit 3 — theme picker UI. Replaced the two-state header toggle with a native `<select>`
  (`data-testid="nav.theme-picker"`) rendered server-side from the registered manifest, grouped by
  dark/light kind, each option value being the theme id; `serve.core.js` seeds the widget from the
  applied theme, persists an explicit pick to `localStorage['bajutsu-theme']`, and follows the OS
  scheme only while no explicit pick stands, resolving the OS scheme against the registry's kinds
  (not just the built-in pair). The pre-paint seed was already default-then-OS aware (unit 2) and is
  unchanged. A native control stays keyboard/screen-reader accessible and never overlaps the tiler
  (BE-0072). Added a `tests/serve/test_http_themes.py` case asserting the picker renders one option
  per theme and the old toggle is gone, updated `docs/web-ui.md` (+ ja mirror), and rewrote the
  `demos/serve-ui/scenarios/theme.yaml` dogfood in lockstep. The dogfood asserts the picker's initial
  state — that it renders and reflects the OS scheme — but does not drive a selection change: the DSL
  has no action that switches a native `<select>` deterministically (`type` typeahead fired under one
  headless Chromium build but was a no-op in CI). A deterministic switch needs a dedicated
  select-option Driver action, which lands with unit 5. ([#855](https://github.com/bajutsu-e2e/bajutsu/pull/855))
- Unit 5 — determinism and dogfood alignment. Added a `selectOption` DSL action (`SelectOption`
  model + `Step.select_option`, dispatched in `orchestrator/actions/handlers/gestures.py`) and the
  `Driver.select_option(sel, option)` protocol method it drives: the Playwright backend resolves the
  `<select>` through the shared `resolve_unique` core, then locates it at the resolved point
  (`elementFromPoint` — the same coordinate a click uses, so matching stays in the determinism core)
  and sets the option by value + fires `change`; the fake driver resolves + records; idb / adb /
  xcuitest raise `UnsupportedAction` (a `<select>` has no native counterpart). With this, the
  `demos/serve-ui/scenarios/theme.yaml` dogfood now drives the picker in both directions and asserts
  the switch, closing the gap unit 3 deferred. The reduced-motion condition-wait guarantee is
  trivially held today (no transitions exist yet) and the `prefers-reduced-motion` collapse lands
  with unit 4's motion. Tests: `tests/test_select_option.py`, plus `select_option` cases in
  `tests/test_playwright.py` / `test_idb.py` / `test_adb.py` / `test_xcuitest.py`; docs updated in
  `docs/scenarios.md` and `docs/dsl-grammar.md` (+ ja mirrors). ([#859](https://github.com/bajutsu-e2e/bajutsu/pull/859))
- Unit 4 — swappable, theme-defined transitions. Extended the token contract with a `--motion-*` set
  (view / modal / pane durations, a shared easing, and enter/leave animation-name tokens) defined in
  the `:root`/midnight fallback block and documented inline in `serve.themes.css`. The client applies
  only semantic state classes: `showView` plays an enter animation on the incoming view; a
  `MutationObserver` plays a modal's enter whenever it is unhidden (so no open site changed) and a new
  `closeModal` helper animates the leave before hiding; the phone-tier pane switch fades the pane
  brought to full width; and the tiler `rebuild()` (`serve.author.js`) is double-buffered — because
  `render()` moves the live panel nodes into the new root, a `data-testid`-stripped deep clone of the
  outgoing root is held absolute over the view as a visual ghost while the rebuilt root animates in,
  so no selector is ever briefly ambiguous. All motion collapses under `prefers-reduced-motion:
  reduce`, and the Playwright backend now opens every context with `reduced_motion="reduce"` — the
  determinism lever (unit 5): in the dogfood and CI every transition is instant, the ghost double-buffer
  reduces to the original instant `replaceChildren`, and no condition-wait races an animation. Tests:
  `--motion-*` contract + reduced-motion collapse in `tests/serve/test_theme_tokens.py`, the
  `reduced_motion` context assertion in `tests/test_playwright.py`; docs updated in `docs/web-ui.md`
  (+ ja mirror). ([#881](https://github.com/bajutsu-e2e/bajutsu/pull/881))
- Unit 6 (part 1 of 2) — in-UI theme editor, live preview, client-side export/import. Exposed the
  design-token contract as JSON: `themes.parse_theme_tokens` extracts every `--*` declaration from
  `serve.themes.css` and categorizes it (color vs `--motion-*`, inferring duration/easing/keyframe),
  and a new `serve/operations/theme_editor.py::get_theme_contract` fills each token's default from the
  `:root`/midnight block, served at `GET /api/themecontract`. The client (`serve.core.js`) generates
  the editor form from that contract — a color input per color token, a text input per motion token —
  in a tiler-safe `#thememodal`; editing injects a `<style id="theme-editor-preview">` block so the
  change previews live with no server round trip. Save-to-local-draft (`localStorage`), CSS export
  (round-trips with the drop-in format), and import round out the client side. Tests:
  `tests/serve/test_theme_contract_parse.py` (parser) and `tests/serve/test_theme_editor.py` (the
  contract endpoint reads the real bundled CSS and fills defaults). Still to land in part 2: surfacing
  the local draft in the picker, server upload into `--themes` (BE-0073 seam) with a bounded
  `lru_cache` invalidation, and the dogfood scenario. ([#883](https://github.com/bajutsu-e2e/bajutsu/pull/883))
- Unit 6 (part 2 of 2) — local-draft picker surfacing + server upload, completing the unit. The
  client (`serve.core.js`) now surfaces the saved local draft as a `custom` entry in the header
  picker: `surfaceCustomDraft` registers it in the theme list, injects a scoped
  `<style id="theme-custom">[data-theme="custom"]{…}</style>` (so it paints only when selected, with
  omitted tokens falling back to `:root`), and adds/refreshes its `<option>`; "Save to Local Draft"
  then switches to it, and the editor gained a name field so a draft/export/upload is named.
  Server upload lands as a dedicated `POST /api/theme` (`{name, kind, tokens}`) rather than the
  BE-0073 zip seam — a theme is a single small declarative file, so the write mirrors the
  apikey/config write pattern: `theme_editor.upload_theme` requires `--themes`, slugs the name into
  the `[data-theme]` id (so the selector always matches the filename stem), guards every token
  name/value against breaking out of the rule, refuses a built-in-id collision, composes the
  canonical manifest + rule, and invalidates the `_index_html` `lru_cache` (the one place unit 2's
  live-reload exclusion is lifted) so the drop-in lists on the next render. Both the contract GET and
  the upload POST are wired into the FastAPI backend too (`server/app.py`), closing a part-1 gap
  where the editor only worked on the stdlib handler. The upload button ships hidden and is revealed
  only when `--themes` is configured (a server-set `window.__bajutsuThemesWritable` flag). The theme
  modal was made to scroll its form within a capped box with a pinned action footer, so Save/Upload
  never fall below the fold. Tests: `tests/serve/test_theme_upload.py` (op + stdlib route + cache
  invalidation), `test_server_app.py` (both FastAPI routes), and the `demos/serve-ui/scenarios/theme-editor.yaml`
  dogfood (local-draft → `custom` picker surfacing; upload button gated off without `--themes`); docs
  updated in `docs/web-ui.md` (+ ja mirror). ([#900](https://github.com/bajutsu-e2e/bajutsu/pull/900))

## References

- [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) — the original `bajutsu serve` Web UI (foundation).
- [BE-0072](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md) — responsive serve UI; introduced the `viewswitch` / pane-stacking layout whose transitions this item themes.
- [BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) — dogfooding the serve UI; sets the `data-testid` convention and owns `demos/serve-ui/scenarios/theme.yaml`.
- [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) — the serve upload seam (zip-bundle upload) the in-UI theme upload (unit 6) reuses.
- [BE-0183](../BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings.md), [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md) — precedent for a persisted user-preference surface in serve (AI provider settings), the closest pattern to a persisted theme choice.
- `bajutsu/templates/serve.themes.css` — the current two-block theme registry (its opening comment foretells the picker).
- `bajutsu/templates/serve.css`, `bajutsu/templates/serve.js`, `bajutsu/serve/handler.py` — the CSS consumers, the client logic (including the tiler `rebuild()`), and the asset-inlining/caching layer.
