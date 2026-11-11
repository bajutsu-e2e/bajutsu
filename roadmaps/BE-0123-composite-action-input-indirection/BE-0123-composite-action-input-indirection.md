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

Two composite GitHub Actions in this repository expand `${{ inputs.* }}` directly inside shell `run:`
blocks instead of passing the values through `env:` first. The two actions are
[`.github/actions/bajutsu-e2e/action.yml`](../../.github/actions/bajutsu-e2e/action.yml) and
[`.github/actions/boot-simulator/action.yml`](../../.github/actions/boot-simulator/action.yml).

Every call site today passes literal strings or the output of a trusted prior step, so no exploitable
path exists right now. The pattern is still fragile. It turns into a shell-injection vector the day an
input carries attacker-influenced content. This proposal routes composite-action inputs through `env:`
indirection, which matches how this repository's CI already handles untrusted fields elsewhere.

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

GitHub Actions expands `${{ ... }}` inside a `run:` block textually, before the shell parses the line.
An input value that contains shell metacharacters (`` ` ``, `$(...)`, `;`, `"`, and so on) is therefore
spliced into the script and interpreted by `bash`, instead of arriving as an inert argument. This is
the well-documented GitHub Actions script-injection pattern. GitHub's own security lab has written up
the same class of issue for `pull_request_target` workflows and untrusted `github.event.*` fields.

Today the pattern is **not exploitable**. Every call site in `.github/workflows/e2e.yml` and
`.github/workflows/idb-monitor.yml` passes one of two things:

- A hardcoded literal, such as `scenarios: demos/showcase/scenarios/smoke.yaml`,
  `target: showcase-swiftui`, or `wait: "false"`.
- `udid: ${{ steps.sim.outputs.udid }}`, a UDID string that `boot-simulator`'s own `xcrun simctl` calls
  produce, so neither a user nor a PR controls it.

No current caller forwards a PR title, branch name, issue body, or other attacker-influenced text into
these inputs.

The finding is **unconfirmed** as an active vulnerability. It is a latent pattern, not a demonstrated
exploit, and every present call site is safe. We flag it because composite actions are reusable by
design. A future workflow, or a future edit to an existing one, could pass a less-trusted value into
`target`, `scenarios`, `artifact-name`, or `wait`. Examples are a branch name, a PR label, or a matrix
value derived from event data. Such a change could land without anyone noticing that the `run:` block
was never hardened against that value.

## Detailed design

The fix is mechanical and local to the two action files. It changes no product code and no `run`/CI
pass/fail logic.

1. **`bajutsu-e2e/action.yml`: route the two `run:` steps' inputs through `env:`.** For "Preflight" and
   "Run scenarios", add an `env:` block that maps each input to an uppercase environment variable
   (for example `TARGET: ${{ inputs.target }}`, `UDID: ${{ inputs.udid }}`,
   `BACKEND: ${{ inputs.backend }}`, `CONFIG: ${{ inputs.config }}`, `SCENARIOS: ${{ inputs.scenarios }}`).
   Inside the shell command, reference `"$TARGET"`, `"$UDID"`, and so on in place of the inline
   `${{ inputs.* }}` expansions. The `env:` assignment still substitutes the raw value, but as a shell
   *variable*. The shell then parses the value as data and never splices it into the script text. This
   is the standard mitigation for this class of finding.
2. **`bajutsu-e2e/action.yml`: route the "Upload run artifacts" step's `${{ inputs.artifact-name }}`
   the same way.** That input is a `with:` value on a `uses:` step (`actions/upload-artifact`), not a
   `run:` block, so shell injection does not apply and the code needs no change. A one-line note in the
   action is still worthwhile, so a future edit does not assume the same risk applies.
3. **`boot-simulator/action.yml`: route `${{ inputs.wait }}` through `env:`.** Add
   `env: WAIT: ${{ inputs.wait }}` to the "Boot" step, and change `if [ "${{ inputs.wait }}" = "true" ]`
   to `if [ "$WAIT" = "true" ]`.
4. **Add a regression check.** Extend `make lint-actions` (`actionlint`) coverage, or add a small
   repository-specific check (for example a `scripts/` lint or a `grep`-based test). The check fails
   when a composite action's `run:` block contains a raw `${{ inputs.` expansion, so the pattern cannot
   silently reappear in a future action or edit.

## Alternatives considered

- **Leave the inputs inline, since every current call site is a literal or a trusted output.**
  Rejected. The motivation for this proposal is that "safe today because nothing untrusted flows in
  yet" is no durable guarantee for a reusable composite action. The fix is cheap, and it removes the
  hazard permanently instead of relying on every future caller to pass only literals.
- **Restrict composite-action inputs with a regex or `pattern` validation step instead of `env:`
  indirection.** GitHub Actions has no native input-validation syntax. Validation would need a
  shell-based step before the risky one, which is more code than the `env:` fix. It would also merely
  reduce the risk, since the pattern would stay permissive but imperfect. `env:` indirection removes
  the injection vector itself.
- **Rewrite the affected steps to avoid shell interpolation entirely (for example by passing arguments
  through a wrapper script's argv).** This is more invasive than necessary. `env:` indirection is the
  standard, minimal fix for this exact pattern, and it keeps the diff small and reviewable.

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
