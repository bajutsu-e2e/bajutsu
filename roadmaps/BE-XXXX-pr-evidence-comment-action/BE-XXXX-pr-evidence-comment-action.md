**English** · [日本語](BE-XXXX-pr-evidence-comment-action-ja.md)

# BE-XXXX — Post run evidence to a pull request or issue with a portable GitHub Action

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-pr-evidence-comment-action.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Integration with external services |
| Related | [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md), [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md), [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md), [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a composite GitHub Action, `post-evidence`, that posts the result of a finished `bajutsu run` to a pull request or an issue. The comment names each failed scenario, the step that failed, and the reason. It links to the run's evidence (`report.html`, the failing step's screenshot, and the scenario's video), which the action uploads as a GitHub Actions artifact. A rerun updates the same comment instead of adding a new one.

The action reads `manifest.json` and nothing else from Bajutsu. It installs no Bajutsu package and calls no Bajutsu command, so any repository that produces a Bajutsu run directory can use it with one `uses:` line.

## Motivation

A reviewer who sees a red Bajutsu check on a pull request today must open the Actions run, find the job, open its artifacts, download a zip, unpack it, and open `report.html`. Most reviewers stop before the last step. The failure reason sits in the job log, and the screenshot that explains it sits inside a zip.

[BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md) improves this for teams that run `serve`, but only as a check that lists failed scenarios and reasons. It carries no screenshot, video, or report link, and it needs a `serve` deployment and a GitHub App. [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md) exports a test table that third-party reporters can render. No path attaches the evidence itself to the pull request. [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) uploads evidence to object storage, yet nothing turns the uploaded files into something a reviewer sees on the pull request.

The observable difference is the pull request page. After a failing run, one comment appears. It states which scenarios failed and why, and it links to the artifact that holds the report, the failing screenshot, and the video. A reviewer reaches the evidence in one click from the pull request. A rerun edits that comment in place, so the conversation holds one comment per job, never one per run.

## Detailed design

### Where the action lives and what it needs

The action lives at `.github/actions/post-evidence/` in this repository. Another repository uses it as `bajutsu-e2e/bajutsu/.github/actions/post-evidence@<ref>`. Its renderer is one Python script that uses the standard library alone. The action runs it with `actions/setup-python` pinned to the repository's Python (3.13), with `update-environment: false`, and invokes the script through the step's `python-path` output. The runner's system Python is never used, and the calling job's own Python stays unchanged. The action therefore needs no `uv sync` and no checkout of Bajutsu.

The calling job grants `pull-requests: write` for a pull request, or `issues: write` for an issue. The action never needs `pull_request_target`. That trigger runs with a write token against code from a fork, which would let an untrusted scenario steer the comment, so the documentation names it as unsupported.

### Inputs and outputs

| Input | Default | Meaning |
|---|---|---|
| `run-dir` | `runs` | Either one run directory or a root that holds several. Given a root, the renderer reads every `*/manifest.json` directly under it and renders one section per run. |
| `artifact-name` | `${{ github.workflow }}-${{ github.job }}` | Name of the uploaded Actions artifact. A matrix job must add its matrix values, because one workflow run cannot hold two artifacts with the same name. Before uploading, the action replaces each character that `actions/upload-artifact` rejects (`" : < > | * ? \ /`, CR, LF) with `-`. |
| `issue-number` | the event's pull request number | Pull request or issue to comment on. Outside a pull request event it is optional. Without it, the action writes the job summary alone and posts no comment. |
| `github-token` | `${{ github.token }}` | Token that writes the comment. |
| `comment-key` | `${{ github.workflow }}-${{ github.job }}` | Key of the sticky comment. Two jobs with different keys keep two comments. A matrix job must add its matrix values, or each leg overwrites the comment the previous leg wrote. |
| `comment-author` | `github-actions[bot]` | Login of the account that owns the sticky comment. Set it to the App's bot login when `github-token` belongs to a GitHub App. |
| `max-failures` | `10` | Failed scenarios listed in full. The comment counts the rest. |
| `evidence-base-url` | empty | Optional base URL that serves the run directory over HTTP. See [Inline images](#inline-images-through-an-evidence-base-url). |

The action exposes one output, `artifact-url`, taken from the upload step.

### What the action does

1. **Upload.** The action uploads `run-dir` with `actions/upload-artifact`, so the whole run (report, screenshots, video, and logs) is one artifact.
2. **Render.** The script reads `manifest.json` and renders Markdown. The header gives the verdict and the counts of passed and failed scenarios. The renderer puts each scenario name and failure reason inside a code span, or a fenced block when the text spans lines. It delimits that text with a backtick run one longer than the longest run inside it, padded with a space, and a fenced block uses a fence longer than any backtick run inside. It strips the `<!--` and `-->` sequences repeatedly until none remain, so text from the app cannot add a mention, HTML, or a second marker. Under it, each failed scenario gets a block with the scenario name, the index and action of the first failed step, and the step's failure reason. A block also names the screenshot that belongs to the failed step and the scenario's video, each as a path inside the artifact.
3. **Publish.** The script writes the Markdown to the job summary, so a run with no pull request still shows it. When an `issue-number` resolves, the script then looks for a comment whose first line is exactly the hidden marker `<!-- bajutsu-evidence:<comment-key> -->`. It matches only comments whose author login equals `comment-author`. It updates the matching comment, or creates one when none exists.

The script reads only fields that the manifest already records. A screenshot or video the manifest does not list is omitted from the comment, and the omission is not an error.

### Size and failure behavior

GitHub caps a comment near 65,000 characters. The renderer stops listing scenarios after `max-failures`, counted across every run under `run-dir`, and states how many it omitted. It also cuts the whole body at a fixed length below the cap. It also truncates each failure reason to a fixed length.

The upload step runs with `continue-on-error: true`, so a failed upload degrades to a warning like any other failure. The comment then states that the evidence upload failed and links to the workflow run instead of an artifact. The action never changes a job's result. The test step's own exit code decides pass or fail, as it does for every Bajutsu run. When the token lacks write permission (a pull request from a fork gets a read-only token) or the API call fails, the script prints a warning, writes the summary alone, and exits zero. No LLM runs anywhere in the action, and the comment transports a verdict that the deterministic run already fixed.

### Secrets

The action posts text that `manifest.json` already carries. The run redacts that text before it writes the manifest ([BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md)). The action adds no source of text, such as the device log or `network.json`, and a comment never embeds file contents. The artifact keeps the access rules of the repository: a reader needs read access to download it.

### Inline images through an evidence base URL

An Actions artifact link opens a download page, and GitHub cannot render an image from it inside a comment. A comment therefore links to the artifact and names each screenshot and video by its path. This is the default, and it is a stated limit.

A team that also serves the run over HTTP can close the gap. Setting `evidence-base-url` makes the renderer emit a screenshot as `![](<base>/<path>)` and a video as a link under the same base. The action never uploads to that location. A job that already ran [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md)'s upload, with public or signed URLs, supplies the base. The input carries no credential, and a signed URL that expires leaves a broken image after expiry, which the documentation states.

### Documentation and adoption

`docs/reporting.md` and its `docs/ja/` mirror gain a section that shows a copy-paste workflow: the run step, then `post-evidence` with `if: always()`. This repository adopts the action in its web E2E workflow as the first user, so the comment shape is exercised by CI on real failures.

## Alternatives considered

- **A `bajutsu` subcommand that posts the comment.** A subcommand would reuse the report code directly, and it would work outside GitHub Actions. We rejected it because the stated goal is a portable form that changes nothing in Bajutsu. A subcommand would also force every consuming job to install Bajutsu just to post a comment.
- **Extend [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md).** The check already runs on `serve`, so adding evidence links there is natural. We kept the two separate because BE-0500 needs a `serve` deployment and a GitHub App, whereas this action needs neither. The two can share the Markdown renderer later, and that sharing is out of scope here.
- **A CTRF reporter action over `ctrf.json`.** Reporters already render a table of results from [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md)'s file. Each reporter differs in how it presents attachments, and none is guaranteed to link a failure to a screenshot inside the artifact. We chose a renderer that we control, and a team can still use both.
- **Embed screenshots as `data:` URIs.** GitHub's sanitizer strips them, and the comment size cap would not hold them anyway.
- **Commit evidence to an orphan branch or a release.** Either path gives stable image URLs. Each also grows the repository, needs write access to contents, and needs a retention policy. The optional base URL lets a team make that choice itself.
- **Publish `report.html` to GitHub Pages.** Pages would show the whole report in a browser. It makes run output public by default for many repositories, and it needs a cleanup policy per pull request, so it stays a choice for a later item.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Renderer: `manifest.json` to Markdown, with truncation, in a standard-library script and unit tests that run in `make test`.
- [ ] Comment client: find-or-update the sticky comment, plus the job-summary write and the warn-and-exit-zero path.
- [ ] Composite action: input wiring, artifact upload, the `artifact-url` output, and the optional `evidence-base-url`.
- [ ] Gate coverage: the script passes `ruff` and `mypy --strict` under `make check`: `make typecheck` adds `.github/actions/post-evidence`, and the unit tests import the script from that path.
- [ ] Documentation: a `docs/reporting.md` section and its `docs/ja/` mirror.
- [ ] Adoption: the web E2E workflow of this repository calls the action.

## References

- [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md): publishes a run-set group as a GitHub check run, without evidence.
- [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md): the CTRF export of a run.
- [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md): uploads a run's evidence to object storage.
- [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md): the precedent for a delivery that transports a fixed verdict and never fails the run.
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md): redaction at the artifact boundary.
- [`docs/reporting.md`](../../docs/reporting.md): the `manifest.json` schema the action reads.
