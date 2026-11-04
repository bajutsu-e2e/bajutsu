**English** · [日本語](BE-0274-serve-scenario-secrets-ja.md)

# BE-0274 — Set scenario-declared secrets from the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0274](BE-0274-serve-scenario-secrets.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0274") |
| Implementing PR | [#1144](https://github.com/bajutsu-e2e/bajutsu/pull/1144) |
| Topic | Security hardening |
| Related | [BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md), [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario declares the secrets it needs under a config's `secrets:` list. The list holds environment
variable names. `${secrets.X}` resolves them at run time (BE-0032). Today the only source of those
values is the process environment of the `bajutsu` CLI (or `serve`). The `serve` Web UI cannot show
which names a bound config declares. It cannot set their values either.

This proposal extends the write-once `SecretStore` of `serve` (BE-0136) to this case. A **Secrets**
panel lists every name that the active config declares. Each name gets a masked, write-once field.
An operator provisions a scenario's secrets from the Web UI. No environment variable has to be
exported, and no `.env` edited by hand, before `serve` starts.

## Motivation

`Defaults.secrets` (`bajutsu/config/schema.py:234`) is a list of environment-variable names. A target can override it. `resolve` merges the two at `bajutsu/config/resolve.py:165`.

At the start of a run, `_resolve_secrets` (`bajutsu/cli/commands/run.py:231`) resolves each declared name from `os.environ`. The result is a `secrets.X` binding. The scenario file holds only the token, never the value (BE-0032's Motivation).

This design keeps the value out of version control and evidence. It does not say how the value reaches the environment. For `serve`, the answer today is "not through the tool at all":

- **Local `serve`** ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)) inherits what the launching shell exported. Take a scenario that needs `${secrets.LOGIN_PASSWORD}`. The operator must `export` the value, or source a `.env` before launch. That step sits outside the Web UI, where Record, Replay, and Crawl happen.
- **Self-hosting** ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) marks its Secrets row as "Shipped today: `.env`". The row calls the same gap unmanaged.
- **Operator credentials.** `serve` already solved this problem for three *operator* credentials. They are the Claude API key ([BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md)), the `claude-code` OAuth token ([BE-0215](../BE-0215-claude-code-oauth-token-credential/BE-0215-claude-code-oauth-token-credential.md)), and the Git config-source token ([BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md)). The Web UI sets each one write-once. Each is masked and never read back (`bajutsu/serve/operations/config.py:41-56`).
- **Scenario credentials.** A *scenario's own* declared secrets are the one category left. BE-0136's generalized store was built to cover them. Item 5 of its Detailed design says "Generalize beyond the one named secret". The store was never extended.

Closing the gap makes `serve` self-sufficient for a scenario's full credential surface. No step remains outside the tool. The non-disclosure guarantee of BE-0136 (write-once, masked, never revealed) then covers scenario secrets too.

## Detailed design

No `run`/CI path is touched. The work sits inside `serve`'s Tier-1 operator-configuration surface, like BE-0136 and BE-0187.

**1. Resolve the declared names from the bound config.** Call `resolve()` (`bajutsu/config/resolve.py:141`) against the active config. It yields each target's effective `secrets:` list. A scenario can run against any target, so the panel takes the union of the lists. With no bound config, or an empty `secrets:`, the list is empty. The panel then shows nothing to configure. The settings panel behaves the same way today for an unselected provider.

**2. A read operation and `GET /api/secrets`.** The endpoint returns `[{"name": str, "set": bool, "masked": str | None}, ...]`. It returns one entry per declared name. Each `masked` value comes from `state.for_org(org).secrets.describe(name)`. The response never carries a plaintext `value`. The shape matches `api_key_info` (`bajutsu/serve/operations/config.py:183`).

Like `GET /api/apikey`, this endpoint is **not** role-gated. `required_role` in `bajutsu/serve/authz.py:169` already treats a describe-only response as safe for any authenticated role. The endpoint discloses which names exist and whether each is set. It never discloses a value.

**3. A write operation and `POST /api/secrets`.** The body is `{"name": str, "value": str}`.

