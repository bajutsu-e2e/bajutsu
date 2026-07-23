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

Bajutsu's deterministic dev-time gate — `make check` (lock-check, format-check, ruff, shellcheck,
actionlint, mypy, pytest + coverage), mirrored by the pre-push hook and CI — is what lets many
parallel branches stay green without colliding ([CLAUDE.md](../../CLAUDE.md),
[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)). An audit
of the gate *itself* found it strong but with a confirmed fidelity defect and several missing layers.
This item hardens the gate: it makes CI mirror `make check` by construction, brings the roadmap
scripts under the type-checker, adds branch coverage, and adds two security layers (a security
linter and a dependency-vulnerability gate), keeping CodeQL on GitHub's default setup.

This is purely developer-facing infrastructure: it changes no tool behavior, runtime, or scenario
semantics, and stays within the prime directives — the deterministic gate gains checks, never an LLM.

## Motivation

- **CI had drifted from `make check`.** CI re-implemented each gate step inline instead of invoking
  the Makefile targets, so the duplicated argument lists could diverge — and two already had: the
  shellcheck file list omitted `scripts/merge-roadmap-index.sh` and `demos/tour/demo.sh` (which the
  pre-push hook *does* lint), and the mypy target and the coverage invocation were each duplicated and
  primed to drift next. The whole promise of the shared gate — "green locally predicts green in CI" —
  was quietly undermined.
- **The roadmap scripts were untyped in the gate.** `mypy` covered `bajutsu demos` only; the
  `scripts/` that enforce the roadmap invariants (index / promote / allocate) ran without type
  checking, so a type bug there could break the very machinery that guards the roadmap.
- **Coverage was line-only and global.** An untested branch did not count, and a single 85% floor over
  the whole package could mask a weak module behind well-covered ones.
- **No security linting in the gate.** A tool that shells out, handles auth, and resolves secrets had
  no `S` / bandit lint, so a hardcoded secret, an insecure hash, or an unsafe deserializer would go
  unflagged. (SAST itself was already provided by CodeQL's default setup.)
- **No dependency-vulnerability gate.** Dependabot proposed version bumps, but nothing failed CI on a
  known CVE in the locked dependency graph.

## Detailed design

### CI mirrors `make check` by construction

The `check` job's steps invoke the Makefile targets (`make lock-check / format-check / lint / lint-sh
/ typecheck / test`) rather than re-listing the commands. The commands and the file/target lists now
live only in the Makefile, so CI cannot drift from the local gate or the pre-push hook. `UV_NO_SYNC=1`
is set at the job level to keep the previous `uv run --no-sync` speed (the environment is already
populated by the explicit `uv sync` step). `actionlint` stays inline: it is installed as `./actionlint`
(not on `PATH`), and `make lint-actions` runs it only when it is on `PATH`.

### Type-check the roadmap scripts

`make typecheck` now covers `scripts` (`mypy bajutsu demos scripts`); the one error this surfaced was
fixed. `tests/` is deliberately left out — strict mypy over the test suite surfaces hundreds of
findings and is a separate effort.

### Branch coverage

`[tool.coverage.run] branch = true` makes an untested `if` / `else` path count against the floor even
when every line ran. The floor is raised from 85% to 87% (measured 87.40%) to lock in the gain; it can
be ratcheted further later.

### Security linter (ruff `S` / flake8-bandit)

Ruff's `S` rules are enabled. `S101` (assert) and `S603` (subprocess) are globally ignored — assert
documents internal invariants throughout and bajutsu never runs under `-O`, and `S603` fires on every
subprocess call while ours are argv lists (`shell=False`); the genuinely dangerous `shell=True` form is
still caught by `S602`. `tests/` and `demos/` ignore the whole `S` category; `scripts/` ignores `S607`
(they invoke git / uv via `PATH`). The remaining findings in `bajutsu/` were triaged with two real
fixes — `hashlib.sha1(..., usedforsecurity=False)` for a non-crypto dedup key, and renaming an OAuth
endpoint constant from `_TOKEN` to `_EXCHANGE_URL` so it no longer reads as a hardcoded secret
(`S105`) — plus, for confirmed false positives, an inline `noqa` for an `urlopen` already guarded to
http/https (`S310`) and a file-level `S506` ignore for `_yaml.py`'s `yaml.load` (a `SafeLoader`
subclass), kept file-level so the line stays identical to main where CodeQL already dismissed the
same finding as a false positive.

### SAST (CodeQL default setup, retained)

CodeQL stays on GitHub's **default setup**, which scans Python, GitHub Actions, JavaScript/TypeScript,
and Swift with zero maintenance and automatic updates. This item deliberately does **not** commit an
advanced CodeQL workflow: a Python-only one would conflict with default setup and, once that is
disabled, regress the other three languages' coverage; matching all four would add a macOS Swift build
for little marginal value on the small, pure-Foundation BajutsuKit. The security work here is therefore
the `S` linter and the dependency-vulnerability gate below — CodeQL is left as-is.

### Dependency-vulnerability gate (pip-audit)

A CI `audit` job exports the locked graph (`uv export`, runtime plus all shipped extras) and audits
the pinned versions with `pip-audit --no-deps`; a known CVE fails the job, and a reviewed advisory is
accepted with `--ignore-vuln`. This complements Dependabot (which proposes upgrades) with a hard gate. Enabling
Dependabot **security updates** in repo settings is the recommended companion.

## Alternatives considered

- **Run `make check` wholesale in CI** instead of per-target steps — rejected: the per-step names give
  a readable CI UI, and `make check` depends on the `hooks` target (git config) which is pointless in
  CI. Per-target invocation keeps both the UI and a single source of truth.
- **Patch only the two missing shellcheck files** — rejected as treating the symptom; the inline
  duplication would simply drift again. Routing CI through the Makefile removes the duplication.
- **Add `tests/` to mypy now** — deferred: it surfaces hundreds of findings and wants a focused
  follow-up with relaxed per-module settings.
- **Author this as a Proposal first (a `BE-XXXX` placeholder)** — unnecessary here: the work is
  implemented in the same change, so it is filed directly as Implemented under *Development
  infrastructure*, a sibling to
  [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md).
- **Commit an advanced CodeQL workflow** (Python-only, or all four languages) — rejected: it conflicts
  with the repo's default setup, and either regresses the non-Python languages once default setup is
  disabled (Python-only) or adds a macOS Swift build for marginal value (all four). Default setup
  already provides broad, maintained SAST.
- **A heavier stance** (mutation testing, typing `tests/`, per-file coverage floors) — out of scope;
  noted as future steps.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [CLAUDE.md](../../CLAUDE.md) — the gate as the contract; `make check` mirrored by CI and the
  pre-push hook.
- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
  — the contributor-workflow sibling (self-healing hooks, generated indexes) this extends.
- [.github/workflows/ci.yml](../../.github/workflows/ci.yml), [Makefile](../../Makefile),
  [pyproject.toml](../../pyproject.toml) — the gate this item hardens.
