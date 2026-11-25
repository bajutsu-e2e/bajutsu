**English** · [日本語](BE-0508-post-run-url-comment-ja.md)

# BE-0508 — Post a serve run URL to a pull request, an issue, or a release with a helper script

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0508](BE-0508-post-run-url-comment.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0508") |
| Topic | Integration with external services |
| Related | [BE-0503](../BE-0503-local-evidence-post/BE-0503-local-evidence-post.md), [BE-0504](../BE-0504-pr-evidence-comment-action/BE-0504-pr-evidence-comment-action.md), [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a helper script, `scripts/post_run_url.py`, that posts the URL of a run in `serve` to a
pull request, an issue, or a release. For a pull request or an issue, it writes one comment that gives
the verdict, names each failing scenario with its first failing step and reason, and links the run.
Running it again edits the same comment. For a release, it writes the same text as a delimited block in
the release notes.

The script uploads nothing. The run is already in `serve`, and the item that imports a run into `serve`
returns its URL. This script only tells people where the run is. It uses the standard library alone,
imports nothing from `bajutsu`, and runs after the verdict, so it never changes the verdict.

## Motivation

A reviewer reads a pull request, not a server's history. After an engineer imports a run, the link
still has to reach the reviewer, and today the engineer pastes it by hand. A pasted link also states
nothing: the reviewer has to open it to learn whether the run passed and which scenario failed.

GitHub gives a script no documented way to attach a run's zip or video to a comment, so the evidence
stays on `serve`, and the comment carries the text and the link. The observable difference is the
comment. After `python3 scripts/post_run_url.py runs/<runId> --url <url> --to owner/repo#123`, the pull
request holds one comment with the counts, each failure, and the link. A rerun edits that comment in
place, and a test checks that it does.

## Detailed design

### The command

```
python3 scripts/post_run_url.py <run-dir | run-id> --url URL --to TARGET
        [--archive-url URL] [--runs DIR] [--marker NAME] [--author LOGIN]
        [--max-failures N] [--dry-run] [--yes]
```

`<run-dir | run-id>` names the run whose verdict and failures the comment reports. A run id is looked
up under `--runs` (default `runs`). The script reads `manifest.json`, or each `<sid>/result.json` when
no manifest exists. `--url` is required, and it is the run's URL that the import returned. The script
refuses a URL whose path does not contain `/runs/<runId>/`, so the comment never pairs one run's verdict
with another run's link. It also requires an `http` or `https` scheme and refuses whitespace, `<`, `>`,
and `)`, so the link cannot break the comment's Markdown. It refuses a URL that carries userinfo
(`user:password@`) or a query string, so a credential pasted into `--url` is never published to a
public thread, for the same reason that `--marker` refuses a `bajutsu_` prefix below. `--archive-url`
is optional, and it adds a line that links the run's zip.

`--to` takes `owner/repo#NUMBER`, a pull request or issue URL, or `release:owner/repo@TAG`. A bare
`#NUMBER` is refused, because in a fork workflow `origin` is the fork, and a guessed repository would
post to the wrong thread. The script sends requests only to `https://api.github.com`, and it accepts a
`--to` URL only on `https://github.com/`. It refuses any other host before it reads the token, so a
pasted URL can never send the token elsewhere.

### The comment

The comment starts with the hidden marker `<!-- bajutsu-run:<name> -->`, which is compared after
stripping trailing whitespace and a carriage return. `--marker` names the marker slot. It defaults to
`local`, and it must match `[A-Za-z0-9._-]+` over its whole value, as an anchored match. The script also
refuses a value that starts with `bajutsu_`, the prefix of a `serve` API key, so a confused user cannot
publish a secret in a hidden marker on a public thread.

The comment then gives the verdict and the counts, one block for each failing scenario up to
`--max-failures` (default 10), and the run's link. It states the count of the blocks that it omits.

Every string that comes from the run goes inside a code span, or a fenced block when it spans lines,
delimited by a backtick run one longer than the longest run inside. A code span whose text starts or
ends with a backtick gets a space of padding, as CommonMark requires. The script strips `<!--` and
`-->` until none remain, so text from the app cannot add a mention, HTML, or a second marker. The
script cuts each such string at 500 characters and adds an ellipsis. It then cuts the whole body below
60,000 characters for a comment, and below 120,000 for release notes, and it adds a note when it does,
because GitHub rejects a comment over 65,536 characters and a release body over 125,000. The comment
embeds no screenshot, because GitHub cannot fetch an image from a URL that asks for a login.

### Posting to a pull request or an issue

The script finds its own comment by the marker and by the login of the token's user, which
`GET /user` returns. For a token with no user, `--author` names the login. It pages through the
thread's comments. It edits the comment when it finds one and creates a comment when it does not.

### Posting to a release

A release has no comments, so the script writes a block into the release notes. The block sits between
`<!-- bajutsu-run:<name> -->` and `<!-- /bajutsu-run:<name> -->`. The script replaces an existing block
and appends a new one at the end otherwise. It finds the release by an exact tag match over the
listing, because the tag endpoint does not return drafts. It never creates and never deletes a release.
A missing release is an error.

### Credentials and safety

The script reads a token from `GH_TOKEN`, then `GITHUB_TOKEN`, the order that `gh` uses, and it asks
`gh auth token` when neither is set. It sends the token as an unredirected header, so a redirect never
carries it to another host. It never writes the token to disk or to a log. A comment needs write access
to pull requests or issues, and a release edit needs write access to repository contents.

Before any write, the script prints a plan: the target, what it will create or edit, and the text. It
asks for confirmation. `--yes` skips the question, and `--dry-run` prints the plan and the text without
any write. The exit status says whether the post succeeded, and it never reflects the run's verdict.

### Tests and documentation

The tests run against a fake GitHub HTTP server, so the gate needs no network. They cover the sticky
comment (create, then edit, and a match across a carriage return), the `--marker` rules, a bare
`#NUMBER`, a `--to` URL on another host, a `--url` for another run, with a broken character, or with userinfo or a query string, escaping with the backtick padding, the length cuts, the release
block (replace and append), a missing release, the token order and the redirect rule, and the exit
status. `docs/reporting.md` and its `docs/ja/` mirror gain a section that follows the upload script's
section.

## Alternatives considered

- **One script that uploads and posts.** The two steps fail for different reasons and need different
  credentials, a `serve` key and a GitHub token. Separate scripts let a team use either alone, and
  each stays small.
- **Embed the screenshots in the comment.** The images live behind the `serve` login, so GitHub cannot
  render them. A text comment with a link states that limit plainly.
- **Upload the zip to GitHub.** [BE-0503](../BE-0503-local-evidence-post/BE-0503-local-evidence-post.md)
  proposed a release asset and a sticky comment. A release asset leaves an entry for each thread, and
  `gh --attach` refuses a zip. A separate change records that rejection.
- **Reuse `scripts/gh_cli.py`.** That wrapper keeps the roadmap scripts' `gh` calls in one place, and
  a second GitHub client in `scripts/` repeats what it was written to prevent: a fix to the token
  order or to retries must now land twice. This script stays a single file that a team can copy out
  of the repository, so it cannot import a sibling module, and it talks to the REST API with the
  standard library. The tests pin the token order, and the cost of the copy is accepted.
- **A GitHub Action.** [BE-0504](../BE-0504-pr-evidence-comment-action/BE-0504-pr-evidence-comment-action.md)
  covers a CI run. A script covers a run on a laptop, where no workflow exists. The two renderers stay
  separate on purpose: the Action links CI artifacts, and this script links a `serve` run, so their
  comment formats differ. Both follow the same escaping rules.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] The command: the argument parsing, the `--to` forms and the bare `#NUMBER` refusal, the `--url` check, and reading the manifest or the `result.json` files.
- [ ] The renderer: the verdict, the failure blocks, the escaping, the length cuts, and the marker rules.
- [ ] The GitHub client: the token order, the sticky comment, and the release block.
- [ ] The plan and the guards: the plan, the confirmation, `--dry-run`, `--yes`, and the exit status.
- [ ] Tests: a fake GitHub server and the cases above.
- [ ] Documentation: a `docs/reporting.md` section and its `docs/ja/` mirror.

## References

- [BE-0504](../BE-0504-pr-evidence-comment-action/BE-0504-pr-evidence-comment-action.md): the Action that posts evidence from CI.
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md): redaction at the artifact boundary.
- [`docs/reporting.md`](../../docs/reporting.md): the `manifest.json` schema that the script reads.
