**English** · [日本語](BE-0124-config-source-owner-repo-validation-ja.md)

# BE-0124 — Tighten config-source owner and repo validation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0124](BE-0124-config-source-owner-repo-validation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

The Git config-source parser accepts `.`, `..`, and `%` inside an owner or repo segment. This
proposal constrains both to GitHub's actual allowed character set so a config source can't smuggle
a path-like or percent-encoded segment into a value that's later used to build a URL, a token
scope, or a cache path.

## Motivation

`bajutsu/config_source.py` parses `github:<owner>/<repo>[@<ref>][:<path>]` and
`git+https://<host>/<owner>/<repo>.git[@<ref>][#<path>]` with `_GITHUB_RE`
(`config_source.py:30-32`, owner/repo as `[^/@:]+`) and `_GIT_URL_RE` (`config_source.py:34-36`,
owner as `[^/@]+`, repo as `[^/@#]+?`). Neither excludes `.`, `..`, or `%`, so an owner/repo value
of `..` or a percent-encoded segment parses successfully. The parsed `owner`/`repo` feed the
GitHub API URL (`config_source.py:119,123`, `https://api.github.com/repos/{owner}/{repo}/...`)
and the on-disk cache path (`config_source.py:181`, `cache_root / spec.host / spec.owner /
spec.repo / sha`). A same-segment traversal token (`..`) or a value that decodes unexpectedly
once combined with other systems could redirect either the API call's target or the cache
directory outside the intended `<host>/<owner>/<repo>/<sha>/` layout.

Severity is Low: the value still goes over TLS to a fixed host, and GitHub's own API would reject
a malformed owner/repo in the request path in the common case, and the extraction step
(`config_source.py:181-212`) already guards against `..`/absolute paths in the *tarball's internal
entries* — this proposal is about the owner/repo segments themselves, which reach the cache path
before that stripping logic runs. The context is TLS + token auth to GitHub, not an open
filesystem or shell surface, which caps the practical impact.

## Detailed design

1. **Replace `_GITHUB_RE`'s and `_GIT_URL_RE`'s owner/repo character classes** with GitHub's real
   allowed charset: alphanumeric plus hyphen for an owner (a GitHub username/org — no leading/
   trailing/double hyphen, though a conservative `[A-Za-z0-9-]+` without re-deriving GitHub's full
   username grammar is an acceptable first cut), and alphanumeric plus `-`, `_`, `.` for a repo
   name — but explicitly reject a repo segment that is exactly `.` or `..`, or contains `%`
   (percent-encoding has no legitimate role in an owner/repo segment here).
2. **Add the rejection as a parse failure**, not a silent sanitization — `parse_config_source`
   (or wherever `_GITHUB_RE`/`_GIT_URL_RE` are consumed) returns `None`/raises the same way it
   already does for a non-matching string, so a malformed source fails the same way an unparseable
   one does today.
3. **Add unit tests** for the rejected shapes (`..`, a repo of `.`, a `%2e%2e`-style segment) and
   confirm the existing valid `owner/repo` shapes (including the ones covered by BE-0063's tests)
   still parse.

## Alternatives considered

- **Validate only at the cache-path-construction step, not at parse time.** Rejected: parsing is
  the single choke point every config source shape (`github:` shorthand and the general
  `git+https://` form) passes through, so validating there closes both call sites in one place
  rather than duplicating the check at every consumer of `owner`/`repo`.
  Failing fast at parse time also gives a clearer error message tied to the actual malformed input.
- **Percent-decode and re-validate instead of rejecting `%` outright.** Rejected as unnecessary
  complexity: GitHub owner/repo names never legitimately contain a `%`, so rejecting it outright is
  simpler and has no false-positive cost.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Tighten `_GITHUB_RE` and `_GIT_URL_RE`'s owner/repo character classes to GitHub's real
      charset, rejecting `.`, `..`, and `%`.
- [ ] Confirm the rejection surfaces as a parse failure, consistent with an unparseable source.
- [ ] Add unit tests for the rejected shapes and the existing valid shapes.

No PR has landed yet.

## References

`bajutsu/config_source.py:30-36` (`_GITHUB_RE`, `_GIT_URL_RE`), `bajutsu/config_source.py:119,123`
(API URL construction), `bajutsu/config_source.py:181` (cache path construction). Related: BE-0063
(Git config source). Originates from the 2026-07-02 codebase-analysis report (security).
