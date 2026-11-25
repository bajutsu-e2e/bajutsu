**English** · [日本語](BE-0067-code-quality-gate-hardening-ja.md)

# BE-0067 — Code-quality gate hardening (CI fidelity, security lint, supply-chain)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0067](BE-0067-code-quality-gate-hardening.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0067") |
| Implementing PR | [#170](https://github.com/bajutsu-e2e/bajutsu/pull/170) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

The deterministic dev-time gate of Bajutsu is `make check`, which runs lock-check, format-check, ruff, shellcheck, actionlint, mypy, and pytest with coverage. The pre-push hook and CI mirror it. The gate lets many parallel branches stay green without colliding ([CLAUDE.md](../../CLAUDE.md), [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)).

An audit of the gate *itself* found it strong, with one confirmed fidelity defect and several missing layers. This item hardens the gate in five ways:

- CI mirrors `make check` by construction.
- The roadmap scripts come under the type-checker.
- Branch coverage is added.
- A security linter and a dependency-vulnerability gate add two security layers.
- CodeQL stays on the default setup of GitHub.

The work is purely developer-facing infrastructure. It changes no tool behavior, runtime, or scenario semantics, and it stays within the prime directives: the deterministic gate gains checks, never an LLM.

## Motivation

- **CI had drifted from `make check`.** CI re-implemented each gate step inline instead of invoking the Makefile targets, so the duplicated argument lists could diverge. Two had already diverged. The shellcheck file list omitted `scripts/merge-roadmap-index.sh` and `demos/tour/demo.sh`, which the pre-push hook *does* lint. The mypy target and the coverage invocation were each duplicated too, and primed to drift next. The whole promise of the shared gate, "green locally predicts green in CI", was quietly undermined.
- **The roadmap scripts were untyped in the gate.** `mypy` covered `bajutsu demos` alone. The `scripts/` that enforce the roadmap invariants (index, promote, allocate) ran without type checking. A type bug there could break the very machinery that guards the roadmap.
- **Coverage was line-only and global.** An untested branch did not count. A single 85% floor over the whole package could mask a weak module behind well-covered ones.
- **No security linting in the gate.** The tool shells out, handles auth, and resolves secrets, yet it had no `S` / bandit lint. A hardcoded secret, an insecure hash, or an unsafe deserializer would go unflagged. (CodeQL's default setup already provided static application security testing (SAST) itself.)
- **No dependency-vulnerability gate.** Dependabot proposed version bumps, but nothing failed CI on a known CVE in the locked dependency graph.

## Detailed design

### CI mirrors `make check` by construction

The steps of the `check` job invoke the Makefile targets (`make lock-check / format-check / lint / lint-sh / typecheck / test`) rather than re-listing the commands. The commands and the file and target lists now live in the Makefile alone, so CI cannot drift from the local gate or the pre-push hook.

`UV_NO_SYNC=1` is set at the job level to keep the previous `uv run --no-sync` speed, because the explicit `uv sync` step already populates the environment. `actionlint` stays inline. The job installs it as `./actionlint`, which is not on `PATH`, and `make lint-actions` runs it only when it is on `PATH`.

### Type-check the roadmap scripts

`make typecheck` now covers `scripts` (`mypy bajutsu demos scripts`). The one error that surfaced was fixed. `tests/` is deliberately left out: strict mypy over the test suite surfaces hundreds of findings and is a separate effort.

### Branch coverage

`[tool.coverage.run] branch = true` makes an untested `if` / `else` path count against the floor, even when every line ran. The floor rises from 85% to 87% (measured 87.40%) to lock in the gain. A later change can ratchet it further.

### Security linter (ruff `S` / flake8-bandit)

The `S` rules of ruff are enabled. Two rules are ignored globally:

- `S101` (assert). Asserts document internal invariants throughout, and bajutsu never runs under `-O`.
- `S603` (subprocess). It fires on every subprocess call, while ours are argv lists (`shell=False`). `S602` still catches the genuinely dangerous `shell=True` form.

`tests/` and `demos/` ignore the whole `S` category. `scripts/` ignores `S607`, because they invoke git and uv via `PATH`.

The remaining findings in `bajutsu/` were triaged. Two were real and got fixes:

- `hashlib.sha1(..., usedforsecurity=False)` for a non-crypto dedup key.
- A rename of an OAuth endpoint constant from `_TOKEN` to `_EXCHANGE_URL`, so that it no longer reads as a hardcoded secret (`S105`).

Two confirmed false positives got suppressions:

- An inline `noqa` for an `urlopen` already guarded to http and https (`S310`).
- A file-level `S506` ignore for the `yaml.load` in `_yaml.py`, which uses a `SafeLoader` subclass. The ignore is file-level so that the line stays identical to main, where CodeQL already dismissed the same finding as a false positive.

### SAST (CodeQL default setup, retained)

CodeQL stays on the **default setup** of GitHub. That setup scans Python, GitHub Actions, JavaScript/TypeScript, and Swift with zero maintenance and automatic updates. This item deliberately does **not** commit an advanced CodeQL workflow:

- A Python-only workflow would conflict with default setup. Once default setup is disabled, the workflow would regress the coverage of the other three languages.
- A workflow for all four languages would add a macOS Swift build. The small, pure-Foundation BajutsuKit gains little from it.

The security work here is therefore the `S` linter and the dependency-vulnerability gate below. CodeQL is left as-is.

### Dependency-vulnerability gate (pip-audit)

A CI `audit` job exports the locked graph (`uv export`, runtime plus all shipped extras). It audits the pinned versions with `pip-audit --no-deps`. A known CVE fails the job, and a reviewed advisory is accepted with `--ignore-vuln`. The gate complements Dependabot, which proposes upgrades, with a hard check. Enabling Dependabot **security updates** in the repo settings is the recommended companion.

## Alternatives considered

- **Run `make check` wholesale in CI** instead of per-target steps. Rejected. The per-step names give a readable CI UI. `make check` also depends on the `hooks` target (git config), which is pointless in CI. Per-target invocation keeps both the UI and a single source of truth.
- **Patch the two missing shellcheck files alone.** Rejected as treating the symptom. The inline duplication would drift again. Routing CI through the Makefile removes the duplication.
- **Add `tests/` to mypy now.** Deferred. It surfaces hundreds of findings and wants a focused follow-up with relaxed per-module settings.
- **Author this as a Proposal first (a `BE-XXXX` placeholder).** Unnecessary here. The work is implemented in the same change, so it is filed directly as Implemented under *Development infrastructure*, a sibling to [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md).
- **Commit an advanced CodeQL workflow** (Python-only, or all four languages). Rejected. It conflicts with the default setup of the repo. A Python-only workflow regresses the non-Python languages once default setup is disabled, and a four-language workflow adds a macOS Swift build for marginal value. Default setup already provides broad, maintained SAST.
- **A heavier stance** (mutation testing, typing `tests/`, per-file coverage floors). Out of scope, and noted as future steps.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [CLAUDE.md](../../CLAUDE.md) — the gate as the contract; `make check` mirrored by CI and the
  pre-push hook.
- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
  — the contributor-workflow sibling (self-healing hooks, generated indexes) this extends.
- [.github/workflows/ci.yml](../../.github/workflows/ci.yml), [Makefile](../../Makefile),
  [pyproject.toml](../../pyproject.toml) — the gate this item hardens.
