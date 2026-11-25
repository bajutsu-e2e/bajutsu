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

This item adds a composite GitHub Action, `post-evidence`, that posts the result of a finished `bajutsu run` to a pull request or an issue. The comment names each failed scenario, the step that failed, and the reason. It links to the evidence that the action uploads as GitHub Actions artifacts. One artifact holds the whole run as a zip. For the first three failed scenarios, the failing step's screenshot and the scenario's video are also uploaded as separate, non-zipped artifacts, so a browser shows each directly. A rerun updates the same comment instead of adding a new one.

The action reads `manifest.json`, or `<sid>/result.json` when a run left no manifest, and nothing else from Bajutsu. It installs no Bajutsu package and calls no Bajutsu command, so any repository that produces a Bajutsu run directory can use it with one `uses:` line.

## Motivation

A reviewer who sees a red Bajutsu check on a pull request today must open the Actions run, find the job, open its artifacts, download a zip, unpack it, and open `report.html`. Most reviewers stop before the last step. The failure reason sits in the job log, and the screenshot that explains it sits inside a zip.

[BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md) improves this for teams that run `serve`, but only as a check that lists failed scenarios and reasons. It carries no screenshot, video, or report link, and it needs a `serve` deployment and a GitHub App. [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md) exports a test table that third-party reporters can render. No path attaches the evidence itself to the pull request. [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) uploads evidence to object storage, yet nothing turns the uploaded files into something a reviewer sees on the pull request.

A zip of the whole run also grows without a bound. Bajutsu streams the device log (`simctl log stream --level debug` on iOS, `logcat` on Android) straight to `device.log` and never caps it. The report embeds only the last 2,000 lines, yet the file on disk keeps every line. Video does not shrink in a zip, because MP4 and WebM are already compressed. A zip artifact cannot close the gap by itself. GitHub offers no in-browser view of a zip's contents, and `report.html` carries its CSS and JavaScript inline but refers to its screenshots and videos by relative path. Uploaded alone, the page opens with broken images. A single image or video file has no such problem, because a browser displays it natively.

The observable difference is the pull request page. After a failing run, one comment appears. It states which scenarios failed and why. A reviewer clicks a screenshot link and sees the image, or clicks a video link and plays it, with no download and no unzip. The whole run stays one click away as a zip. A rerun edits the comment in place, so the conversation holds one comment per job, never one per run. A reader still needs read access to the repository, because artifact URLs require sign-in.

## Detailed design

### Where the action lives and what it needs

The action lives at `.github/actions/post-evidence/` in this repository. Another repository uses it as `bajutsu-e2e/bajutsu/.github/actions/post-evidence@<ref>`. Its renderer is one Python script that uses the standard library alone. The action runs it with `actions/setup-python` pinned to the repository's Python (3.13), with `update-environment: false`, and invokes the script through the step's `python-path` output. The runner's system Python is never used, and the calling job's own Python stays unchanged. The action therefore needs no `uv sync` and no checkout of Bajutsu.

The calling job grants `pull-requests: write` for a pull request, or `issues: write` for an issue. The action never needs `pull_request_target`. That trigger grants a write token to a job that, to run a fork's scenarios, must check out and execute the fork's code, so the documentation names it as unsupported. Every input reaches a shell step or the script through `env:`, never through an inline `${{ }}` ([BE-0123](../BE-0123-composite-action-input-indirection/BE-0123-composite-action-input-indirection.md)).

### Inputs and outputs

