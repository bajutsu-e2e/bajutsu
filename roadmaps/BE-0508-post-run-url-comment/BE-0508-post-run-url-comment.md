**English** · [日本語](BE-0508-post-run-url-comment-ja.md)

# BE-0508 — Post a serve run URL to a pull request, an issue, or a release with a helper script

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0508](BE-0508-post-run-url-comment.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0508") |
| Implementing PR | [#2599](https://github.com/bajutsu-e2e/bajutsu/pull/2599) |
| Topic | Integration with external services |
| Related | [BE-0503](../BE-0503-local-evidence-post/BE-0503-local-evidence-post.md), [BE-0504](../BE-0504-pr-evidence-comment-action/BE-0504-pr-evidence-comment-action.md), [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a helper script, `scripts/post_run_url.py`. The script posts the Uniform Resource
Locator (URL) of a run in `serve` to one of three targets:

- a pull request
- an issue
- a release

For a pull request or an issue, the script writes one comment. The comment gives the verdict and
links the run. It also names each failing scenario with its first failing step and reason. Running
the script again edits the same comment. For a release, the script writes the same text as a
delimited block in the release notes.

The script uploads nothing. The run is already in `serve`, and the item that imports a run into
`serve` returns its URL. This script merely tells people where the run is. It uses the standard
library alone and imports nothing from `bajutsu`. It runs after the verdict, so it never changes the
verdict.

## Motivation

A reviewer reads a pull request, not a server's history. After an engineer imports a run, the link
still has to reach the reviewer. Today the engineer pastes it by hand. A pasted link also states
nothing. The reviewer has to open it to learn whether the run passed and which scenario did not.

GitHub gives a script no documented way to attach a run's zip or video to a comment. The evidence
thus stays on `serve`, and the comment carries the text and the link. The observable difference
is the comment. Consider this command:

```
python3 scripts/post_run_url.py runs/<runId> --url <url> --to owner/repo#123
```

After it runs, the pull request holds one comment. The comment has the counts, each failing
scenario, and the link. A rerun edits that comment in place. A test checks that behavior.

## Detailed design

### The command

```
python3 scripts/post_run_url.py <run-dir | run-id> --url URL --to TARGET
        [--archive-url URL] [--runs DIR] [--marker NAME] [--author LOGIN]
        [--max-failures N] [--dry-run] [--yes]
```

`<run-dir | run-id>` names the run whose verdict and failing scenarios the comment reports. The
script looks up a run id under `--runs` (default `runs`). It reads `manifest.json`. When no manifest
exists, it reads each `<sid>/result.json` instead.

The command requires `--url`, the run's URL that the import returned. The script checks the URL as
follows.

| Check on `--url` | Reason |
|---|---|
| The path must contain `/runs/<runId>/` | The comment never pairs one run's verdict with another run's link |
| The URL must use `http` or `https` | The link cannot break the comment's Markdown |
| No blank characters, `<`, `>`, or `)` | The link cannot break the comment's Markdown |
| No userinfo (`user:password@`) and no query string | A credential pasted into `--url` never reaches a public thread |

The last check has the same reason as the refusal of a `bajutsu_` prefix in `--marker`, described
below. `--archive-url` is optional, and it adds a line that links the run's zip.

`--to` takes one of these forms:

- `owner/repo#NUMBER`
- a pull request or issue URL
- `release:owner/repo@TAG`

The script refuses a bare `#NUMBER`. In a fork workflow, `origin` is the fork, and a guessed
repository would post to the wrong thread. The script sends requests to `https://api.github.com`
alone. It accepts a `--to` URL on `https://github.com/` alone. It refuses any other domain before it
reads the token. A pasted URL can thus never send the token elsewhere.

### The comment

The comment starts with the hidden marker `<!-- bajutsu-run:<name> -->`. Before comparing a marker,
the script strips trailing blank characters and a carriage return. `--marker` names the marker slot,
which defaults to `local`. The whole value must match `[A-Za-z0-9._-]+`, as an anchored match.

The script also refuses a value that starts with `bajutsu_`. That prefix marks a `serve` key for
the Application Programming Interface (API). A confused user then cannot publish a secret. The
hidden marker may sit on a public thread.

The comment then gives these items:

- the verdict and the counts
- one block for each failing scenario, up to `--max-failures` (default 10)
- the count of the blocks that it omits
- the run's link

Every string that comes from the run goes inside a code span, or a fenced block when it spans lines.
The delimiter is a backtick run.
That run is one longer than the longest run inside. CommonMark requires a space of padding in one
case. The case is a code span whose text starts or ends with a backtick. The script strips `<!--` and
`-->` until none remain. Text from the app thus cannot add a mention, HTML, or a second marker.

The script cuts each such string at 500 characters and adds an ellipsis. It then cuts the whole body
and adds a note when it does:

| Target | The script cuts the body below | GitHub refuses a body over |
|---|---|---|
| Comment | 60,000 characters | 65,536 characters |
| Release notes | 120,000 characters | 125,000 characters |

The comment embeds no screenshot. GitHub cannot fetch an image from a URL that asks for a
login.

### Posting to a pull request or an issue

The script finds its own comment by the marker and by the login of the token's user.
`GET /user` returns that login. For a token with no user, `--author` names the login. The script
pages through the thread's comments. It edits the comment when it finds one and creates a comment
when it does not.

### Posting to a release

A release has no comments, so the script writes a block into the release notes. The block sits between
`<!-- bajutsu-run:<name> -->` and `<!-- /bajutsu-run:<name> -->`. The script replaces an existing
block. Otherwise, it appends a new one at the end. It finds the release by an exact tag match over the
listing. The tag endpoint does not return drafts. It never creates and never deletes a
release. A missing release is an error.

### Credentials and safety

The script reads a token from `GH_TOKEN`, then `GITHUB_TOKEN`, the order that `gh` uses. It asks
`gh auth token` when neither variable exists. It sends the token as an unredirected header, so a redirect
never carries it to another server. It never writes the token to disk or to a log. A comment needs
write access to pull requests or issues. A release edit needs write access to repository contents.

Before any write, the script prints a plan and asks for confirmation. The plan holds the target, what
the script will create or edit, and the text. `--yes` skips the question. `--dry-run` prints the plan
and the text without any write. The exit status says whether the post succeeded, and it never
reflects the run's verdict.

### Tests and documentation

The tests run against a fake GitHub HTTP server, so the gate needs no network. They cover these
cases.

| Area | Cases |
|---|---|
| Sticky comment | Create, then edit, and a match across a carriage return |
| Arguments | The `--marker` rules and a bare `#NUMBER` |
| URL checks | A `--to` URL on another domain |
| URL checks | A `--url` for another run or with a broken character |
| URL checks | A `--url` with userinfo or a query string |
| Rendering | Escaping with the backtick padding, and the length cuts |
| Release | The release block (replace and append) and a missing release |
| Credentials | The token order and the redirect rule |
| Result | The exit status |

`docs/reporting.md` and its `docs/ja/` mirror gain a section that follows the upload script's
section.

## Alternatives considered

- **One script that uploads and posts.** The two steps break for different reasons. They also need
  different credentials, a `serve` key and a GitHub token. Separate scripts let a team use either
  alone, and each stays small.
- **Embed the screenshots in the comment.** The images live behind the `serve` login.
  GitHub cannot render them. A text comment with a link states that limit plainly.
- **Upload the zip to GitHub.** [BE-0503](../BE-0503-local-evidence-post/BE-0503-local-evidence-post.md)
  proposed a release asset and a sticky comment. A release asset leaves an entry for each thread, and
  `gh --attach` refuses a zip. A separate change records that rejection.
- **Reuse `scripts/gh_cli.py`.** That wrapper keeps the roadmap scripts' `gh` calls in one place. A
  second GitHub client in `scripts/` repeats what the wrapper exists to prevent. A fix to the token
  order or to retries must now land twice. This script stays a single file that a team can copy out
  of the repository. It cannot import a sibling module for that reason. It talks to GitHub's
  Representational State Transfer API with the standard library. The tests pin the token
  order, and we accept the cost of the copy.
- **A GitHub Action.** [BE-0504](../BE-0504-pr-evidence-comment-action/BE-0504-pr-evidence-comment-action.md)
  covers a CI run. A script covers a run on a laptop, where no workflow exists. The two renderers stay
  separate on purpose. The Action links CI artifacts, and this script links a `serve` run, so their
  comment formats differ. Both follow the same escaping rules.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work). The log records what changed and when
> (oldest first), linking the PRs.

- [x] The command: argument parsing and the `--to` forms.
  It also covers the bare `#NUMBER` refusal and the `--url` check.
  It reads the manifest or the `result.json` files.
- [x] The renderer: the verdict and the failing blocks.
  It also covers the escaping, the length cuts, and the marker rules.
- [x] The GitHub client: the token order, the sticky comment, and the release block.
- [x] The plan and the guards: the plan and the confirmation.
  It also covers `--dry-run`, `--yes`, and the exit status.
- [x] Tests: a fake GitHub server and the cases above.
- [x] Documentation: a `docs/reporting.md` section and its `docs/ja/` mirror.

Log:

- [#2599](https://github.com/bajutsu-e2e/bajutsu/pull/2599), all six units shipped in one change. The table lists where the implementation differs
  from the design.

| Point | What shipped, and why |
|---|---|
| Run-id lookup | The flag is `--runs-dir`, the name that the sibling upload script uses for the same lookup. |
| `--archive-url` | It gets the same checks as `--url`, except the path check. A signed zip address would publish its signature. |
| Length cut | The script drops whole failing blocks near the limit. A cut inside a code span would let the rest render as Markdown. |
| `--dry-run` | A dry run without a token stays offline. The plan then says that nothing was looked up. |
| Release block | An opening marker without its closing marker stops the post, so no release text is lost. |

## References

- [BE-0504](../BE-0504-pr-evidence-comment-action/BE-0504-pr-evidence-comment-action.md): the Action that posts evidence from CI.
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md): masking at the artifact boundary.
- [`docs/reporting.md`](../../docs/reporting.md): the `manifest.json` schema that the script reads.
