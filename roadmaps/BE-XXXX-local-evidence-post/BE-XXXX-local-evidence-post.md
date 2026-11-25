**English** · [日本語](BE-XXXX-local-evidence-post-ja.md)

# BE-XXXX — Post a local run's evidence to a pull request or issue from the command line

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-local-evidence-post.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Integration with external services |
| Related | [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md), [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md), [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md), [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md), [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a command, `bajutsu post`, that takes a finished run from a developer's own machine and posts it to a pull request or an issue. The comment names each failed scenario, the failing step, and the reason, and it links to the run's evidence. A second run of the command updates the same comment instead of adding a new one. The command uses the developer's own GitHub credentials, and it runs after the verdict, so it never changes what the run decided.

The comment has the same shape as the one the portable `post-evidence` GitHub Action posts from CI, which a sibling proposal defines. The two entry points share one renderer, so a reviewer reads one format whether a runner or a laptop produced the run.

## Motivation

Many Bajutsu runs never reach CI. A developer who changes an iOS flow runs the scenarios against a local Simulator, because the CI lanes cover a fraction of the suite and a device-cloud run costs time and money. The evidence that the change works then sits in `runs/` on that laptop. To show it to a reviewer, the developer exports a zip with `bajutsu export` ([BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)) and drags files into a comment by hand in the browser, one screenshot or video at a time.

[BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) already uploads a run's evidence to object storage from `bajutsu run`, but nothing turns the uploaded files into a comment. The CI-side Action cannot help here either, because it needs a workflow run and a runner, and a local run has neither.

The observable difference is a single command. After `bajutsu post <run> --to owner/repo#123`, the pull request holds one comment that states which scenarios passed or failed and why, with links to the failing screenshot, the video, and the report. Running the command again after a fix edits that comment in place. A developer no longer opens a browser to drag files.

## Detailed design

### The command

```
bajutsu post <run-id | run-dir> --to <target> [--sink store|release|none]
             [--evidence-base-url URL] [--key KEY] [--max-failures N]
             [--log-tail-kb N] [--size-warn-mb N] [--dry-run] [--yes]
```

`<run-id | run-dir>` is resolved the way `bajutsu export` resolves it. `--to` takes `owner/repo#123`, the URL of a pull request or an issue, or `#123`, which the command resolves against the `origin` remote of the current checkout. The command posts, and it does not run a scenario or read an AI provider. `run` keeps no knowledge of GitHub, because a posting step inside `run` would tie a verdict to a network call.

### Credentials

The command reads a token from `GITHUB_TOKEN` or `GH_TOKEN`. When neither is set and the `gh` CLI is installed, it asks `gh auth token`. It never writes the token to disk, to the run directory, or to a log. A pull request or an issue comment needs write access to pull requests or issues. The `release` sink also needs write access to repository contents.

### Where the evidence goes

GitHub has no documented endpoint that uploads an image or a video into a comment. The command therefore puts the files somewhere a link can reach, and the comment carries the links. The `--sink` option chooses where.

| Sink | What it does | Needs |
|---|---|---|
| `store` | Uploads the run tree with the uploader of [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) to `--evidence-store` or `BAJUTSU_EVIDENCE_STORE`. The comment links each file under `--evidence-base-url`, and a screenshot appears inline. | An `s3://` or `gs://` URI and a base URL that serves it. |
| `release` | Uploads the failing screenshots, the videos, and the whole-run zip as assets of one draft release for the pull request. The comment links each asset. | Write access to repository contents. |
| `none` | Uploads nothing. The comment names each file by its path, for a developer who attaches files by hand. | Nothing beyond the comment token. |

The default is `store` when a store URI is set, and `release` otherwise. A release asset needs a repository sign-in to open, and a comment cannot render it as an inline image. Whether a browser displays or downloads each asset type is unverified, and the first work item is a spike that records it. The result may change the default.

### What the comment holds

The renderer produces the same Markdown as the Action. The header gives the verdict, the counts, and the run id. Each failed scenario gets a block with its name, the first failed step, and the failure reason. The renderer reads `manifest.json`, or `<sid>/result.json` when a run left no manifest. It escapes every text it copies with the rules of the Action's item. The sticky comment uses the hidden marker `<!-- bajutsu-evidence:<key> -->` on its first line, compared after stripping trailing whitespace and a carriage return. `--key` defaults to `local`, so a local comment and a CI comment on the same pull request stay separate.

The command finds its own comment through the login that `GET /user` returns for the token. A token that has no user, such as a GitHub App installation token, passes the login with `--author`.

### Confirming before it uploads

A screenshot or a video can show private data, and an upload to a repository is outward-facing. A deleted asset can stay cached. The command therefore prints a plan before it uploads: the target, the repository's visibility, each file, and the total size. It asks for confirmation. `--yes` skips the question for a script. `--dry-run` prints the plan and the Markdown it would post, then stops without any network write.

The command truncates an oversize `deviceLog` to its last `--log-tail-kb` kilobytes before it uploads, and it skips the whole-run zip above `--size-warn-mb`. It states both in the plan and in the comment. Text redaction happens when the run writes `manifest.json` ([BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md)). The pixels of a screenshot or a video carry no redaction, and the confirmation exists for that reason.

### Exit status

`bajutsu post` is a command the developer ran to post, so a failure to post exits non-zero with the reason. This differs from `run --evidence-store`, whose upload is a side effect of a run and degrades to a warning. The exit status of `post` never reflects the run's verdict. A failed run still posts, and the command exits zero when the post succeeds.

### One renderer for two entry points

The CLI and the Action must not drift. The renderer therefore lives in one module that uses the standard library alone and imports nothing else from Bajutsu. The CLI imports it. The Action loads the same file from the checkout of its own repository. The Action's item currently places its script in the Action directory, so the two items reconcile on one location, and the work breakdown below names that step.

### Documentation

`docs/cli.md` and its `docs/ja/` mirror gain a `post` section, with the sink table and a worked example for a local Simulator run.

## Alternatives considered

- **A `--post` flag on `run`.** One command would run and post. We rejected it because `run` would then depend on the network and on GitHub credentials, and a run killed partway would never post. A separate command also posts runs that finished earlier.
- **Extend `bajutsu export`.** The command already builds a zip, so posting would be a small step. A zip is the wrong unit for a comment, though, because a reviewer wants the failing screenshot and the reason first. `export` stays a pure archiver.
- **The web upload endpoint that the browser uses.** It would put an image inline in a comment. It is undocumented, it can change without notice, and a tool that depends on it breaks silently. We did not adopt it.
- **Wrap the `gh` CLI in a documented script.** A script would cost nothing to ship. It would also leave the renderer, the escaping, the sticky comment, and the confirmation for every team to rewrite, and the CI and local comments would drift apart.
- **Upload from `serve`.** The `serve` server already holds runs and could post them. It requires a deployment and a GitHub App ([BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md)), whereas a laptop needs neither.
- **Commit the evidence to a branch.** A branch gives stable URLs. It grows the repository, needs a retention policy, and writes to the repository's history, so it stays out of the defaults.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Spike: upload a screenshot, a video, and a zip as release assets, and record whether a signed-in reader sees each displayed or downloaded.
- [ ] Shared renderer: one standard-library module that both the CLI and the Action load, reconciled with the Action item's script location.
- [ ] GitHub client: token resolution, `GET /user`, the find-or-update sticky comment with paging, and `--author`.
- [ ] Sinks: `store` over the BE-0110 uploader, `release`, and `none`, with the base URL and the plan listing.
- [ ] Command: `bajutsu post` with `--to` parsing, the confirmation, `--dry-run`, `--yes`, log truncation, the size guard, and the exit status.
- [ ] Tests: a fake GitHub HTTP server that the gate runs offline, covering the sticky comment, the CRLF marker match, and the no-manifest case.
- [ ] Documentation: a `docs/cli.md` section and its `docs/ja/` mirror.

## References

- [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md): exports a run as a zip.
- [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md): uploads a run's evidence to object storage.
- [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md): the precedent for a delivery that runs after the verdict.
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md): redaction at the artifact boundary.
- [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md): publishes a run-set group as a GitHub check run.
- [`docs/cli.md`](../../docs/cli.md): the CLI reference that gains the `post` section.
