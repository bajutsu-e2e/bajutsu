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

The `check` job in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) installs
`actionlint`, the one gate step that `uv sync` does not cover. It does so by piping a script
fetched from a mutable git tag straight into `bash`. Every other third-party dependency in this
repo's CI is pinned by commit SHA.

This proposal brings the `actionlint` installer in line with that posture.

## Motivation

`ci.yml:66` reads:

```yaml
- name: Install actionlint
  run: bash <(curl -fsSL https://raw.githubusercontent.com/rhysd/actionlint/v1.7.12/scripts/download-actionlint.bash) 1.7.12
```

This line fetches `scripts/download-actionlint.bash` from the `v1.7.12` ref of `rhysd/actionlint`
and executes it directly. The script then downloads and unpacks the `actionlint` binary for the
matching release.

A git tag is mutable. The upstream maintainer can force-move it, and so can an attacker in a
compromised-account scenario. A commit SHA, by contrast, is immutable. The fetched script runs
unmodified in the CI runner, so a retagged `v1.7.12` would run with whatever permissions the
`check` job has (`contents: read`, per the top-level `permissions:` block of `ci.yml`). The scope is
bounded, but it is still an unreviewed code-execution path.

Every other third-party action referenced anywhere in `.github/` is pinned by full commit SHA, with
a version comment (for example `actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0  #
v7.0.0`). Those actions are `actions/checkout`, `astral-sh/setup-uv`, `actions/cache`,
`actions/create-github-app-token`, `actions/upload-pages-artifact`, `actions/deploy-pages`,
`maxim-lobanov/setup-xcode`, and `actions/upload-artifact`.

The `actionlint` installer is the one exception to that established posture. It is also a
`curl | bash` rather than a `uses:` step, a strictly less reviewable supply-chain shape than an
Action reference.

Severity: this finding is **unconfirmed**. No incident or upstream compromise is known, and the
job's permissions are already read-only, which caps the blast radius. The finding is flagged because
it is a straightforward, mechanical gap relative to a security posture this repository already holds
itself to everywhere else. No demonstrated exploit motivates it.

## Detailed design

The fix has two independent parts, both scoped to `ci.yml:66`:

1. **Pin the installer script itself by commit SHA.** Replace the `v1.7.12` tag in the
   `raw.githubusercontent.com` URL with the commit SHA that the tag currently points to. Keep a
   trailing comment naming the tag, for human readability. This is the same convention that the
   `uses:` pins already use (for example `@<sha>  # v1.7.12`). The fetched script content then stays
   immutable, regardless of what the tag is later moved to.
2. **Verify the downloaded binary, if the installer script supports it.** Check whether
   `download-actionlint.bash` already verifies a checksum for the binary it fetches. If it does not,
   or if a released `actionlint` binary's checksum can be pinned independently (for example via
   `actionlint`'s published checksums file for the `1.7.12` release), add that verification. The
   binary, not just the installer script, then becomes tamper-evident. This is a smaller, decoupled
   follow-up: pinning the script (item 1) is the primary fix and can land alone.

Both changes are confined to one `run:` line in `ci.yml`. Neither touches `run`/CI's pass/fail logic
(the `check` job still fails only on `actionlint`'s own findings), so prime directive 1 is
unaffected. There is no product-code surface here; the change is CI supply-chain hardening alone.

## Alternatives considered

- **Switch to a `uses:`-based actionlint Action instead of the installer script.** Several community
  Actions wrap the `actionlint` binary download. Using one would let this pin follow the same
  SHA-pinned `uses:` pattern as every other third-party step. Rejected for this proposal. It swaps
  one third-party trust surface (rhysd's installer script) for another (a community-maintained
  Action), rather than simply pinning what is already used. Reconsider it only if the existing
  installer script becomes hard to pin reliably.
- **Vendor the `actionlint` binary or build it from source in CI.** This removes the runtime fetch
  entirely. It adds real maintenance cost, though: updating a vendored binary, or adding a Go
  toolchain step. A SHA pin already solves the problem with a one-line change, so the cost is
  disproportionate for the severity here.
- **Do nothing, since the job's permissions are already read-only.** The blast radius is bounded. But
  the whole point of this repo's existing SHA-pinning convention is to not rely on that bound
  holding for every future workflow change. Leaving the one exception in place undermines the
  consistency of that convention more than it endangers the immediate job.

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
