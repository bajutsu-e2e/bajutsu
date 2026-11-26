**English** · [日本語](BE-0504-pr-evidence-comment-action-ja.md)

# BE-0504 — Post run evidence to a pull request or issue with a portable GitHub Action

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0504](BE-0504-pr-evidence-comment-action.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0504") |
| Implementing PR | [#2530](https://github.com/bajutsu-e2e/bajutsu/pull/2530) (all units except the spike) |
| Topic | Integration with external services |
| Related | [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md), [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md), [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md), [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a composite GitHub Action, `post-evidence`. It posts the result of a finished
`bajutsu run` to a pull request or an issue. The comment names each failing scenario, the step that
stopped it, and the reason. It links to the evidence that the action uploads as GitHub Actions
artifacts. One artifact holds the whole run as a zip. The action also covers the first three failing
scenarios with separate, non-zipped artifacts. Those hold the failing step's screenshot and the
scenario's video, so a browser shows each directly. A rerun updates the same comment and adds no new
one.

The action reads `manifest.json` from Bajutsu. When a run left no manifest, it reads
`<sid>/result.json` instead. It reads nothing else. It installs no Bajutsu package and calls no
Bajutsu command. Any repository that produces a Bajutsu run directory can use it with one `uses:`
line.

## Motivation

Today a reviewer who sees a red Bajutsu check on a pull request has a long path to the evidence. The
reviewer opens the Actions run, finds the job, and opens its artifacts. The reviewer then downloads a
zip, unpacks it, and opens `report.html`. Most reviewers stop before the last step. The reason for
the red result sits in the job log. The screenshot that explains it sits inside a zip.

[BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md) improves this for teams
that run `serve`. It offers merely a check that lists failing scenarios and reasons. The check
carries no screenshot, video, or report link. It also needs a `serve` deployment and a GitHub App.
[BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md) exports a test table that
third-party reporters can render. No path attaches the evidence itself to the pull request.
[BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) uploads evidence to object
storage. Yet nothing turns the uploaded files into something a reviewer sees on the pull request.

A zip of the whole run also grows without a bound. Bajutsu streams the device log straight to
`device.log` and never caps it. The source is `simctl log stream --level debug` on iOS and `logcat`
on Android. The report embeds the last 2,000 lines alone, yet the file on disk keeps every line.
Video does not shrink in a zip, because MP4 and WebM files already use compression. A zip artifact
cannot close the gap by itself.

GitHub offers no in-browser view of a zip's contents. The `report.html` page carries its Cascading
Style Sheets (CSS) and JavaScript inline. It refers to its screenshots and videos by relative path.
A page uploaded alone opens with broken images. A single image or video file has no such problem,
because a browser displays it natively.

The observable difference is the pull request page. After a failing run, one comment appears. It
states which scenarios did not pass and why. A reviewer clicks a screenshot link and sees the image.
A video link plays the video, with no download and no unzip. The whole run stays one click away as a
zip. A rerun edits the comment in place. The conversation holds one comment per job, never one per run. A reader still needs read access to the repository. Artifact Uniform Resource Locators (URLs)
require sign-in.

## Detailed design

### Where the action lives and what it needs

The action lives at `.github/actions/post-evidence/` in this repository. Another repository uses it
as `bajutsu-e2e/bajutsu/.github/actions/post-evidence@<ref>`. Its renderer is one Python script
that uses the standard library alone. The action runs the script with `actions/setup-python`, pinned
to the repository's Python (3.13). It sets `update-environment: false`. It then invokes the script
through the step's `python-path` output. The action never uses the runner's system Python, and the
calling job's own Python stays unchanged. The action needs no `uv sync` and no checkout of Bajutsu.

The calling job grants `pull-requests: write` for a pull request, or `issues: write` for an issue.
The action never needs `pull_request_target`. That trigger grants a write token to the job. To run a
fork's scenarios, the job must check out and run the fork's code. The documentation names the trigger
as unsupported for that reason. Every input reaches a shell step or the script through `env:`. No input passes through an inline
`${{ }}`
([BE-0123](../BE-0123-composite-action-input-indirection/BE-0123-composite-action-input-indirection.md)).

### Inputs and outputs

| Input | Default | Meaning |
|---|---|---|
| `run-dir` | `runs` | Either one run directory or a root that holds more than one. Given a root, the renderer reads every `*/manifest.json` directly under it and renders one section per run. |
| `artifact-name` | `${{ github.workflow }}-${{ github.job }}` | Name of the zip artifact, and the prefix of the non-zipped ones. A matrix job must add its matrix values, because one workflow run cannot hold two artifacts with the same name. Before uploading, the action replaces each character that `actions/upload-artifact` does not accept (`" : < > \| * ? \ /`, CR, LF) with `-`. |
| `retention-days` | `14` | Days GitHub keeps every artifact the action uploads. The value ranges from 1 to 90, and a smaller value frees storage sooner. |
| `compression-level` | `6` | Zlib level of the whole-run zip, from 0 to 9. A run made up chiefly of video barely shrinks at any level, so `0` uploads it faster. |
| `log-tail-kb` | `1024` | A `deviceLog` file larger than this keeps its last `log-tail-kb` kilobytes alone in the zip. `0` keeps every log whole. |
| `size-warn-mb` | `500` | When the uncompressed staged content exceeds this size, the action skips the whole-run upload and the comment warns. `0` disables the guard. |
| `upload` | `true` | When `false`, the action uploads nothing and links `artifact-url` for the whole run. Use it after a step, such as the existing `bajutsu-e2e` action, that already uploaded `runs/`. The comment then names screenshots and videos by path and links no per-file artifact. With `false`, the action stages nothing, so log truncation, `size-warn-mb`, `retention-days`, and `compression-level` do not apply, and the comment makes no truncation claim. Every path reads `<runId>/<name recorded in the manifest>`, which is the layout of an upload of `runs/`. An empty `artifact-url` links to the workflow run. |
| `artifact-url` | empty | URL of the artifact to link when `upload` is `false`. |
| `issue-number` | the event's pull request number | Pull request or issue to comment on. Outside a pull request event it is optional. Without it, the action writes the job summary alone and posts no comment. |
| `github-token` | `${{ github.token }}` | Token that writes the comment. |
| `comment-key` | `${{ github.workflow }}-${{ github.job }}` | Key of the sticky comment. Two jobs with different keys keep two comments. A matrix job must add its matrix values, or each leg overwrites the comment the previous leg wrote. |
| `comment-author` | `github-actions[bot]` | Login of the account that owns the sticky comment. Set it to the App's bot login when `github-token` belongs to a GitHub App. |
| `max-failures` | `10` | Failing scenarios listed in full. The comment counts the rest. |
| `evidence-base-url` | empty | Optional base URL that serves the run directory over Hyper Text Transfer Protocol (HTTP). See [Full fidelity through an evidence base URL](#full-fidelity-through-an-evidence-base-url). |

The action exposes one output, `artifact-url`, the URL of the whole-run artifact.

### What the action does

1. **Select.** The script reads `manifest.json` and picks the files to upload. It picks files for
   each of the first three failing scenarios. These are the failing step's screenshot and the
   scenario's video. The script writes their paths as step outputs.
2. **Stage and upload.** The action stages a copy of `run-dir` in the runner's
   temporary directory. [Keeping the zip small](#keeping-the-zip-small) describes the copy. Every entry in the copy reads
   `<runId>/<name recorded in the manifest>`. The rule holds whether `run-dir` names one run or a
   root of more than one. The action uploads that copy as one zip artifact with
   `actions/upload-artifact`. It leaves `overwrite` at its default of `false`. Two matrix legs with
   the same name then conflict visibly in the job log and do not replace each other. A non-zipped
   upload takes one file per artifact (`archive: false`), and a composite action cannot repeat a
   step. For that reason the action has six fixed upload steps. They
   form three screenshot slots and three video slots. Each slot runs when the select step filled it, and not otherwise.
3. **Render and publish.** The script renders Markdown and writes it to the job summary.
   A run with no pull request still shows it. When an `issue-number` resolves, the script looks for the
   sticky comment. It takes a comment's first line and strips trailing blanks and a carriage return.
   That line must equal the marker `<!-- bajutsu-evidence:<comment-key> -->`. GitHub may normalize line endings to Carriage Return and Line
   Feed (CRLF) after a manual edit. An exact comparison would miss such a comment. The script would then post a duplicate. The script matches comments from the
   `comment-author` login, and no others. It pages through every comment on the
   issue (`per_page=100`), so a long conversation cannot hide the marker. It updates the matching
   comment, or creates one when none exists.

The rendered comment has this shape. The header gives the verdict and the counts of passing and
failing scenarios. It also gives the commit Secure Hash Algorithm (SHA)
value (`github.sha`). A link points to the workflow run. Under the header, each failing scenario gets a block with its name. The block shows
the index and action of the first failing step, and the step's reason. When `steps` holds no failing
entry, the block shows the scenario-level `failure` summary instead. It then names no step or
screenshot. A block links the screenshot and the video through
non-zipped artifacts. A scenario outside the first three gets no
such links. It names the screenshot and the video by their path inside the
whole-run artifact.
That path reads `<runId>/<name recorded in the manifest>`.

The renderer puts each scenario name, reason, and artifact path inside a code span. Text that spans
lines goes in a fenced block. The renderer delimits a code span with a backtick run. That run is one longer than the
longest run inside the text, and a space pads it. A fenced block uses a fence longer than any backtick
run inside. Together, the code span and the fence stop app text from adding a mention. They also stop it
from adding Hyper Text Markup Language (HTML). The renderer also strips `<!--` and `-->` again and again until none remain. The text then cannot
forge a second marker.

The script reads fields that the manifest already records, and no others. The comment omits a screenshot or video that the manifest does not list. The omission is not an
error. The renderer
pins the lowest `schemaVersion` of `manifest.json` that it understands. It compares that pin with the
file's own value. A lower or unrecognized version makes the comment say so. A future schema bump may land in `bajutsu/common/report/manifest.py`.
The comment then shows a visible notice, never an empty block.

### Keeping the zip small

These measures apply when `upload` is `true`. Bajutsu does not cap `device.log` while it records, so
the action caps the log in the staged copy. Bounding the log at capture is a change to Bajutsu
itself, which this item avoids. A separate item can add it.

- **Truncate an oversize log.** The staged copy looks at each artifact of kind `deviceLog`. The
  `manifest.json` file lists those artifacts, or `<sid>/result.json` does when no manifest exists.
  The copy replaces each one that exceeds `log-tail-kb` with its last `log-tail-kb` kilobytes. The
  first line of the truncated file names the original size and the kept size. The comment states
  that the action truncated a log. The action uploads the full log nowhere, so the log vanishes when
  GitHub discards the job's runner. The truncation keeps the end of the log, because a red run leaves
  its trace near the end.
- **Warn before an upload that cannot fit.** The script sums the staged files uncompressed. When the
  sum exceeds `size-warn-mb`, it skips the whole-run upload. The comment names the total and the
  threshold, and still links the small per-file artifacts. This guard protects the shared storage
  allowance of the repository owner. The allowance counts every artifact and fails later uploads once
  full. The sum uses uncompressed sizes, so it overstates a log-heavy run whose zip compresses well.
  A team that keeps whole logs with `log-tail-kb: 0` raises `size-warn-mb` or sets it to `0`. The
  default threshold is a cautious guess and not a GitHub limit. While writing this item,
  we could not confirm two GitHub limits from the official pages. Those are the per-artifact cap
  and the per-plan allowances. A team tunes
  the threshold to its plan. The whole-run upload may not
  happen or succeed. The comment then lists no path inside it. The `artifact-url` output then stays empty.
- **Shorten retention.** `retention-days` defaults to 14. The default of GitHub is 90
  days. Evidence from a pull request loses value after the merge.
- **Tune compression.** `compression-level` stays at the default of 6 because logs compress well. A
  team whose runs consist chiefly of video sets it to `0` for a faster upload at no size cost.

Truncation never edits `manifest.json`. A reader who opens the unpacked `report.html` sees the log
link point to the shortened file. The comment is the place that says so.

### Viewing the evidence in a browser

A browser natively displays a non-zipped Portable Network Graphics (PNG), MP4, or WebM file. It needs
no JavaScript to do so. That property makes the per-file artifacts the main path for a quick look.
GitHub's documentation does not say how every browser handles a non-zipped artifact. A browser may
display the file or download it. For that reason the first work item is a
spike. The spike uploads a
screenshot and a video with `archive: false`. It records what a signed-in reader sees. If a file type
downloads instead of displaying, the link still delivers the file. The documentation states the
result.

The action does not upload `report.html` as a non-zipped artifact. Its relative image and video paths would
break, as the Motivation describes. A reader who wants the full report downloads the whole-run
zip. The reader unpacks it and opens `report.html`. That page works offline
([BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)).

### Size and error behavior

GitHub caps a comment near 65,000 characters. The renderer stops listing scenarios after `max-failures`.
The count spans every run under `run-dir`. The renderer states the omitted count. It
cuts the whole body at a fixed length below the cap. It also truncates each reason to a fixed length.

A run directory may hold no `manifest.json`. The script then reads each `<sid>/result.json`, which
Bajutsu writes the moment a scenario ends. Those files list the scenarios that finished and the
`deviceLog` artifacts to truncate. The script still stages and uploads the whole run. A timeout or an
abnormal exit can stop a run partway. Such a run leaves no manifest, because Bajutsu writes it after
every scenario finishes. The comment states that the run did not complete and links to the workflow
run. An incomplete run thus never leaves an earlier verdict standing. A root may hold more than one run. The
comment then names every directory without a manifest and never skips one.

Every upload step runs with `continue-on-error: true`. An unsuccessful upload degrades to a warning
like any other problem. The comment then states that the evidence upload did not succeed. It
links to the workflow run instead of an artifact. The action never changes a job's result. The test
step's own exit code decides pass or fail, as it does for every Bajutsu run.

The token may lack
write permission. A pull request from a fork gets a token without write access. The
Application Programming Interface (API) call may also not succeed. In both cases the script prints a
warning, writes the summary alone, and exits zero. No Large Language Model (LLM) runs anywhere in the
action. The comment transports a verdict that the deterministic run already fixed.

### Secrets

Truncating a log does not mask it, and the staged copy keeps whatever the log already holds. The
action posts text that `manifest.json` already carries. The run masks that text before it writes the
manifest
([BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md)). The
action adds no source of text, such as the device log or `network.json`. A comment never embeds file
contents. The artifacts keep the access rules of the repository. A reader needs read access to open or
download them. The screenshots and videos are the app's own pixels, which text masking does not
cover. A team that cannot upload them sets `upload: false` and keeps its own handling.

### Full fidelity through an evidence base URL

Artifact links cannot render an image inside a comment, because the URL needs sign-in. A team that
also serves the run over HTTP can close the gap. Setting `evidence-base-url` changes what the
renderer emits. A screenshot appears as `![](<base>/<path>)` and a video as a link. The renderer also
links to `<base>/report.html`. All three sit under the same base. The renderer percent-encodes each
path segment before joining it to the base. The action never uploads to that location. A job that
already ran [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md)'s upload, with
public or signed URLs, supplies the base. The input carries no credential. A signed URL that expires
leaves a broken image after expiry, which the documentation states.

### Documentation and adoption

`docs/reporting.md` and its `docs/ja/` mirror gain a section that shows a copy-paste workflow. The
run step comes first, then `post-evidence` with `if: always()`. This repository adopts the action in
its web E2E workflow as the first user. CI thus exercises the comment shape on real red results.

## Alternatives considered

- **A `bajutsu` subcommand that posts the comment.** A subcommand would reuse the report
  code directly. It would also work outside GitHub Actions. We rejected it because the stated goal is a
  portable form that changes nothing in Bajutsu. A subcommand would also force every consuming job to
  install Bajutsu merely to post a comment.
- **Extend [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md).** The check
  already runs on `serve`, so adding evidence links there is natural. We kept the two separate.
  BE-0500 needs a `serve` deployment and a GitHub App. This action needs neither. The
  two can share the Markdown renderer later, and that sharing is out of scope here.
- **A reporter action over `ctrf.json`.** The file uses the Common Test Report Format (CTRF).
  Reporters
  already render a table of results from the file of
  [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md). Each reporter differs in
  how it presents attachments. None promises to link a red result to a screenshot inside the
  artifact. We chose a renderer that we control, and a team can still use both.
- **Upload `report.html` alone as a non-zipped artifact.** The page would open.
  Its images and videos would not. Their relative paths resolve to nothing. We have also not verified
  whether GitHub runs the inline JavaScript of an artifact.
- **A single-file `report.html`.** It would embed images as `data:` URIs. That form would need a change to the
  Bajutsu report, which this item avoids. Videos are too large to embed, and the JavaScript question
  above applies as well. A later item can revisit it if the spike shows that artifact pages run
  scripts.
- **Embed screenshots in the comment as `data:` URIs.** GitHub's sanitizer strips them. The
  comment size cap would not hold them anyway.
- **A third-party artifact viewer.** One example is the artifact.ci GitHub App. It gives browsable
  links to a whole artifact. It also hands the run's screenshots and videos to a third party. We keep
  it out of the default and leave the choice to each team.
- **Commit evidence to an orphan branch or a release.** Either path gives stable image URLs. Each
  also grows the repository, needs write access to contents, and needs a retention policy. The
  optional base URL lets a team make that choice itself.
- **Publish `report.html` to GitHub Pages.** Pages would show the whole report in a browser. By
  default, run output would then be public in a large share of repositories. It also needs a cleanup
  policy per pull request, so it stays a choice for a later item. The base URL reaches the same
  result for a team that has set it up.
- **Cap the device log inside Bajutsu while it records.** A cap at capture would bound every
  consumer. That includes the zip export of
  [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md). It changes the
  recorder, which this item leaves alone. It belongs in its own item.
- **Drop `device.log` from the zip.** The zip would stay small. Yet the log is the evidence that
  explains an abnormal exit. A tail keeps the part that carries the trace.
- **Accept a duplicate upload.** The existing `bajutsu-e2e` action already uploads the run. Every
  iOS job would then store it twice. Instead, such a job can link the first upload through the
  `upload` and `artifact-url` inputs.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Spike: upload a screenshot and a video with `archive: false`. Record whether a signed-in reader sees them displayed or downloaded. Also record whether a job rerun conflicts with the previous artifact names. Add an `overwrite` input if it does.
- [x] Renderer: `manifest.json` to Markdown. It covers escaping and truncation. It also covers the no-manifest case that reads `<sid>/result.json`. The script uses the standard library alone. Its unit tests run in `make test`. Bajutsu's own manifest writer generates the fixtures. A schema change thus breaks the tests in its own PR.
- [x] Comment client: find-or-update the sticky comment with paging. It also covers the job-summary write and the warn-and-exit-zero path.
- [x] Size control: the staged copy with log truncation and the `size-warn-mb` guard. It also covers `retention-days` and `compression-level`. Unit tests cover the truncation, the guard, and truncation without a manifest.
- [x] Existing action output: `bajutsu-e2e` gains an `artifact-url` output. It forwards the one from `actions/upload-artifact`. A job can pass it to `post-evidence` with `upload: false`.
- [x] Composite action: input wiring through `env:` and the whole-run zip upload. It also covers the six non-zipped upload slots. Its inputs include `upload`, `artifact-url`, and the optional `evidence-base-url`.
- [x] Gate coverage: the script passes `ruff` and `mypy --strict` under `make check`. `make typecheck` adds `.github/actions/post-evidence`. The unit tests import the script from that path. `make test` measures it (`--cov`). The per-file floor of BE-0385 in `coverage-floors.json` holds it to the same bar as `bajutsu/`.
- [x] Documentation: a `docs/reporting.md` section and its `docs/ja/` mirror.
- [x] Adoption: the web E2E workflow of this repository calls the action.

Log:

- [#2530](https://github.com/bajutsu-e2e/bajutsu/pull/2530) — added the `post-evidence` composite
  action. It holds the standard-library renderer and sticky-comment client. It also holds log
  truncation, the size guard, and the zip and six non-zipped upload slots. The PR adds the
  `artifact-url` output of `bajutsu-e2e`, gate coverage, docs, and adoption in the web E2E job. The
  spike needs a real GitHub run and stays open.

## References

- [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md): publishes a run-set group as a GitHub check run, without evidence.
- [BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md): the CTRF export of a run.
- [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md): uploads a run's evidence to object storage.
- [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md): the precedent for a delivery that transports a fixed verdict. It never turns the run red.
- [BE-0123](../BE-0123-composite-action-input-indirection/BE-0123-composite-action-input-indirection.md): routes composite-action inputs through `env:`.
- [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md): exports a run as a zip that `report.html` works from offline.
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md): masking at the artifact boundary.
- [`docs/reporting.md`](../../docs/reporting.md): the `manifest.json` schema the action reads.
- [GitHub changelog, non-zipped artifacts](https://github.blog/changelog/2026-02-26-github-actions-now-supports-uploading-and-downloading-non-zipped-artifacts/): the `archive: false` upload.
