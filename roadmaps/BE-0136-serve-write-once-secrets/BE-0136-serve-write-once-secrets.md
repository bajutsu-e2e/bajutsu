**English** · [日本語](BE-0136-serve-write-once-secrets-ja.md)

# BE-0136 — Write-once secrets store for serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0136](BE-0136-serve-write-once-secrets.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0136") |
| Implementing PR | [#661](https://github.com/bajutsu-e2e/bajutsu/pull/661) |
| Topic | Security hardening |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md) |
<!-- /BE-METADATA -->

## Introduction

`serve` lets an admin set operator-level credentials through the web UI. Today the only one is the
Claude API key. Unlike a GitHub Actions secret, the value can currently be read back in plaintext by any
authenticated caller once it is set.

This proposal makes operator secrets in `serve` **write-once**. An admin can set or replace a secret.
No endpoint, for any role, ever returns the plaintext again; every response carries a masked preview instead. On
the hosted `server` backend, the value is also encrypted at rest instead of living in process memory
alone.

## Motivation

`api_key_info` (`bajutsu/serve/operations.py:237`) and `set_api_key` (`bajutsu/serve/operations.py:598`)
back the "Claude API key" field in `serve`'s settings panel. The two sides of that field are asymmetric
in a way a secrets manager should never be:

- **Setting** the key (`POST /api/apikey`) requires the `admin` role, because `/api/apikey` is one of
  `_ADMIN_PATHS` (`authz.py:122`). The gate applies only to an OAuth session with a database-backed
  `Repository` wired (`forbidden_for_role`, `authz.py:164`). A Bearer-token request has no identity and
  stays full-access by design, since it is the operator credential. With no database wired at all
  (local `serve`), the role gate is a no-op.
- **Reading** the key back in plaintext (`GET /api/apikey?reveal=1`) requires *no role at all*,
  however the caller authenticated. `required_role` (`authz.py:147`) returns `None` for every `GET`. An
  OAuth session with the read-only `viewer` role (BE-0015 7c-2) can therefore retrieve the live
  credential that a higher-privileged admin configured. The mutating side of the same endpoint would
  refuse that session.
- **Storage** is `os.environ[var] = value` (`set_api_key`). The value lives in the `serve` process's
  memory alone. It does not survive a restart, it is not shared across the control-plane replicas that
  BE-0015 §4 ("Control-plane scale-out") plans, and it cannot be scoped per org.

GitHub Actions Secrets get this right. An org owner can set or overwrite a secret, but no UI or API
call, including the owner's own, ever discloses the value again. An operator credential in `serve` needs
that write-once, non-retrievable shape. The roadmap already names the gap without solving it:

- BE-0015's Secrets row calls for **"per-org BYO `ANTHROPIC_API_KEY`"** as security hardening before
  public exposure. Its multi-tenancy section admits: *"secrets are a single server-level API key today,
  not per-org injected secrets."*
- BE-0016's self-hosting menu marks **Secrets** as "Shipped today: `.env`". That is unmanaged, with no
  actual secrets layer behind the compose stack.

Both point at the same missing piece: a real secrets seam. The current design is a single mutable
environment variable that the whole process shares and any viewer can echo back.

## Detailed design

**1. A `SecretStore` seam, following the existing pattern.** `bajutsu/serve/` already swaps in five
seams behind `ServeState`: `RunExecutor`, `LogBus`, `ArtifactStore`, `ScenarioStore`, and `Repository`.
Each is a `Protocol` with a local implementation and a hosted one, selected by `_build_server_state`.
Add a sixth, `SecretStore`, with two operations: `set(name, value)` and `describe(name)`. `describe`
returns a masked preview or `None`. The interface that an HTTP handler can reach deliberately has
**no `get(name) -> value`**. The plaintext exists inside the code path that consumes it (spawning a
`record`, `run`, or `crawl` subprocess) and never on a response path.

**2. Local implementation: today's behavior, same seam.** The local `serve` keeps holding the value in
`os.environ` for the process's lifetime, in memory alone, as documented today. The single-user local
case sees no behavior change. The value merely moves behind the seam, so `api_key_info` and
`set_api_key` (and any future named secret) go through one interface.

**3. Hosted implementation: encrypted at rest, per org.** On the `server` backend
(`bajutsu/serve/server/db.py`), add a `secrets` table with the columns `org_id`, `name`, `ciphertext`,
`updated_at`, and `updated_by`. The table is scoped by `org_id` exactly like the existing `projects` and
`runs` tables, the same column that lets BE-0015 §8's per-org storage resolve today.

Values are encrypted with authenticated encryption: the `cryptography` package's `Fernet`, added as a
new dependency behind the existing `db` extra. The key is an operator-provided master key,
`BAJUTSU_SECRETS_KEY`. It is analogous to `BAJUTSU_DATABASE_URL`: a deployment secret provisioned
outside the database, for example through the platform's own secret store. This directly resolves
BE-0015's "per-org BYO API key" gap. The table and seam that hold today's single Claude key generalize
to a `name`-scoped secret per org.

**4. Reveal is gone, not gated.** `GET /api/apikey` drops the `reveal` parameter entirely. It always
returns `{"set": bool, "masked": "sk-...ab12"}`, and never a `value` field, for any role, admin
included. An admin who needs to rotate a key overwrites it with `POST /api/apikey`. The admin never needs
to read the old key back, which matches how GitHub Actions Secrets works.

