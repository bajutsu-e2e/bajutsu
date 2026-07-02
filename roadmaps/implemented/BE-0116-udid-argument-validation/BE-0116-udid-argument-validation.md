**English** · [日本語](BE-0116-udid-argument-validation-ja.md)

# BE-0116 — Tighten UDID validation against argument injection

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0116](BE-0116-udid-argument-validation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

`serve`'s UDID validation regex permits a leading hyphen, so a client-supplied `udid` can start
with `-`. This proposal tightens the pattern so a UDID can never be mistaken for a subprocess
flag.

## Motivation

`bajutsu/serve/helpers.py:27` defines `_UDID_RE = re.compile(r"^[A-Za-z0-9-]+$")`, used
(`helpers.py:481`) to validate `--udid` tokens coming from `serve` request bodies before they
reach a `bajutsu` subprocess invocation (`run` / `record` / `crawl` / `doctor`). Because the
character class allows `-` anywhere, including the first position, a value like `-rf` or
`--config` validates as a legal UDID. `serve` passes the validated token as a single argv
element (not through a shell), so classic shell-injection is not in play — but many CLI parsers,
and `idb`/`xcrun` in particular, treat a leading-hyphen argument as an option rather than a
positional value, so a crafted `udid` can be interpreted as a flag by the downstream tool the
subprocess invokes.

Severity is Low: the attacker still needs a request that reaches `serve`'s `/api/run` (or
`/api/record` / `/api/crawl`) with the RBAC/token gate already passed (BE-0051), and the affected
argument feeds a fixed subprocess argv rather than a shell, which caps the blast radius to
argument confusion in the downstream tool, not arbitrary command execution.

## Detailed design

1. **Tighten `_UDID_RE`** from `^[A-Za-z0-9-]+$` to `^[A-Za-z0-9][A-Za-z0-9-]*$`, requiring the
   first character to be alphanumeric. This still accepts every real UDID shape (`simctl`'s
   hyphenated UUIDs, physical-device 40-character identifiers, and the literal `booted`), since
   none of them start with `-`.
2. **Re-run the existing validation call sites** (`helpers.py:481` and its callers in the
   `/api/run`, `/api/record`, `/api/crawl`, `/api/doctor` request paths) unchanged — the fix is
   confined to the regex, not the call sites, since `_valid_udid` (or equivalent) already rejects
   the whole value on a non-match.
3. **Add a unit test** asserting a leading-hyphen value (e.g. `-rf`, `--help`) is rejected while
   real UDID shapes and `booted` still pass.

## Alternatives considered

- **Reject any UDID containing a hyphen anywhere except the standard UUID positions.** Rejected
  as over-specific: it would hard-code the 8-4-4-4-12 UUID shape into the validator, breaking
  forward compatibility if a future UDID format differs, for no additional injection protection
  beyond banning a leading hyphen.
- **Sanitize by stripping leading hyphens instead of rejecting.** Rejected: silently rewriting a
  client-supplied value could route the subprocess to a different, unintended device; failing
  the request outright is safer and more debuggable.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Tighten `_UDID_RE` to `^[A-Za-z0-9][A-Za-z0-9-]*$`.
- [x] Add a unit test covering leading-hyphen rejection and legitimate UDID/`booted` acceptance.

- 2026-07-02: Tightened `_UDID_RE` to require an alphanumeric first character and extended
  `test_valid_udid` with leading-hyphen rejection cases.

## References

`bajutsu/serve/helpers.py:27` (`_UDID_RE`), `bajutsu/serve/helpers.py:481`. Related: BE-0035
(device control primitives). Originates from the 2026-07-02 codebase-analysis report (security).
