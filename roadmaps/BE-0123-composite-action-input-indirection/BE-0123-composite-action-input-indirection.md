**English** · [日本語](BE-0123-composite-action-input-indirection-ja.md)

# BE-0123 — Route composite-action inputs through env indirection

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0123](BE-0123-composite-action-input-indirection.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0123") |
| Implementing PR | [#675](https://github.com/bajutsu-e2e/bajutsu/pull/675) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

Two of this repository's composite GitHub Actions —
[`.github/actions/bajutsu-e2e/action.yml`](../../../.github/actions/bajutsu-e2e/action.yml) and
[`.github/actions/boot-simulator/action.yml`](../../../.github/actions/boot-simulator/action.yml) —
expand `${{ inputs.* }}` directly inside shell `run:` blocks instead of passing them through `env:`
first. Every call site today passes literal strings or a trusted prior step's output, so there is no
exploitable path right now, but the pattern is fragile: it is the same shape that turns into a
shell-injection vector the day an input carries attacker-influenced content. This proposal routes
composite-action inputs through `env:` indirection, matching how untrusted fields are already
handled elsewhere in this repository's CI.

## Motivation

`bajutsu-e2e/action.yml` inline-expands six inputs across two `run:` steps:

```yaml
- name: Preflight (bajutsu doctor — non-blocking)
  shell: bash
  run: >-
    uv run --no-sync bajutsu doctor --target "${{ inputs.target }}" --udid "${{ inputs.udid }}"
    --backend "${{ inputs.backend }}" --config "${{ inputs.config }}"
    || echo "doctor: non-blocking (convention score only)"

- name: Run scenarios
  shell: bash
  run: >-
    uv run --no-sync bajutsu run --scenario "${{ inputs.scenarios }}" --target "${{ inputs.target }}"
    --udid "${{ inputs.udid }}" --backend "${{ inputs.backend }}"
    --config "${{ inputs.config }}" --no-erase
```

`boot-simulator/action.yml` does the same for one input:

```yaml
if [ "${{ inputs.wait }}" = "true" ]; then
```

When GitHub Actions expands `${{ ... }}` inside a `run:` block, the substitution happens textually,
before the shell ever parses the line — so any input value containing shell metacharacters
(`` ` ``, `$(...)`, `;`, `"`, etc.) is spliced into the script and interpreted by `bash`, not passed
as an inert argument. This is the well-documented GitHub Actions script-injection pattern (the same
class of issue GitHub's own security lab has written up for `pull_request_target` workflows and
untrusted `github.event.*` fields).

Today this is **not exploitable**: every call site in `.github/workflows/e2e.yml` and
`.github/workflows/idb-monitor.yml` passes either a hardcoded literal (`scenarios:
demos/showcase/scenarios/smoke.yaml`, `target: showcase-swiftui`, `wait: "false"`, etc.) or
`udid: ${{ steps.sim.outputs.udid }}`, which is a UDID string produced by `boot-simulator`'s own
`xcrun simctl` calls — not user- or PR-controlled input. There is no current caller that forwards a
PR title, branch name, issue body, or other attacker-influenced text into these inputs.

Severity: this finding is **unconfirmed** as an active vulnerability — it is a latent pattern, not a
demonstrated exploit, and every present call site is safe. It is flagged because composite actions
are reusable by design: a future workflow (or a future edit to an existing one) could pass a
less-trusted value — e.g. a branch name, PR label, or matrix value derived from event data — into
`target`, `scenarios`, `artifact-name`, or `wait` without anyone noticing the `run:` block was never
hardened against that.

## Detailed design

The fix is mechanical and localized to the two action files, with no product-code or `run`/CI
pass/fail-logic changes:

1. **`bajutsu-e2e/action.yml`: route the two `run:` steps' inputs through `env:`.** For "Preflight"
   and "Run scenarios", add an `env:` block mapping each input to an uppercase environment variable
   (e.g. `TARGET: ${{ inputs.target }}`, `UDID: ${{ inputs.udid }}`, `BACKEND: ${{ inputs.backend }}`,
   `CONFIG: ${{ inputs.config }}`, `SCENARIOS: ${{ inputs.scenarios }}`), then reference `"$TARGET"`,
   `"$UDID"`, etc. inside the shell command instead of the inline `${{ inputs.* }}` expansions. The
   `env:` assignment still substitutes the raw value, but as a shell *variable*, so the shell parses
   it as data rather than splicing it into the script text — the standard mitigation for this class
   of finding.
2. **`bajutsu-e2e/action.yml`: route the "Upload run artifacts" step's `${{ inputs.artifact-name }}`
   the same way.** This one is a `with:` value on a `uses:` step (`actions/upload-artifact`), not a
   `run:` block, so it is not subject to shell injection — no code change needed there, but it is
   worth a one-line note in the action so a future edit doesn't assume the same risk applies.
3. **`boot-simulator/action.yml`: route `${{ inputs.wait }}` through `env:`.** Add
   `env: WAIT: ${{ inputs.wait }}` to the "Boot" step and change `if [ "${{ inputs.wait }}" =
   "true" ]` to `if [ "$WAIT" = "true" ]`.
4. **Add a regression check.** Extend `make lint-actions` (`actionlint`) coverage or a small
   repository-specific check (e.g. a `scripts/` lint or a `grep`-based test) that fails if a
   composite action's `run:` block contains a raw `${{ inputs.` expansion, so this pattern cannot
   silently reappear in a future action or edit.

## Alternatives considered

- **Leave the inputs inline since every current call site is a literal or trusted output.** Rejected
  — the whole motivation for this proposal is that "safe today because nothing untrusted flows in
  yet" is not a durable guarantee for a reusable composite action; the fix is cheap and removes the
  hazard permanently rather than relying on every future caller remembering to only pass literals.
- **Restrict composite-action inputs with a regex/`pattern` validation step instead of `env:`
  indirection.** GitHub Actions has no native input-validation syntax, so this would mean adding a
  shell-based validation step before the risky one — more code than the `env:` indirection fix, and
  it only reduces risk (a permissive-but-imperfect pattern) rather than removing the injection
  vector the way `env:` indirection does.
- **Rewrite the affected steps to avoid shell interpolation entirely (e.g. pass arguments via a
  wrapper script's argv).** More invasive than necessary; `env:` indirection is the standard,
  minimal fix for this exact pattern and keeps the diff small and reviewable.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Route `bajutsu-e2e/action.yml`'s "Preflight" and "Run scenarios" steps' inputs through `env:` indirection
- [x] Note (or confirm no change needed) for `bajutsu-e2e/action.yml`'s `with: name: ${{ inputs.artifact-name }}` upload step
- [x] Route `boot-simulator/action.yml`'s `${{ inputs.wait }}` through `env:` indirection
- [x] Add a regression check that fails on a raw `${{ inputs. }}` expansion inside a composite action's `run:` block

- [#675](https://github.com/bajutsu-e2e/bajutsu/pull/675): routed both composite actions' `run:`-block inputs through `env:` indirection
  (`TARGET`/`UDID`/`BACKEND`/`CONFIG`/`SCENARIOS` in `bajutsu-e2e`, `WAIT` in `boot-simulator`),
  annotated the injection-safe `with:` upload step, and added `tests/test_action_input_indirection.py`
  as the regression net against a raw `${{ inputs. }}` reappearing in a composite `run:` block.

## References

- `.github/actions/bajutsu-e2e/action.yml` — inline-expands `target`, `udid`, `backend`, `config`,
  `scenarios` in two `run:` steps
- `.github/actions/boot-simulator/action.yml` — inline-expands `wait` in its `run:` step
- `.github/workflows/e2e.yml`, `.github/workflows/idb-monitor.yml` — the current call sites, all
  passing literals or `steps.sim.outputs.udid`
- GitHub Security Lab, "Keeping your GitHub Actions and workflows secure: Preventing pwn requests"
  — background on the `${{ ... }}` script-injection pattern this proposal mitigates
- Related: BE-0069 (executable contributor guardrails)
- Originates from the 2026-07-02 codebase-analysis report (security).