The removal is a breaking change to the existing `reveal` query parameter. The "showing it redacted with
a reveal toggle" line in `docs/getting-started.md` and the settings-panel UI (`serve.js`) drop the reveal
control.

**5. Generalize beyond the one named secret.** `api_key_info` and `set_api_key` become thin wrappers over
`SecretStore.describe("aiApiKey")` and `SecretStore.set("aiApiKey", value)`. A second named secret (for
example, a future Bedrock AWS credential, or a target's own API credential) reuses the same store and the
same write-once guarantee with no new plumbing.

**6. Tests.** Extend the serve HTTP harness. It needs no Simulator and no live Postgres, because the
hosted `SecretStore` test double runs the same contract as SQLite does for `Repository`. The tests check
four properties:

- `POST /api/apikey` requires admin (unchanged).
- `GET /api/apikey` never carries a `value` key, whatever the role or query parameters.
- A set-then-describe round trip returns the masked preview alone.
- For the hosted implementation, the stored `ciphertext` column never contains the plaintext substring.

**7. Docs.** Update `docs/getting-started.md` (drop the reveal-toggle description) and
`docs/self-hosting.md`, in both languages. In `docs/self-hosting.md`, the Secrets row moves from
"Shipped today: `.env`" to the new encrypted store, and notes `BAJUTSU_SECRETS_KEY` provisioning.

No change touches the deterministic `run`/CI gate, the scenario schema, the runner, or the drivers. The
work sits entirely inside `serve`'s operator-configuration surface, consistent with the prime directive
that nothing here can affect pass/fail.

## Alternatives considered

- **Gate `reveal` behind the `admin` role instead of removing it.** Rejected as the weaker fix. It closes
  the viewer-can-read gap but keeps a plaintext round trip that an admin, or anyone who compromises an
  admin session, can still exercise. GitHub Actions Secrets disclose to *no one* after they are set,
  including the account that set them. Matching that bar is what makes this a secrets manager rather
  than a slightly better guarded config field.
- **A cloud secret manager (Doppler, AWS Secrets Manager, or Vault), as BE-0015's Secrets row originally
  suggested.** Not rejected outright. It remains the right choice for a large, multi-cloud deployment.
  But it adds an external dependency and account before any secret can be set. That is a poor fit for
  the self-hosted single-node case ([`deploy/self-host/`](../../deploy/self-host/)) that BE-0016 already
  ships. An in-database encrypted store needs a master key that the operator already manages alongside
  `BAJUTSU_DATABASE_URL`. It also does not preclude swapping in Doppler or Vault later behind the same
  `SecretStore` seam.
- **Leave the value in `os.environ` for the hosted backend too (today's shape, merely gated by RBAC).**
  Rejected. The value does not survive a restart, cannot be shared across the control-plane replicas
  that BE-0015 §4 plans, and has no per-org scoping. The encrypted per-org table closes all three gaps
  at once.
- **Encrypt with a key derived from the session token or a per-request secret instead of a standalone
  master key.** Rejected. A secret would become unreadable the moment the token rotates or a session
  ends. That breaks the "set once, keep working" requirement that a spawned `record` or `run` job
  depends on.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `SecretStore` Protocol (`set` / `describe`, no `get` reachable from an HTTP handler)
- [x] Local implementation — today's `os.environ` behavior, moved behind the seam
- [x] Hosted implementation — `secrets` table (`org_id`, `name`, `ciphertext`), `Fernet` encryption
      keyed by `BAJUTSU_SECRETS_KEY`, added behind the `db` extra
- [x] `GET /api/apikey` drops `reveal`; never returns a plaintext `value`, for any role
- [x] `api_key_info` / `set_api_key` generalized to `SecretStore.describe("aiApiKey")` /
      `.set("aiApiKey", value)`
- [x] Tests: role/reveal removal, set-then-describe round trip, hosted ciphertext never holds
      plaintext
- [x] Docs updated (`docs/getting-started.md`, `docs/self-hosting.md`), both languages

**Out of scope (follow-up).** Wiring the hosted worker to *consume* a stored secret is not part of this
item and remains as-is. A worker runs in its own process and reads the credential from its own
deployment environment. This item stores the value set through the UI write-once and encrypted per org.
Per-org injection into a worker's spawned job belongs to BE-0015's per-org key work.

### Log

- The implementing PR delivers the whole MECE breakdown in one change, shipped in
  [#661](https://github.com/bajutsu-e2e/bajutsu/pull/661). It contains:
  - the `SecretStore` seam (`bajutsu/serve/secrets.py`) with the local `EnvSecretStore`
  - the hosted `DbSecretStore` (`bajutsu/serve/server/secrets.py`) with the `secrets` table (migration
    `0006`) and `Fernet` encryption
  - the `reveal` removal across `operations`, both HTTP shells, and the web UI
  - the bilingual docs

## References

`bajutsu/serve/operations.py:237` (`api_key_info`), `bajutsu/serve/operations.py:598`
(`set_api_key`), `bajutsu/serve/authz.py:122` (`_ADMIN_PATHS`) and `:147` (`required_role`),
`bajutsu/serve/server/db.py` (the `Repository` seam this proposal extends). Related:
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (names
the "per-org BYO API key" gap this closes), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
(marks self-hosted Secrets as unmanaged `.env` today), [BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md)
(a different layer — how a *scenario* consumes a secret at run time, not how `serve` stores an
operator credential).