- A `name` outside the resolved declared set gets a 400. The endpoint therefore cannot write arbitrary environment variables. Only names that the bound config asks for are settable.
- A valid name is set or cleared through the existing `SecretStore.set`. An empty `value` clears, as for the three existing named secrets.
- The endpoint is gated to the `admin` role. It joins `_ADMIN_PATHS` (`bajutsu/serve/authz.py:122`) next to `/api/apikey`, `/api/claudecodetoken`, and `/api/gitcredential`. Setting a credential belongs to that tier of action.

**4. Extend the name-to-env-var mapping.** `ServeState._env_var_for_secret` (`bajutsu/serve/state.py:585`) maps only the three fixed logical names today. Any other name falls through to `active_key_env(self)`. For a scenario secret name, that is silently wrong. The value would overwrite the AI key's env var instead of its own.

The fix adds an explicit case. When `name` is in the resolved declared set, the env var *is* `name`. BE-0032's `secrets:` entries already are environment-variable names. The three fixed logical names, in contrast, map to differently named variables. The existing `_valid_key_env_name` guard (`bajutsu/serve/operations/config.py:77`) runs before `os.environ[name] = value`. It rejects non-identifier names and unsafe system variables (`_UNSAFE_ENV_VARS`).

**5. UI: a "Scenario secrets" section.** The section sits next to the Claude API key, `claude-code` token, and Git credential blocks in `bajutsu/templates/serve.core.mjs`. It renders one masked, write-once field per name that `GET /api/secrets` returns. An empty list leaves the section empty or hidden.

The interaction matches the existing three blocks: a `renderX` / `applyX` pair, per the BE-0136 convention. The section loops over the declared list. It does not hand-write one block per name. It re-fetches the list whenever the bound config changes, the same event that the header's config name already follows. Switching to a config with a different `secrets:` list refreshes the panel.

**6. Storing works on both backends now; only hosted *consumption* is the follow-up.** The seam splits the backends at no extra cost. Both endpoints work on **both** deployments the moment this item ships:

- **Local `serve`.** The `EnvSecretStore` writes into the `os.environ` of the local `serve` process, through the mapping of item 4. The spawned `record`, `run`, or `crawl` subprocess inherits the value through `jobs._spawn_env` (`bajutsu/serve/jobs.py:36`). A value set through the UI is stored and consumed with no further wiring. `${secrets.X}` resolves in the run.
- **Self-hosted (`server` backend).** `state.for_org(org).secrets` already returns the hosted `DbSecretStore` (`bajutsu/serve/server/secrets.py`). It encrypts any named secret at rest per org (Fernet, keyed by `BAJUTSU_SECRETS_KEY`). BE-0136 §3 built it to generalize to this case. `GET` and `POST /api/secrets` therefore **set and describe a scenario secret per org with no new storage code**. The encrypted `secrets` table and the write-once guarantee are the same.

**Consumption** is not yet wired. A self-hosted run executes on a remote worker (`bajutsu/serve/server/worker_job.py`), not in the control-plane process. The stored value has to reach that worker's spawned `bajutsu run`. Only then can `${secrets.X}` resolve there.

**7. The self-hosted consumption path (described, and scoped as the one follow-up).** A direct precedent exists, so the follow-up is well specified and not an open question. BE-0229 resolves an org's AI-provider settings on the control plane at enqueue (`resolve_provider_env`, `dispatch.py:83`). The result becomes `job.env_overlay` in the job spec. The worker merges it onto the spawn env through `_spawn_env` (`jobs.py:49`). The run uses that org's selection, and the worker holds no settings of its own.

A scenario secret would ride the same rail. At enqueue, the control plane resolves the org's declared secrets from `DbSecretStore`. It threads them to the worker. The worker's `_spawn_env` sets each one under its declared env-var name. The `bajutsu run` subprocess then resolves `${secrets.X}`.

This item does not fold the follow-up in, because of a security question that the provider overlay does not raise. A scenario secret's plaintext would travel in the job spec over the queue. The follow-up must choose between two designs:

- (a) A dedicated secret overlay that `oplog` and redaction treat as sensitive, and that the queue payload never logs.
- (b) Giving the worker the `BAJUTSU_SECRETS_KEY`. The worker then fetches and decrypts the org's secret itself, and no plaintext ever rides the queue.

The choice is a control-plane/worker trust-boundary decision ([BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md) territory). It deserves its own review. BE-0224 drew the same boundary for its own hosted-injection follow-up.

