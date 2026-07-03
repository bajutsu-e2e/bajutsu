**English** · [日本語](BE-0136-serve-write-once-secrets-ja.md)

# BE-0136 — Write-once secrets store for serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0136](BE-0136-serve-write-once-secrets.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0136") |
| Topic | Security hardening |
| Related | [BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0032](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables.md) |
<!-- /BE-METADATA -->

## Introduction

`serve` lets an admin set operator-level credentials — today, the Claude API key — through the web
UI. Unlike a GitHub Actions secret, once set that value can currently be read back in plaintext by
any authenticated caller. This proposal makes operator secrets in `serve` **write-once**: an admin
can set or replace a secret, but no endpoint — for any role — ever returns its plaintext again, only
a masked preview. On the hosted `server` backend the value is also encrypted at rest instead of
living only in process memory.

## Motivation

`bajutsu/serve/operations.py:237`'s `api_key_info` and `bajutsu/serve/operations.py:598`'s
`set_api_key` back the "Claude API key" field in `serve`'s settings panel. The two sides of that
field are asymmetric in a way a secrets manager should never be:

- **Setting** the key (`POST /api/apikey`) requires the `admin` role — `/api/apikey` is one of
  `authz.py:122`'s `_ADMIN_PATHS` — but only for an OAuth session with a database-backed
  `Repository` wired (`authz.py:164`'s `forbidden_for_role`); a Bearer-token request has no
  identity and stays full-access by design (it is the operator credential), and with no database
  wired at all (local `serve`) the role gate is a no-op.
- **Reading** the key back in plaintext (`GET /api/apikey?reveal=1`) requires *no role at all*,
  regardless of how the caller authenticated — `authz.py:147`'s `required_role` returns `None` for
  every `GET`, so an OAuth session with the read-only `viewer` role (BE-0015 7c-2) can retrieve the
  live credential a higher-privileged admin configured, something the mutating side of the same
  endpoint would refuse.
- **Storage** is `os.environ[var] = value` (`set_api_key`) — held only in the `serve` process's
  memory, so it does not survive a restart, is not shared across the control-plane replicas BE-0015
  §4 ("Control-plane scale-out") plans, and cannot be scoped per org.

GitHub Actions Secrets get this right: an org owner can set or overwrite a secret, but no UI or API
call — including the owner's own — ever discloses the value again. That write-once, non-retrievable
shape is exactly what an operator credential in `serve` needs, and it is a gap the roadmap already
names without solving:

- BE-0015's Secrets row calls for **"per-org BYO `ANTHROPIC_API_KEY`"** as security hardening before
  public exposure, and its multi-tenancy section admits: *"secrets are a single server-level API key
  today, not per-org injected secrets."*
- BE-0016's self-hosting menu marks **Secrets** as "Shipped today: `.env`" — i.e. unmanaged, with no
  actual secrets layer behind the compose stack.

Both point at the same missing piece: a real secrets seam, not a single mutable environment variable
the whole process shares and any viewer can echo back.

## Detailed design

**1. A `SecretStore` seam, following the existing pattern.** `bajutsu/serve/` already swaps in five
seams behind `ServeState` (`RunExecutor`, `LogBus`, `ArtifactStore`, `ScenarioStore`, `Repository`) —
a `Protocol` with a local implementation and a hosted one, selected by `_build_server_state`. Add a
sixth: `SecretStore`, with two operations only — `set(name, value)` and `describe(name)`, returning a
masked preview or `None`. There is deliberately **no `get(name) -> value`** in the interface an HTTP
handler can reach; the plaintext exists only inside the code path that consumes it (spawning a
`record`/`run`/`crawl` subprocess), never on a response path.

**2. Local implementation — today's behavior, same seam.** The local `serve` keeps holding the value
in `os.environ` for the process's lifetime (in memory only, as documented today) — no behavior change
for the single-user local case, just moved behind the seam so `api_key_info`/`set_api_key` (and any
future named secret) go through one interface.

**3. Hosted implementation — encrypted at rest, per org.** On the `server` backend
(`bajutsu/serve/server/db.py`), add a `secrets` table (`org_id`, `name`, `ciphertext`, `updated_at`,
`updated_by`), scoped by `org_id` exactly like the existing `projects`/`runs` tables — the same column
that lets BE-0015 §8's per-org storage resolve today. Values are encrypted with authenticated
encryption (the `cryptography` package's `Fernet`, added as a new dependency behind the existing
`db` extra) keyed by
an operator-provided master key (`BAJUTSU_SECRETS_KEY`, analogous to `BAJUTSU_DATABASE_URL` — a
deployment secret provisioned outside the database, e.g. via the platform's own secret store). This
directly resolves BE-0015's "per-org BYO API key" gap: the same table and seam that hold today's
single Claude key generalize to a `name`-scoped secret per org.

**4. Reveal is gone, not gated.** `GET /api/apikey` drops the `reveal` parameter entirely and always
returns `{"set": bool, "masked": "sk-...ab12"}` — never a `value` field, for any role, admin included.
An admin who needs to rotate a key overwrites it with `POST /api/apikey`; they never need to read the
old one back, matching how GitHub Actions Secrets works. This is a breaking change to the existing
`reveal` query param — `docs/getting-started.md`'s "showing it redacted with a reveal toggle" line and
the settings-panel UI (`serve.js`) drop the reveal control.

**5. Generalize beyond the one named secret.** `api_key_info`/`set_api_key` become thin wrappers over
`SecretStore.describe("aiApiKey")` / `SecretStore.set("aiApiKey", value)`, so a second named secret
(e.g. a future Bedrock AWS credential, or a target's own API credential) reuses the same store and the
same write-once guarantee with no new plumbing.

**6. Tests.** Extend the serve HTTP harness (no Simulator, no live Postgres needed — the hosted
`SecretStore` test double runs the same contract as SQLite does for `Repository`): `POST /api/apikey`
requires admin (unchanged), `GET /api/apikey` never carries a `value` key regardless of role or query
params, a set-then-describe round trip returns only the masked preview, and — for the hosted
implementation — the stored `ciphertext` column never contains the plaintext substring.

**7. Docs.** Update `docs/getting-started.md` (drop the reveal-toggle description) and
`docs/self-hosting.md` (the Secrets row moves from "Shipped today: `.env`" to the new encrypted store,
noting `BAJUTSU_SECRETS_KEY` provisioning), both languages.

No change touches the deterministic `run`/CI gate, the scenario schema, the runner, or the drivers —
this is entirely inside `serve`'s operator-configuration surface, consistent with the prime directive
that nothing here can affect pass/fail.

## Alternatives considered

- **Gate `reveal` behind the `admin` role instead of removing it.** Rejected as the weaker fix: it
  closes the viewer-can-read gap but keeps a plaintext round trip an admin (or anyone who compromises
  an admin session) can still exercise. GitHub Actions Secrets disclose to *no one* after they're set,
  including the account that set them — matching that bar is what makes this a secrets manager rather
  than a slightly-better-guarded config field.
- **A cloud secret manager (Doppler / AWS Secrets Manager / Vault), as BE-0015's Secrets row
  originally suggested.** Not rejected outright — it remains the right choice for a large, multi-cloud
  deployment — but it adds an external dependency and account before any secret can be set, which is a
  poor fit for the self-hosted single-node case ([`deploy/self-host/`](../../../deploy/self-host/))
  BE-0016 already ships. An in-database encrypted store needs only a master key the operator already
  manages alongside `BAJUTSU_DATABASE_URL`, and does not preclude swapping in Doppler/Vault later
  behind the same `SecretStore` seam.
- **Leave the value in `os.environ` for the hosted backend too (today's shape, just RBAC-gated).**
  Rejected: it does not survive a restart, cannot be shared across the control-plane replicas BE-0015
  §4 plans, and offers no per-org scoping — three separate gaps the encrypted per-org table closes at
  once.
- **Encrypt with a key derived from the session token or a per-request secret instead of a standalone
  master key.** Rejected: it would make a secret unreadable the moment the token rotates or a session
  ends, which breaks the "set once, keep working" requirement a spawned `record`/`run` job depends on.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `SecretStore` Protocol (`set` / `describe`, no `get` reachable from an HTTP handler)
- [ ] Local implementation — today's `os.environ` behavior, moved behind the seam
- [ ] Hosted implementation — `secrets` table (`org_id`, `name`, `ciphertext`), `Fernet` encryption
      keyed by `BAJUTSU_SECRETS_KEY`, added behind the `db` extra
- [ ] `GET /api/apikey` drops `reveal`; never returns a plaintext `value`, for any role
- [ ] `api_key_info` / `set_api_key` generalized to `SecretStore.describe("aiApiKey")` /
      `.set("aiApiKey", value)`
- [ ] Tests: role/reveal removal, set-then-describe round trip, hosted ciphertext never holds
      plaintext
- [ ] Docs updated (`docs/getting-started.md`, `docs/self-hosting.md`), both languages

No PR has landed yet.

## References

`bajutsu/serve/operations.py:237` (`api_key_info`), `bajutsu/serve/operations.py:598`
(`set_api_key`), `bajutsu/serve/authz.py:122` (`_ADMIN_PATHS`) and `:147` (`required_role`),
`bajutsu/serve/server/db.py` (the `Repository` seam this proposal extends). Related:
[BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (names
the "per-org BYO API key" gap this closes), [BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
(marks self-hosted Secrets as unmanaged `.env` today), [BE-0032](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables.md)
(a different layer — how a *scenario* consumes a secret at run time, not how `serve` stores an
operator credential).
