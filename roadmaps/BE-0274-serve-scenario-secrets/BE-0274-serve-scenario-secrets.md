**English** · [日本語](BE-0274-serve-scenario-secrets-ja.md)

# BE-0274 — Set scenario-declared secrets from the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0274](BE-0274-serve-scenario-secrets.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0274") |
| Topic | Security hardening |
| Related | [BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md), [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario declares the secrets it needs under a config's `secrets:` list — a list of environment
variable names `${secrets.X}` resolves at run time (BE-0032). Today the only way to supply those
values is the process environment the `bajutsu` CLI (or `serve`) happens to run in — there is no
`serve` Web UI affordance to see which names a bound config declares, or to set their values. This
proposal extends `serve`'s existing write-once `SecretStore` (BE-0136) to cover this case: a
**Secrets** panel lists every name the active config declares, each with a masked, write-once field,
so an operator can provision a scenario's secrets from the Web UI instead of exporting environment
variables (or hand-editing `.env`) before starting `serve`.

## Motivation

`bajutsu/config/schema.py:234`'s `Defaults.secrets` (overridable per target, merged in `resolve` at
`bajutsu/config/resolve.py:165`) is a list of environment-variable names. `_resolve_secrets`
(`bajutsu/cli/commands/run.py:231`) resolves each declared name from `os.environ` into a `secrets.X`
binding at the start of a run; the scenario file only ever holds the token, never the value
(BE-0032's Motivation). That design keeps the value out
of version control and evidence, but it says nothing about *how the value gets into the environment
in the first place* — and for `serve`, the answer today is "not through the tool at all":

- **Local `serve`** ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)) inherits
  whatever the shell that launched `make serve` exported. An operator who wants to run a scenario
  needing `${secrets.LOGIN_PASSWORD}` has to `export` it (or put it in a `.env` sourced before
  launch) — a step entirely outside the Web UI the rest of the workflow (Record / Replay / Crawl)
  happens in.
- **Self-hosting** ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) marks
  its Secrets row as "Shipped today: `.env`" — the same gap, called out as unmanaged.
- `serve` already solved exactly this shape of problem for three *operator* credentials — the Claude
  API key ([BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md)), the
  `claude-code` OAuth token ([BE-0215](../BE-0215-claude-code-oauth-token-credential/BE-0215-claude-code-oauth-token-credential.md)),
  and the Git config-source token ([BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md))
  — each settable (write-once, masked, never read back) through the Web UI
  (`bajutsu/serve/operations/config.py:41-56`). A *scenario's own* declared secrets are the one
  category of credential BE-0136's generalized store was built to cover (its Detailed design item 5:
  "Generalize beyond the one named secret") but never actually extended to.

Closing this makes `serve` self-sufficient for a scenario's full credential surface — no step
outside the tool, and the same non-disclosure guarantee (write-once, masked, never revealed) BE-0136
already established for operator credentials.

## Detailed design

No `run`/CI path is touched — this is entirely inside `serve`'s Tier‑1 operator-configuration
surface, same as BE-0136 and BE-0187.

**1. Resolve the declared names from the bound config.** Reuse `resolve()`
(`bajutsu/config/resolve.py:141`) against the active config to get each target's effective `secrets:` list, and
union them (a scenario can run against any target) into the set of names the panel should offer. No
config bound, or a config with an empty `secrets:`, yields an empty list — the panel then shows
nothing to configure, same as the settings panel today for a provider that isn't selected.

**2. A read operation + `GET /api/secrets`.** Returns `[{"name": str, "set": bool, "masked":
str | None}, ...]`, one entry per declared name, each `masked` sourced from
`state.for_org(org).secrets.describe(name)` — never a plaintext `value`, matching `api_key_info`'s
shape (`bajutsu/serve/operations/config.py:183`). Like `GET /api/apikey`, this is **not** role-gated
(`bajutsu/serve/authz.py:169`'s `required_role` already treats a describe-only response as safe for
any authenticated role) — it discloses which names exist and whether they're set, never a value.

**3. A write operation + `POST /api/secrets`.** Body `{"name": str, "value": str}`. Rejects any
`name` not currently in the resolved declared set (400) — this keeps the endpoint from becoming an
arbitrary-environment-variable-write primitive; only names the bound config itself asks for are
settable. Valid names are set/cleared through the existing `SecretStore.set` (an empty `value`
clears, same contract as the three existing named secrets). Gated to the `admin` role, added to
`bajutsu/serve/authz.py:122`'s `_ADMIN_PATHS` alongside `/api/apikey` / `/api/claudecodetoken` /
`/api/gitcredential` — setting a credential is exactly that tier of action.