**8. Tests.** The serve HTTP harness gains four assertions:

- `GET /api/secrets` reflects the bound config's declared names and updates when the config changes. It never carries a `value`.
- `POST /api/secrets` rejects a name absent from the declared set with a 400.
- A set-then-describe round trip for a valid name returns only the masked preview.
- A spawned local run's subprocess environment carries the value under the declared name after the endpoint sets it.

The hosted store already has BE-0136's per-org-ciphertext contract test. The test is extended to cover a scenario-declared name. Set, then describe, returns the mask. The `ciphertext` column never holds the plaintext. The self-hosted *storage* path is then covered, although consumption is deferred.

**9. Docs.** `docs/web-ui.md` and `docs/ja/web-ui.md` describe the new Secrets panel. `docs/self-hosting.md` and `docs/ja/self-hosting.md` change too. For scenario secrets, the self-hosting Secrets row moves from "Shipped today: `.env`" to the following statement. A scenario secret is settable per org from the Web UI. It is encrypted at rest today, reusing `BAJUTSU_SECRETS_KEY`, which is already provisioned for the operator credentials. A note adds that consumption of a stored scenario secret by a self-hosted run on the remote worker is the tracked follow-up (item 7).

## Alternatives considered

- **A generic secret manager where the operator types arbitrary names, not tied to a config's `secrets:` declaration.** This is closer to a GitHub-Actions-style free-form secrets store. Rejected for this item, for three reasons. It decouples the panel from what a scenario actually needs. It reintroduces an arbitrary-environment-variable-write surface. It gives no feedback when a declared name is misspelled or a stale secret is never cleaned up. Tying the panel to the resolved `secrets:` list keeps declaration and provisioning in sync. A free-form store can always be layered in later, behind the same `SecretStore` seam, if a real need for undeclared ad-hoc secrets shows up.
- **Wire the self-hosted worker's secret *consumption* into this same item.** Rejected as out of scope, mirroring BE-0224's own deferral. The boundary is narrower than it looks. *Storing* a scenario secret per org already works self-hosted through `DbSecretStore` (Detailed design 6), so only one leg is deferred: injecting the stored value into the remote worker's spawned run.

  Bundling that leg here would fold a control-plane/worker trust-boundary decision into a Web UI feature. The decision is whether the decrypted secret travels in the job spec, or whether the worker holds `BAJUTSU_SECRETS_KEY` and decrypts it itself (Detailed design 7). That decision ([BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md) territory) deserves its own review.
- **Let `POST /api/secrets` set any environment-variable name, gated to `admin` only.** Rejected. An `admin` role in `serve` is still a Web UI operator, not a shell. An unbounded write primitive into `os.environ`, even when gated to admin, has a materially larger blast radius than "only names this config already declared it needs". It also buys no functionality that a scenario actually uses.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Resolve the bound config's declared `secrets:` names (union across targets)
- [x] `GET /api/secrets` (read operation), no role gate, never a plaintext value
- [x] `POST /api/secrets` (write operation), admin-gated, rejects an undeclared name
- [x] `ServeState._env_var_for_secret` extended for declared scenario-secret names
- [x] UI: Scenario secrets section in `serve.core.mjs`, refreshes on config change
- [x] Both backends store per-name: local `EnvSecretStore` and (self-hosted) `DbSecretStore` — the
      seam split is inherited, so this is a test/verify item, not new storage code
- [x] Tests: declared-name reflection, undeclared-name rejection, masked-only round trip, spawned
      local-run env inheritance, hosted `DbSecretStore` per-org round trip (ciphertext never plaintext)
- [x] Docs updated (`docs/web-ui.md`, `docs/self-hosting.md`, both languages)

### Log

- Implemented all checklist items in one change: `GET`/`POST /api/secrets` + `declared_secret_names`
  in `operations/config.py`, the `_env_var_for_secret` identity mapping for declared names in
  `state.py`, the shared route + `_ADMIN_PATHS` gating, the Settings "Scenario secrets" UI, and the
  bilingual docs. Storing verified on both backends; self-hosted consumption stays the follow-up
  (Detailed design 7). PR: [#1144](https://github.com/bajutsu-e2e/bajutsu/pull/1144).

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
