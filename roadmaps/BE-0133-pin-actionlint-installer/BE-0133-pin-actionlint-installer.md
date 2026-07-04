**English** · [日本語](BE-0133-pin-actionlint-installer-ja.md)

# BE-0133 — Pin the actionlint installer by SHA

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0133](BE-0133-pin-actionlint-installer.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0133") |
| Implementing PR | [#606](https://github.com/bajutsu-e2e/bajutsu/pull/606) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

The `check` job in [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml) installs
`actionlint` — the one gate step not covered by `uv sync` — by piping a script fetched from a
mutable git tag straight into `bash`. Every other third-party dependency in this repo's CI is
pinned by commit SHA. This proposal brings the `actionlint` installer in line with that posture.

## Motivation

`ci.yml:66` reads:

```yaml
- name: Install actionlint
  run: bash <(curl -fsSL https://raw.githubusercontent.com/rhysd/actionlint/v1.7.12/scripts/download-actionlint.bash) 1.7.12
```

This fetches `scripts/download-actionlint.bash` from the `v1.7.12` ref of `rhysd/actionlint` and
executes it directly. A git tag is mutable — it can be force-moved by the upstream maintainer or,
in a compromised-account scenario, by an attacker — unlike a commit SHA, which is immutable. The
script itself then downloads and unpacks the `actionlint` binary for the matching release. Because
the fetched script runs unmodified in the CI runner, a retagged `v1.7.12` would run with whatever
permissions the `check` job has (`contents: read`, per `ci.yml`'s top-level `permissions:` block) —
bounded, but still an unreviewed code-execution path.

By contrast, every other third-party action referenced anywhere in `.github/` — `actions/checkout`,
`astral-sh/setup-uv`, `actions/cache`, `actions/create-github-app-token`,
`actions/upload-pages-artifact`, `actions/deploy-pages`, `maxim-lobanov/setup-xcode`,
`actions/upload-artifact` — is pinned by full commit SHA with a version comment (e.g.
`actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0  # v7.0.0`). The `actionlint` installer
is the one exception to that established posture, and it is a `curl | bash` rather than a
`uses:` step, which is a strictly less reviewable supply-chain shape than an Action reference.

Severity: this finding is **unconfirmed** — no incident or upstream compromise is known, and the
job's permissions are already read-only, which caps the blast radius. It is flagged because it is a
straightforward, mechanical gap relative to a security posture this repository already holds itself
to everywhere else, not because of a demonstrated exploit.

## Detailed design

The fix has two independent parts, both scoped to `ci.yml:66`:

1. **Pin the installer script itself by commit SHA.** Replace the `v1.7.12` tag in the
   `raw.githubusercontent.com` URL with the commit SHA that tag currently points to, keeping a
   trailing comment noting the tag for human readability — the same convention already used for
   `uses:` pins (e.g. `@<sha>  # v1.7.12`). This makes the fetched script content immutable
   regardless of what the tag is later moved to.
2. **Verify the downloaded binary, if the installer script supports it.** Check whether
   `download-actionlint.bash` already verifies a checksum for the binary it fetches; if not, or if
   a released `actionlint` binary's checksum can be pinned independently (e.g. via
   `actionlint`'s published checksums file for the `1.7.12` release), add that verification so the
   binary — not just the installer script — is tamper-evident. This is a smaller, decoupled
   follow-up: pinning the script (item 1) is the primary fix and can land alone.

Both changes are confined to one `run:` line in `ci.yml`; neither touches `run`/CI's pass/fail
logic (the `check` job still only fails on `actionlint`'s own findings), so prime directive 1 is
unaffected. There is no product-code surface here — this is a CI supply-chain hardening only.

## Alternatives considered

- **Switch to a `uses:`-based actionlint Action instead of the installer script.** Several
  community Actions wrap the `actionlint` binary download; using one would let this pin follow the
  same SHA-pinned `uses:` pattern as every other third-party step. Rejected for this proposal
  because it swaps one third-party trust surface (rhysd's installer script) for another (a
  community-maintained Action) rather than simply pinning what is already used; worth reconsidering
  only if the existing installer script becomes hard to pin reliably.
- **Vendor the `actionlint` binary or build it from source in CI.** Removes the runtime fetch
  entirely, but adds real maintenance cost (updating a vendored binary, or a Go toolchain step) for
  a problem a SHA pin already solves with a one-line change. Disproportionate for the severity here.
- **Do nothing, since the job's permissions are already read-only.** The blast radius is bounded,
  but the whole point of this repo's existing SHA-pinning convention is to not rely on that
  bound holding for every future workflow change; leaving the one exception in place undermines the
  consistency of that convention rather than the immediate job.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Pin the `actionlint` installer script URL in `ci.yml` by commit SHA (keep the version as a comment)
- [x] Investigate and, if feasible, add checksum verification for the downloaded `actionlint` binary

- [#606](https://github.com/bajutsu-e2e/bajutsu/pull/606) — Pin the installer script to commit `914e7df` (the commit `v1.7.12` points at)
  in `ci.yml`, keeping `# v1.7.12` for readability. The installer script has no checksum
  verification of its own and fetches the binary from a mutable release asset, so a follow-on
  `Verify actionlint binary checksum` step pins the extracted linux/amd64 binary's sha256
  (`c872d6db…`, derived from `actionlint_1.7.12_linux_amd64.tar.gz` whose release checksum is
  `8aca8db9…`) — closing both the script and binary trust surfaces.

## References

- `.github/workflows/ci.yml:66` — the unpinned `curl | bash` installer invocation
- `.github/workflows/ci.yml` top-level `permissions: contents: read` — the bound on this step's
  blast radius today
- `rhysd/actionlint` — https://github.com/rhysd/actionlint
- Related: BE-0067 (code quality gate hardening), BE-0069 (executable contributor guardrails)
- Originates from the 2026-07-02 codebase-analysis report (security).