| Input | Default | Meaning |
|---|---|---|
| `run-dir` | `runs` | Either one run directory or a root that holds several. Given a root, the renderer reads every `*/manifest.json` directly under it and renders one section per run. |
| `artifact-name` | `${{ github.workflow }}-${{ github.job }}` | Name of the zip artifact, and the prefix of the non-zipped ones. A matrix job must add its matrix values, because one workflow run cannot hold two artifacts with the same name. Before uploading, the action replaces each character that `actions/upload-artifact` rejects (`" : < > | * ? \ /`, CR, LF) with `-`. |
| `retention-days` | `14` | Days GitHub keeps every artifact the action uploads. The value must lie between 1 and 90, and a shorter period frees storage sooner. |
| `compression-level` | `6` | Zlib level of the whole-run zip, from 0 to 9. A run that is mostly video barely shrinks at any level, so `0` uploads it faster. |
| `log-tail-kb` | `1024` | A `deviceLog` file larger than this keeps only its last `log-tail-kb` kilobytes in the zip. `0` keeps every log whole. |
| `size-warn-mb` | `500` | When the staged zip content exceeds this size, the action skips the whole-run upload and the comment warns. `0` disables the guard. |
| `upload` | `true` | When `false`, the action uploads nothing and links `artifact-url` for the whole run. Use it after a step, such as the existing `bajutsu-e2e` action, that already uploaded `runs/`. The comment then names screenshots and videos by path and links no per-file artifact. With `false`, the action stages nothing, so log truncation, `size-warn-mb`, `retention-days`, and `compression-level` do not apply, and the comment makes no truncation claim. Paths are given relative to the parent of `run-dir`, matching an upload of `runs/`. An empty `artifact-url` links to the workflow run. |
| `artifact-url` | empty | URL of the artifact to link when `upload` is `false`. |
| `issue-number` | the event's pull request number | Pull request or issue to comment on. Outside a pull request event it is optional. Without it, the action writes the job summary alone and posts no comment. |
| `github-token` | `${{ github.token }}` | Token that writes the comment. |
| `comment-key` | `${{ github.workflow }}-${{ github.job }}` | Key of the sticky comment. Two jobs with different keys keep two comments. A matrix job must add its matrix values, or each leg overwrites the comment the previous leg wrote. |
| `comment-author` | `github-actions[bot]` | Login of the account that owns the sticky comment. Set it to the App's bot login when `github-token` belongs to a GitHub App. |
| `max-failures` | `10` | Failed scenarios listed in full. The comment counts the rest. |
| `evidence-base-url` | empty | Optional base URL that serves the run directory over HTTP. See [Full fidelity through an evidence base URL](#full-fidelity-through-an-evidence-base-url). |

The action exposes one output, `artifact-url`, the URL of the whole-run artifact.

### What the action does

1. **Select.** The script reads `manifest.json` and picks the files to upload. It names the failed step's screenshot and the scenario's video for each of the first three failed scenarios, and writes their paths as step outputs.
2. **Stage and upload.** The action stages a copy of `run-dir` in the runner's temporary directory, as [Keeping the zip small](#keeping-the-zip-small) describes, and uploads that copy as one zip artifact with `actions/upload-artifact`, leaving `overwrite` at its default of `false`. Two matrix legs with the same name then conflict visibly in the job log instead of replacing each other. A non-zipped upload takes one file per artifact (`archive: false`), and a composite action cannot repeat a step. The action therefore has six fixed upload steps, three screenshot slots and three video slots. Each slot runs only when the select step filled it.
3. **Render and publish.** The script renders Markdown and writes it to the job summary, so a run with no pull request still shows it. When an `issue-number` resolves, the script looks for a comment whose first line is exactly the hidden marker `<!-- bajutsu-evidence:<comment-key> -->`. It matches only comments whose author login equals `comment-author`, and it pages through every comment on the issue (`per_page=100`), so the marker is found however long the conversation grows. It updates the matching comment, or creates one when none exists.

The rendered comment has this shape. The header gives the verdict, the counts of passed and failed scenarios, the commit SHA (`github.sha`), and a link to the workflow run. Under it, each failed scenario gets a block with its name, the index and action of the first failed step, and the step's failure reason. When no entry in `steps` failed, the block shows the scenario-level `failure` summary instead, and names no step or screenshot. A block links the screenshot and the video through their non-zipped artifacts. A scenario outside the first three names its screenshot and video by their path inside the whole-run artifact.

The renderer puts each scenario name, failure reason, and artifact path inside a code span, or a fenced block when the text spans lines. It delimits that text with a backtick run one longer than the longest run inside it, padded with a space, and a fenced block uses a fence longer than any backtick run inside. Together the code span and the fence keep app text from adding a mention or HTML. The renderer also strips `<!--` and `-->` repeatedly until none remain, so the text cannot forge a second marker.

The script reads only fields that the manifest already records. A screenshot or video the manifest does not list is omitted from the comment, and the omission is not an error.

### Keeping the zip small

These measures apply when `upload` is `true`. Bajutsu does not cap `device.log` while it records, so the action caps the log in the staged copy. Bounding the log at capture is a change to Bajutsu itself, which this item avoids. A separate item can add it.

- **Truncate an oversize log.** The staged copy replaces each artifact of kind `deviceLog` (listed by `manifest.json`, or by `<sid>/result.json` when no manifest exists) that exceeds `log-tail-kb` with its last `log-tail-kb` kilobytes. The first line of the truncated file names the original size and the kept size. The comment states that a log was truncated, and the full log is uploaded nowhere, so it is lost when the job's runner is discarded. The truncation keeps the end of the log, because a failure leaves its trace near the end.
- **Warn before an upload that cannot fit.** The script sums the staged files. When the sum exceeds `size-warn-mb`, it skips the whole-run upload. The comment names the total and the threshold, and still links the small per-file artifacts. This guard protects the shared storage allowance of the repository owner, which counts every artifact and fails later uploads once it is full. The default threshold is a conservative guess and not a GitHub limit. GitHub's per-artifact maximum and its per-plan allowances were not confirmed from the official pages while this item was written, so a team tunes the threshold to its plan. When the whole-run upload is skipped or fails, the comment lists no path inside it, and the `artifact-url` output is empty.
- **Shorten retention.** `retention-days` defaults to 14, below the 90-day default of GitHub, because evidence from a pull request loses value after the pull request merges.
- **Tune compression.** `compression-level` stays at the default of 6 because logs compress well. A team whose runs are mostly video sets it to `0` for a faster upload at no size cost.

Truncation never edits `manifest.json`. A reader who opens the unpacked `report.html` sees the log link point to the shortened file, and the comment is the place that says so.

### Viewing the evidence in a browser

A browser displays a non-zipped PNG, MP4, or WebM natively and needs no JavaScript to do so. That property makes the per-file artifacts the main path for a quick look. GitHub's documentation does not say whether a non-zipped artifact is served for display or for download in every browser. The first work item is therefore a spike that uploads a screenshot and a video with `archive: false` and records what a signed-in reader sees. If a file type downloads instead of displaying, the link still delivers the file, and the documentation states the result.

The action does not upload `report.html` as a non-zipped artifact. Its relative image and video paths would break, as the Motivation describes. A reader who wants the full report downloads the whole-run zip and opens `report.html` after unpacking, which works offline ([BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)).

### Size and failure behavior

GitHub caps a comment near 65,000 characters. The renderer stops listing scenarios after `max-failures`, counted across every run under `run-dir`, and states how many it omitted. It cuts the whole body at a fixed length below the cap, and it truncates each failure reason to a fixed length.

When a run directory holds no `manifest.json`, the script reads each `<sid>/result.json`, which Bajutsu writes the moment a scenario ends, to list the scenarios that finished and the `deviceLog` artifacts to truncate. It still stages and uploads the whole run. A run killed partway, by a crash or a job timeout, leaves no manifest, because Bajutsu writes it after every scenario finishes. The comment states that the run did not complete and links to the workflow run, so a crashed run never leaves an earlier verdict standing. Under a root that holds several runs, the comment names every directory without a manifest and never skips it.

Every upload step runs with `continue-on-error: true`, so a failed upload degrades to a warning like any other failure. The comment then states that the evidence upload failed and links to the workflow run instead of an artifact. The action never changes a job's result. The test step's own exit code decides pass or fail, as it does for every Bajutsu run. When the token lacks write permission (a pull request from a fork gets a read-only token) or the API call fails, the script prints a warning, writes the summary alone, and exits zero. No LLM runs anywhere in the action, and the comment transports a verdict that the deterministic run already fixed.

### Secrets

Truncating a log is not redaction, and the staged copy keeps whatever the log already holds. The action posts text that `manifest.json` already carries. The run redacts that text before it writes the manifest ([BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md)). The action adds no source of text, such as the device log or `network.json`, and a comment never embeds file contents. The artifacts keep the access rules of the repository: a reader needs read access to open or download them. The screenshots and videos are the app's own pixels, which redaction of text does not cover. A team that cannot upload them sets `upload: false` and keeps its own handling.

### Full fidelity through an evidence base URL

Artifact links cannot render an image inside a comment, because the URL needs sign-in. A team that also serves the run over HTTP can close the gap. Setting `evidence-base-url` makes the renderer emit a screenshot as `![](<base>/<path>)`, a video as a link, and a link to `<base>/report.html`, all under the same base. Each path segment is percent-encoded before it is joined to the base. The action never uploads to that location. A job that already ran [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md)'s upload, with public or signed URLs, supplies the base. The input carries no credential, and a signed URL that expires leaves a broken image after expiry, which the documentation states.

### Documentation and adoption

`docs/reporting.md` and its `docs/ja/` mirror gain a section that shows a copy-paste workflow: the run step, then `post-evidence` with `if: always()`. This repository adopts the action in its web E2E workflow as the first user, so the comment shape is exercised by CI on real failures.

## Alternatives considered

- **A `bajutsu` subcommand that posts the comment.** A subcommand would reuse the report code directly, and it would work outside GitHub Actions. We rejected it because the stated goal is a portable form that changes nothing in Bajutsu. A subcommand would also force every consuming job to install Bajutsu just to post a comment.
- **Extend [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md).** The check already runs on `serve`, so adding evidence links there is natural. We kept the two separate because BE-0500 needs a `serve` deployment and a GitHub App, whereas this action needs neither. The two can share the Markdown renderer later, and that sharing is out of scope here.
- **A CTRF reporter action over `ctrf.json`.** Reporters already render a table of results from [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md)'s file. Each reporter differs in how it presents attachments, and none is guaranteed to link a failure to a screenshot inside the artifact. We chose a renderer that we control, and a team can still use both.
- **Upload `report.html` alone as a non-zipped artifact.** The page would open, but its images and videos would not, because their relative paths resolve to nothing. Whether GitHub runs the inline JavaScript of an artifact is also unverified.
- **A single-file `report.html` with images embedded as `data:` URIs.** It would need a change to the Bajutsu report, which this item avoids. Videos are too large to embed, and the JavaScript question above applies as well. A later item can revisit it if the spike shows that artifact pages run scripts.
- **Embed screenshots in the comment as `data:` URIs.** GitHub's sanitizer strips them, and the comment size cap would not hold them anyway.
- **A third-party artifact viewer, such as the artifact.ci GitHub App.** It gives browsable links to a whole artifact, but it hands the run's screenshots and videos to a third party. We keep it out of the default and leave the choice to each team.
- **Commit evidence to an orphan branch or a release.** Either path gives stable image URLs. Each also grows the repository, needs write access to contents, and needs a retention policy. The optional base URL lets a team make that choice itself.
- **Publish `report.html` to GitHub Pages.** Pages would show the whole report in a browser. It makes run output public by default for many repositories, and it needs a cleanup policy per pull request, so it stays a choice for a later item. The base URL reaches the same result for a team that has set it up.
- **Cap the device log inside Bajutsu while it records.** A cap at capture would bound every consumer, including the zip export of [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md). It changes the recorder, which this item leaves alone, so it belongs in its own item.
- **Drop `device.log` from the zip.** The zip would stay small, but the log is the evidence that explains a crash. A tail keeps the part that carries the failure.
- **Accept a duplicate upload next to the existing `bajutsu-e2e` action.** Every iOS job would then store the run twice. The `upload` and `artifact-url` inputs let such a job link the first upload instead.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Spike: upload a screenshot and a video with `archive: false`, and record whether a signed-in reader sees them displayed or downloaded. Also record whether re-running a job conflicts with the artifact names of the previous attempt, and add an `overwrite` input if it does.
- [ ] Renderer: `manifest.json` to Markdown, with escaping, truncation, and the no-manifest case that reads `<sid>/result.json`, in a standard-library script and unit tests that run in `make test`.
- [ ] Comment client: find-or-update the sticky comment with paging, plus the job-summary write and the warn-and-exit-zero path.
- [ ] Size control: the staged copy with log truncation, the `size-warn-mb` guard, `retention-days`, and `compression-level`, with unit tests for the truncation, the guard, and truncation without a manifest.
- [ ] Existing action output: `bajutsu-e2e` gains an `artifact-url` output that forwards `actions/upload-artifact`'s own, so a job can pass it to `post-evidence` with `upload: false`.
- [ ] Composite action: input wiring through `env:`, the whole-run zip upload, the six non-zipped upload slots, the `upload` and `artifact-url` inputs, and the optional `evidence-base-url`.
- [ ] Gate coverage: the script passes `ruff` and `mypy --strict` under `make check`: `make typecheck` adds `.github/actions/post-evidence`, and the unit tests import the script from that path.
- [ ] Documentation: a `docs/reporting.md` section and its `docs/ja/` mirror.
- [ ] Adoption: the web E2E workflow of this repository calls the action.

## References

- [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md): publishes a run-set group as a GitHub check run, without evidence.
- [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md): the CTRF export of a run.
- [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md): uploads a run's evidence to object storage.
- [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md): the precedent for a delivery that transports a fixed verdict and never fails the run.
- [BE-0123](../BE-0123-composite-action-input-indirection/BE-0123-composite-action-input-indirection.md): routes composite-action inputs through `env:`.
- [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md): exports a run as a zip that `report.html` works from offline.
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md): redaction at the artifact boundary.
- [`docs/reporting.md`](../../docs/reporting.md): the `manifest.json` schema the action reads.
- [GitHub changelog, non-zipped artifacts](https://github.blog/changelog/2026-02-26-github-actions-now-supports-uploading-and-downloading-non-zipped-artifacts/): the `archive: false` upload.
