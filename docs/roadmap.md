**English** · [日本語](ja/roadmap.md)

# Bajutsu roadmap / backlog

> A **living document** that gathers the features we want to build next. Drop any new idea
> into the [Unsorted ideas](#unsorted-ideas) bin first, then promote it into the tables below
> once it firms up.
>
> - **The accurate list of what exists today (implemented / unwired)** lives in
>   [architecture.md#implementation-status](architecture.md#implementation-status) — that is
>   the source of truth. This page is about what's *next*.
> - The design background (the *why*) is in [`../DESIGN.md`](../DESIGN.md).
> - The **strategic "shape" of the whole (the north star)** is in [vision.md](vision.md).
>   This page is the granular backlog; vision is the umbrella over it.

## Legend

**Priority** — `P0` (do next) / `P1` (will do) / `P2` (nice to have) / `P3` (idea stage)
**Status** — 💡 idea / 📋 planned / 🚧 in progress / ❄️ on hold / leaning out-of-scope / ✅ done (once done, remove from the table and reflect it in architecture.md)

---

## 1. On-device validation (M1 close-out)

The deterministic core runs end-to-end on the FakeDriver, and **the idb backend's subprocess
execution (`describe-all` parsing, frame-center tap/text/swipe) and the simctl launch sequence
are validated on a real device (iPhone 17 Pro / latest iOS)** (`make -C demos/features e2e` +
the `e2e.yml` CI, [architecture.md](architecture.md#implementation-status)). What remains is
only ongoing maintenance monitoring.

| Feature | Summary | Priority | Status | Source / related |
|---|---|---|---|---|
| `idb_companion` version monitoring | Keep up with idb's own maintenance cadence and compatibility with the latest runtimes. Pin/monitor the version in CI | P1 | 💡 | [DESIGN §11](../DESIGN.md) |
| idb element-tree normalization accuracy | Continuously confirm on a real device that the tree representation of standard SwiftUI elements (e.g. `.searchable`) does not break | P1 | 💡 | [DESIGN §11](../DESIGN.md) |

## 2. Platform expansion (Android / Flutter)

The scope is currently **limited to the iOS Simulator** ([DESIGN §1](../DESIGN.md)). This is the
broad direction of going multi-platform by leveraging the driver / backend abstractions. It is a
**strategic decision that entails updating the core scope statement (DESIGN §1 and the README)**.

> **The concrete approach and design (the mapping for selector portability, per-platform
> backends, and the rollout order) are detailed in [multi-platform.md](multi-platform.md).**
> The table below is a backlog summary of that.

| Feature | Summary | Priority | Status | Source / related |
|---|---|---|---|---|
| Android backend | A driver for the Android emulator, driving it via adb + UIAutomator and the like. Maps `resource-id` / `content-desc` selectors id-first | P2 | 💡 | [DESIGN §5](../DESIGN.md), `bajutsu/drivers/` |
| Flutter support | Flutter renders its own UI, so elements rarely surface in the OS a11y tree. Consider resolving through Flutter's semantics tree (`integration_test` / VM Service / Flutter Driver) | P2 | 💡 | — |
| Cross-platform abstractions | Design-review whether selector resolution, the stability-order ladder, and the evidence subsystem can be reused across OSes (platform differences absorbed on the abstraction side) | P2 | 💡 | [DESIGN §5](../DESIGN.md), [architecture.md](architecture.md) |
| Update the scope statement | Revise "what we do / don't do" and the product description from iOS-only to multi-platform | P2 | 💡 | [DESIGN §1](../DESIGN.md), [`../README.md`](../README.md) |

## 3. Authoring experience (record / GUI editor)

The AI-driven `record` (Tier 1) is implemented ([recording.md](recording.md)). The aim here is
**non-AI action capture** and **visual editing of scenarios**, to make the §6.5 round trip
(record → edit → re-run) easy for humans. The local web UI launcher `bajutsu serve` (run
scenarios + view reports in the browser) is implemented as the first step toward this (table below).

| Feature | Summary | Priority | Status | Source / related |
|---|---|---|---|---|
| Local web UI (`bajutsu serve`) | A small launcher for the scenario / app list, one-click runs, streaming run logs, and in-browser report display (stdlib only). A Tier 1 convenience that stays out of the CI gate. The first step toward a GUI editor (visual editing, element picker) | P3 | ✅ | `bajutsu/serve.py`, [cli.md](cli.md) |
| Action-capture record | Record real operations on the Simulator (tap / type / swipe) into a scenario (AI-independent). Needs idb event capture or accessibility-event monitoring | P2 | 💡 | [DESIGN §6.5](../DESIGN.md), `bajutsu/record.py` |
| Scenario GUI editor | Visually edit the scenario YAML / assertion DSL. Pick an element on a screenshot → settle on a selector, integrated with the doctor score | P3 | 💡 | [scenarios.md](scenarios.md), [selectors.md](selectors.md) |
| Demarcation from the existing AI record | Document the division of roles between "AI explores and writes" and "transcribe a human's operations", plus conversion between them | P3 | 💡 | [recording.md](recording.md) |
| Public hosting of the web UI | Turn the local `serve` into a shared, public service. Split into a control plane (Linux: FastAPI + Postgres + Redis + R2) and a macOS worker pool (Orka), adding auth, isolation, and per-run Simulators. Entails a core refactor turning `subprocess.Popen` into a job queue | P3 | 💡 | [cloud-hosting.md](cloud-hosting.md), `bajutsu/serve.py` |
| Self-hosting of the web UI | A setup that runs on your own Mac. Stage A (Tailscale + LaunchAgent to run the current `serve` immediately) and Stage B (Docker Compose: Postgres/Redis/MinIO/Authelia + your own Mac worker pool). An operations guide covering the fact that the Simulator requires a GUI session | P3 | 💡 | [self-hosting.md](self-hosting.md), `bajutsu/serve.py` |

## 4. Integration & automation (MCP)

| Feature | Summary | Priority | Status | Source / related |
|---|---|---|---|---|
| MCP server | Expose `run` / `doctor` / `record` / `codegen` as MCP tools so agents like Claude can drive them directly. A good fit with Tier 1 (AI authoring) | P2 | 💡 | [cli.md](cli.md), `bajutsu/agent.py` / `claude_agent.py` |
| Return evidence as MCP resources | Expose run results such as `manifest.json` / `report.html` as resources an agent can read | P2 | 💡 | [reporting.md](reporting.md) |

## 5. Backend expansion (iOS actuators)

| Feature | Summary | Priority | Status | Source / related |
|---|---|---|---|---|
| XCUITest backend | A second actuator after idb. Make it registerable at the top of the stability-order ladder (the abstraction is already maintained) | P2 | 💡 | [DESIGN §5 / §3](../DESIGN.md), `bajutsu/backends.py` |
| Multi-backend evidence fallback | The actuator is currently single. Absorb capability gaps by routing only evidence capture to a different backend (designed in §9, not yet wired) | P2 | 💡 | [drivers.md](drivers.md), [DESIGN §9](../DESIGN.md) |

## 6. Self-healing triage (M4)

Lower the maintenance cost of regressions while keeping AI out of the judge role and limited to
an investigator.

| Feature | Summary | Priority | Status | Source / related |
|---|---|---|---|---|
| AI triage (root-cause summary, fix suggestions) | AI reads the failure evidence and produces a root-cause summary and fix suggestions (human review assumed). `bajutsu triage` (rule-based) plus `--ai` (Claude, including the failure screenshot). The deterministic `trace` command is the layer beneath it | P2 | ✅ | [DESIGN §3.1 / §12](../DESIGN.md), `bajutsu/triage.py` · `bajutsu/claude_triage.py` |
| `update` (minimal-diff proposals = applying structured fixes) | Update a broken scenario with a minimal diff instead of re-recording the whole thing. Triage proposes a structured fix (`renameId`/`addIndex`/`raiseTimeout`) → `--apply` (dry-run diff) / `--write` applies it to the source, `--rerun` verifies by re-running. The rename and addIndex closed loops are proven on a real device | P2 | ✅ | [DESIGN §6.5](../DESIGN.md), `bajutsu triage --apply` |
| Guards against "making tests laxer" | A brake against the risk of self-healing loosening pass/fail. A fix is **always reviewed by a human as a diff and explicitly applied with `--write`** (never auto-applied); a fragment mismatch is a safe no-op | P2 | ✅ | [DESIGN §11](../DESIGN.md) |

## 7. doctor / onboarding

> The doctor feasibility gate (the CLI suite + a check for a booted Simulator) is
> **implemented** ([architecture.md](architecture.md#implementation-status)). Add new
> onboarding candidates here as they come up.

## 8. codegen coverage

| Feature | Summary | Priority | Status | Source / related |
|---|---|---|---|---|
| Coordinate swipe generation | `swipe { from, to }` currently falls back to a `// TODO` | P2 | 💡 | [codegen.md](codegen.md) |
| Shrink unsupported syntax | Reduce the range of cases (e.g. unknown selectors) that drop to a `// TODO` | P3 | 💡 | [codegen.md](codegen.md) |

## 9. Miscellaneous / on hold

| Feature | Summary | Priority | Status | Source / related |
|---|---|---|---|---|
| `mockServer` (external mock) | Only the config schema exists. It has been superseded by declarative in-protocol `mocks` (implemented), so whether an external-server approach is really needed is open | P3 | ❄️ | [architecture.md](architecture.md#implementation-status), `config.py` `MockServer` |
| Guard against over-matching evidence rules | Prevent artifacts from bloating due to over-matching capturePolicy (an `--explain` dry run, a lighter default policy) | P2 | 💡 | [DESIGN §11](../DESIGN.md) |

---

## 10. Candidates from competitive research (MagicPod / Autify)

MagicPod and Autify have **AI self-healing + no-code + cloud device farm + visual testing** in
their DNA. Both companies' flagship feature is "**AI auto-corrects locators / tap positions
during a run**", but that collides head-on with Bajutsu's core ([DESIGN §2](../DESIGN.md):
**keep AI out of the CI gate / determinism first**). So we evaluated them split into "things we
can adopt deterministically as-is" and "**things we can adopt only outside the gate (Tier 1 /
triage)**".

### 10.1 Adopt (deterministic, aligned with the philosophy)

| Feature | Summary / shape in Bajutsu | Origin | Priority | Status | Related |
|---|---|---|---|---|---|
| Visual-regression assertions | A **new assertion type** that diffs a screenshot against a baseline. Supports exclusion regions and per-device / per-locale baselines. Because it is a deterministic machine check rather than AI, it fits "pass/fail by machine assertions only" | Both | P1 | 💡 | [DESIGN §6.4](../DESIGN.md), [evidence.md](evidence.md) |
| Parameterized shared steps | Define and call **reusable components with arguments** via the `use` step, expanding `${params.*}` (`expand_components`). Usable alongside the `setup` prelude (no args). DRYs up common steps like login | MagicPod | P1 | ✅ | `bajutsu/scenario.py` (`use`/`expand_components`), [scenarios.md](scenarios.md) |
| Data-driven scenarios | Repeat one scenario over multiple rows via `data` (inline) / `dataFile` (CSV). Substitute `${row.*}` per row (`expand_data`). Effective for multilingual / boundary-value testing | MagicPod | P2 | ✅ | `bajutsu/scenario.py` (`expand_data`), [scenarios.md](scenarios.md) |
| Secret variables | Resolve `${secrets.X}` from environment variables for use in input, and **automatically mask their real values in evidence** (extending the existing `redact` down to input values). Declared in config under `secrets:` | MagicPod | P2 | ✅ | `bajutsu/interp.py` · `bajutsu/redaction.py`, [evidence.md](evidence.md) |
| Scenario variables + light control flow | The `${...}` interpolation primitive (`interp.py`, handling params/row/secrets uniformly) is implemented. What remains is **capturing UI values → reusing them later (`vars.*`)** and conditionals / loops within bounds that don't break determinism | MagicPod | P2 | 🚧 | `bajutsu/interp.py`, [scenarios.md](scenarios.md) |
| Tags / labels + selective runs | Run a subset of scenarios by `tags` with `--tag`/`--exclude` (include/exclude, exclude wins, `select_scenarios`). Effective for staged CI runs | MagicPod | P2 | ✅ | `bajutsu/scenario.py` (`select_scenarios`), [cli.md](cli.md) |
| Extended device-control primitives | **Location (`setLocation`) and push notifications (`push`) are implemented.** What remains is timezone / clipboard / foreground-background transitions / shake, etc. (`rotate`/`swipe`/`pinch` already exist) | MagicPod | P2 | 💡 | [DESIGN §6.2](../DESIGN.md), `bajutsu/scenario.py` |
| Utility steps | Issue HTTP requests / generate OTP / 2FA codes / verify received email via APIs. Needed for automating real-app login flows | MagicPod | P3 | 💡 | [scenarios.md](scenarios.md) |
| WebView / hybrid support | Currently assumes a native a11y tree. Bridge into the DOM inside a WebView | MagicPod | P3 | 💡 | [drivers.md](drivers.md) |

### 10.2 Adopt outside the gate only (Tier 1 / triage only, never in the CI gate)

| Feature | Summary / shape in Bajutsu | Origin | Priority | Status | Related |
|---|---|---|---|---|---|
| Autonomous crawl exploration (App Explorer style) | AI **autonomously crawls screen transitions to generate a screen map + reports crashes / unreachable states**. Strengthens Tier 1 `record`. Fits "AI = explorer" | Autify VAX | P2 | 💡 | [recording.md](recording.md), [DESIGN §3.1](../DESIGN.md) |
| Self-healing limited to "propose + opt-in apply" | Both companies auto-correct during a run. Bajutsu stays with §6's **triage proposes a minimal diff → human reviews the diff → explicitly applies with `--write`** (no implicit in-run correction = the "making tests laxer" guard, [DESIGN §11](../DESIGN.md)) | Both | P2 | ✅ | [§6](#6-self-healing-triage-m4) |
| AI assertions | AI judges a natural-language expectation. **Never put into the CI gate** (it breaks determinism). Limited to draft assistance in record / triage | MagicPod | P3 | ❄️ | [DESIGN §2 / §3.1](../DESIGN.md) |

### 10.3 Not adopting (already covered / out of scope)

- **Change history / version management** — already covered, since scenarios are YAML under git.
- **Cloud device farm / real-device / cloud execution** — out of the current iOS-Simulator-only scope ([DESIGN §1](../DESIGN.md)). Multi-platform is tracked separately in [§2](#2-platform-expansion-android--flutter).
- **Per-step screenshots / UI tree on error / device logs** — already covered by the evidence subsystem (capturePolicy + the `result:error` safety net).
- **NL→test generation (Autopilot equivalent)** — overlaps with the existing `record` + [§3](#3-authoring-experience-record--gui-editor).
- **Scheduling / Slack / TestRail integration** — the domain of the CI / notification layer. Low priority (separately, if needed).
- **Automatic retry of failed tests** — in tension with determinism-first (no fixed sleeps, condition waits). It can hide flakiness, so if adopted at all it should be limited to quarantine use and needs careful consideration.

---

## Unsorted ideas

> Drop half-formed thoughts here. Promote them into the tables above later.

-
