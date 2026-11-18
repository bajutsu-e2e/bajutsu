**English** · [日本語](BE-0124-config-source-owner-repo-validation-ja.md)

# BE-0124 — Tighten config-source owner and repo validation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0124](BE-0124-config-source-owner-repo-validation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0124") |
| Implementing PR | [#627](https://github.com/bajutsu-e2e/bajutsu/pull/627) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

The Git config-source parser accepts `.`, `..`, and `%` inside an owner or repo segment. A config
source can therefore smuggle a path-like or percent-encoded segment into a value that the tool later
uses to build a URL, a token scope, or a cache path. This proposal constrains both segments to
GitHub's actual allowed character set.

## Motivation

`bajutsu/config_source.py` parses two source forms:

- `github:<owner>/<repo>[@<ref>][:<path>]`, with `_GITHUB_RE` (`config_source.py:30-32`), which
  matches owner and repo as `[^/@:]+`.
- `git+https://<host>/<owner>/<repo>.git[@<ref>][#<path>]`, with `_GIT_URL_RE`
  (`config_source.py:34-36`), which matches owner as `[^/@]+` and repo as `[^/@#]+?`.

Neither pattern excludes `.`, `..`, or `%`, so an owner or repo value of `..`, or a percent-encoded
segment, parses successfully. The parsed `owner` and `repo` feed two places:

- the GitHub API URL (`config_source.py:119,123`, `https://api.github.com/repos/{owner}/{repo}/...`)
- the on-disk cache path (`config_source.py:181`, `cache_root / spec.host / spec.owner / spec.repo / sha`)

A same-segment traversal token (`..`) could redirect the API call's target or the cache directory
outside the intended `<host>/<owner>/<repo>/<sha>/` layout. So could a value that decodes unexpectedly
once combined with other systems.

Severity is Low, for three reasons:

- The value still goes over TLS to a fixed host.
- In the common case, GitHub's own API rejects a malformed owner or repo in the request path.
- The extraction step (`config_source.py:181-212`) already guards against `..` and absolute paths in
  the *tarball's internal entries*.

That guard does not cover what this proposal concerns, the owner and repo segments themselves. Those
segments reach the cache path before the stripping logic runs.

The context is TLS plus token authentication to GitHub, not an open filesystem or shell surface,
which caps the practical impact.

## Detailed design

1. **Replace the owner and repo character classes in `_GITHUB_RE` and `_GIT_URL_RE`** with GitHub's
   real allowed character set.
   - An owner (a GitHub username or organization) takes alphanumerics plus hyphen. GitHub forbids a
     leading, trailing, or doubled hyphen, but re-deriving its full username grammar is out of
     scope. A conservative `[A-Za-z0-9-]+` is an acceptable first cut.
   - A repo name takes alphanumerics plus `-`, `_`, and `.`. The parser explicitly rejects a repo
     segment that is exactly `.` or `..`, or that contains `%`. Percent-encoding has no legitimate
     role in an owner or repo segment here.
2. **Report the rejection as a parse failure, not a silent sanitization.** `parse_config_source` (or
   whichever code consumes `_GITHUB_RE` and `_GIT_URL_RE`) returns `None` or raises, exactly as it
   already does for a non-matching string. A malformed source then fails the same way an unparseable
   one does today.
3. **Add unit tests** for the rejected shapes (`..`, a repo of `.`, a `%2e%2e`-style segment). The
   tests also confirm that the existing valid `owner/repo` shapes still parse, including the ones
   covered by BE-0063's tests.

## Alternatives considered

- **Validate only at the cache-path-construction step, not at parse time.** Rejected. Parsing is the
  single choke point that every config source shape passes through, both the `github:` shorthand and
  the general `git+https://` form. Validating there closes both call sites in one place, where
  validating at the consumers would duplicate the check at every use of `owner` and `repo`. Failing
  fast at parse time also gives an error message tied to the actual malformed input.
- **Percent-decode and re-validate instead of rejecting `%` outright.** Rejected as unnecessary
  complexity. GitHub owner and repo names never legitimately contain a `%`, so rejecting it outright
  is simpler and has no false-positive cost.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Tighten `_GITHUB_RE` and `_GIT_URL_RE`'s owner/repo character classes to GitHub's real
      charset, rejecting `.`, `..`, and `%`.
- [x] Confirm the rejection surfaces as a parse failure, consistent with an unparseable source.
- [x] Add unit tests for the rejected shapes and the existing valid shapes.

- 2026-07-04: Constrained the owner class to `[A-Za-z0-9-]+` and the repo class to `[A-Za-z0-9._-]+`
  (so `%` no longer matches) and rejected a bare `.`/`..` repo segment in `parse_config_spec`, with
  unit tests for the rejected shapes and the dotted/`.git` shapes that must still parse.

## References

`bajutsu/config_source.py:30-36` (`_GITHUB_RE`, `_GIT_URL_RE`), `bajutsu/config_source.py:119,123`
(API URL construction), `bajutsu/config_source.py:181` (cache path construction). Related: BE-0063
(Git config source). Originates from the 2026-07-02 codebase-analysis report (security).