**4. Extend the name→env-var mapping.** `ServeState._env_var_for_secret` (`bajutsu/serve/state.py:585`)
today maps only the three fixed logical names and falls through to `active_key_env(self)` for
anything else — silently wrong for a scenario secret name, which would overwrite the AI key's env var
instead of its own. Add an explicit case: when `name` is in the resolved declared set, the env var
*is* `name` itself (BE-0032's `secrets:` entries already are environment-variable names, unlike the
three fixed logical names which map to a differently-named var). Validate with the existing
`_valid_key_env_name` (`bajutsu/serve/operations/config.py:77`) guard against non-identifier or
unsafe system variable names (`_UNSAFE_ENV_VARS`) before ever calling `os.environ[name] = value`.

**5. UI: a "Scenario secrets" section.** Alongside the existing Claude API key / claude-code token /
Git credential blocks in `bajutsu/templates/serve.core.mjs`, a new section renders one masked,
write-once field per name `GET /api/secrets` returns (empty section, or hidden entirely, when the
list is empty) — same interaction shape as the existing three (a `renderX`/`applyX` pair per BE-0136
convention), generalized to a loop over the declared list instead of one hand-written block per
name. Re-fetches the declared list whenever the bound config changes (the same event the header's
config name already reacts to), so switching to a config with a different `secrets:` list refreshes
the panel.

**6. Storing works on both backends now; only hosted *consumption* is the follow-up.** The seam does
the backend split for free, so the two endpoints above land working on **both** deployments the
moment this item ships:

- **Local `serve`.** The `EnvSecretStore` writes into the local `serve` process's `os.environ`
  (item 4's name→env-var mapping), inherited by the spawned `record`/`run`/`crawl` subprocess through
  `jobs._spawn_env` (`bajutsu/serve/jobs.py:36`), so a value set through the UI is *both* stored and
  consumed with no further wiring — `${secrets.X}` resolves in the run.
- **Self-hosted (`server` backend).** `state.for_org(org).secrets` already returns the hosted
  `DbSecretStore` (`bajutsu/serve/server/secrets.py`), which stores any named secret encrypted at
  rest per org (Fernet, keyed by `BAJUTSU_SECRETS_KEY`) — BE-0136 §3, built to generalize to exactly
  this. So `GET`/`POST /api/secrets` **set and describe a scenario secret per org on a self-hosted
  deployment with no new storage code**: the same encrypted `secrets` table, the same write-once
  guarantee. What is *not* yet wired is **consumption**: the run executes on a remote worker
  (`bajutsu/serve/server/worker_job.py`), not in the control-plane process, so the stored value has
  to reach that worker's spawned `bajutsu run` for `${secrets.X}` to resolve there.

**7. The self-hosted consumption path (described; scoped as the one follow-up).** There is a direct
precedent to follow, so this is a well-specified follow-up rather than an open question. BE-0229
already resolves an org's AI-provider settings on the control plane at enqueue
(`dispatch.py:83`'s `resolve_provider_env`) into `job.env_overlay`, ships it in the job spec, and the
worker merges it onto the spawn env via `_spawn_env` (`jobs.py:49`) so the run uses that org's
selection without the worker holding any settings of its own. A scenario secret would ride the same
rail: at enqueue, the control plane resolves the org's declared secrets from `DbSecretStore` and
threads them to the worker, whose `_spawn_env` puts each under its declared env-var name so the
`bajutsu run` subprocess resolves `${secrets.X}`. The reason it is a *separate* item and not folded
in here is the security question the provider overlay does not raise: a scenario secret's plaintext
would then travel in the job spec over the queue, so the follow-up must decide between (a) a
dedicated secret overlay that `oplog`/redaction treat as sensitive and the queue payload never logs,
or (b) giving the worker the `BAJUTSU_SECRETS_KEY` so it fetches and decrypts the org's secret
itself and no plaintext ever rides the queue. That is a control-plane/worker trust-boundary decision
([BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md) territory) that
deserves its own review — exactly the boundary BE-0224 drew for its own hosted-injection follow-up.

**8. Tests.** Extend the serve HTTP harness: `GET /api/secrets` reflects the bound config's declared
names (and updates when the config changes) and never carries a `value`; `POST /api/secrets` rejects
a name absent from the declared set (400); a set-then-describe round trip for a valid name returns
only the masked preview; a spawned local run's subprocess environment carries the value under the
declared name after it is set through the endpoint. The hosted store already has BE-0136's
per-org-ciphertext contract test; extend it to assert a scenario-declared name round-trips through
`DbSecretStore` (set → describe returns the mask, the `ciphertext` column never holds the plaintext),
so the self-hosted *storage* path is covered even though consumption is deferred.

**9. Docs.** Update `docs/web-ui.md` / `docs/ja/web-ui.md` (the new Secrets panel) and
`docs/self-hosting.md` / `docs/ja/self-hosting.md`. The self-hosting Secrets row moves from "Shipped
today: `.env`" for scenario secrets to: settable per org from the Web UI and encrypted at rest today
(reusing `BAJUTSU_SECRETS_KEY`, already provisioned for the operator credentials), with the note that
a self-hosted run consuming a stored scenario secret on the remote worker is the tracked follow-up
(item 7).

## Alternatives considered

- **A generic secret manager where the operator types arbitrary names, not tied to a config's
  `secrets:` declaration.** Closer to a GitHub-Actions-style free-form secrets store. Rejected for
  this item: it decouples the panel from what a scenario actually needs, reintroduces an
  arbitrary-environment-variable-write surface, and gives no feedback when a declared name is
  misspelled or a stale secret is never cleaned up. Tying the panel to the resolved `secrets:` list
  keeps declaration and provisioning in sync; a free-form store can always be layered in later behind
  the same `SecretStore` seam if a real need for undeclared ad-hoc secrets shows up.
- **Wire the self-hosted worker's secret *consumption* into this same item.** Rejected as out of
  scope, mirroring BE-0224's own deferral — but note the boundary is narrower than it looks:
  *storing* a scenario secret per org already works self-hosted through `DbSecretStore` (Detailed
  design 6), so only the "inject the stored value into the remote worker's spawned run" leg is
  deferred. Bundling it here would fold a control-plane/worker trust-boundary decision — does the
  decrypted secret travel in the job spec, or does the worker hold `BAJUTSU_SECRETS_KEY` and decrypt
  it itself? (Detailed design 7) — into a Web UI affordance, and that decision
  ([BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md) territory)
  deserves its own review.
- **Let `POST /api/secrets` set any environment-variable name, gated to `admin` only.** Rejected: an
  `admin` role in `serve` is still a Web UI operator, not a shell — an unbounded write primitive into
  `os.environ` (even admin-gated) is a materially larger blast radius than "only names this config
  already declared it needs," and buys no functionality a scenario actually uses.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Resolve the bound config's declared `secrets:` names (union across targets)
- [ ] `GET /api/secrets` (read operation), no role gate, never a plaintext value
- [ ] `POST /api/secrets` (write operation), admin-gated, rejects an undeclared name
- [ ] `ServeState._env_var_for_secret` extended for declared scenario-secret names
- [ ] UI: Scenario secrets section in `serve.core.mjs`, refreshes on config change
- [ ] Both backends store per-name: local `EnvSecretStore` and (self-hosted) `DbSecretStore` — the
      seam split is inherited, so this is a test/verify item, not new storage code
- [ ] Tests: declared-name reflection, undeclared-name rejection, masked-only round trip, spawned
      local-run env inheritance, hosted `DbSecretStore` per-org round trip (ciphertext never plaintext)
- [ ] Docs updated (`docs/web-ui.md`, `docs/self-hosting.md`, both languages)

**Out of scope (follow-up).** The self-hosted *consumption* leg only: injecting a stored per-org
scenario secret into the remote worker's spawned `bajutsu run` so `${secrets.X}` resolves there.
Storing the secret per org already works self-hosted (the hosted `DbSecretStore` encrypts it at
rest), so what remains is the control-plane-to-worker injection and its trust-boundary decision
(Detailed design 7), tracked as a follow-up alongside BE-0224's equivalent gap for the Git
config-source token.

## References

`bajutsu/config/schema.py:234` (`Defaults.secrets`) and `bajutsu/config/resolve.py:165` (merge in
`resolve`), `bajutsu/cli/commands/run.py:231` (`_resolve_secrets`),
`bajutsu/serve/state.py:585` (`_env_var_for_secret`), `bajutsu/serve/operations/config.py:41-56`
(the three existing named secrets) and `:183` (`api_key_info`), `bajutsu/serve/authz.py:122`
(`_ADMIN_PATHS`) and `:169` (`required_role`), `bajutsu/serve/secrets.py` (the local `SecretStore`
seam) and `bajutsu/serve/server/secrets.py` (the hosted `DbSecretStore`, per-org Fernet),
`bajutsu/serve/jobs.py:36` (`_spawn_env`) and `bajutsu/serve/server/worker_job.py` /
`bajutsu/serve/operations/dispatch.py:83` (`env_overlay`, BE-0229's enqueue-time overlay the
self-hosted consumption path would follow), `bajutsu/templates/serve.core.mjs` (the existing masked
write-once UI blocks). Related:
[BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md) (how a scenario consumes
`${secrets.X}`), [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md)
(the write-once `SecretStore` seam this extends), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
(names the "Shipped today: `.env`" gap this closes), [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md)
(the precedent for deferring hosted per-org injection to a follow-up).
