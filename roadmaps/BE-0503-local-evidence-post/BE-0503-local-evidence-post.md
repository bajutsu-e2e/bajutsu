**English** · [日本語](BE-0503-local-evidence-post-ja.md)

# BE-0503 — Post a local run's evidence to a pull request or issue with a helper script

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0503](BE-0503-local-evidence-post.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Rejected** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0503") |
| Topic | Integration with external services |
| Related | [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md), [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md), [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md), [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md), [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md) |
<!-- /BE-METADATA -->

## Introduction

> **Rejected — the evidence belongs in the server's store, linked by URL.**
> GitHub gives a script no documented way to put a run's zip into a comment. `gh --attach` takes
> images and videos alone, and it refuses a zip before it posts. A release asset is the fallback.
> It leaves one release entry for each thread, and a draft release opens for people with write
> access alone. An Actions artifact needs a runner, which a local run lacks. Nothing here waits on
> a blocker, so the item is rejected rather than deferred. A later item persists the finished run
> in the `serve` store and links its URL from the comment. The spike on a draft release did run: the asset API serves each file as an attachment, and a
> draft asset's public link answers 404 without write access.

This item adds a helper script, `scripts/post_evidence.py`, that takes a finished run from a developer's own machine and posts it to a pull request or an issue. The comment names each failed scenario, the failing step, and the reason, and it links to the run's evidence. Running the script again updates the same comment instead of adding a new one. The script uses the developer's own GitHub credentials, and it runs after the verdict, so it never changes what the run decided.

The script is not a Bajutsu feature. It is one file that uses the standard library alone and imports nothing from `bajutsu`. It adds no subcommand and no entry in `[project.scripts]`, and the installed package does not ship it. The portable `post-evidence` GitHub Action, which a sibling proposal defines, today carries its own copy of the renderer under the Action's directory. Reconciling it onto this one file is a work item below, and until it lands the two copies must be kept identical by hand. Once reconciled, a reviewer reads one comment format, whether a runner or a laptop produced the run.

## Motivation

Many Bajutsu runs never reach CI. A developer who changes an iOS flow runs the scenarios against a local Simulator, because the CI lanes cover a fraction of the suite and a device-cloud run costs time and money. The evidence that the change works then sits in `runs/` on that laptop. To show it to a reviewer, the developer exports a zip with `bajutsu export` ([BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)) and drags files into a comment by hand in the browser, one screenshot or video at a time.

The CI-side Action cannot help here, because it needs a workflow run and a runner, and a local run has neither. [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) uploads a run's evidence to object storage, but nothing turns the uploaded files into a comment.

A script suits this need better than a product command, for two reasons. A `bajutsu` subcommand would become a documented CLI contract that the project versions and supports, for a convenience that depends on GitHub's web behavior, which Bajutsu does not control. A single script also travels: a team copies the file, or fetches it by URL, into a repository that has no Bajutsu checkout.

The observable difference is a single command. After `python3 scripts/post_evidence.py runs/<runId> --to owner/repo#123`, the pull request holds one comment that states how many scenarios passed and failed, and names each failure with its reason. Running it again after a fix edits that comment in place. A developer no longer opens a browser to drag files into a comment.

## Detailed design

### The script

```
python3 scripts/post_evidence.py <run-dir | run-id> --to <target>
        [--runs DIR] [--sink none|release] [--publish]
        [--evidence-base-url URL] [--key KEY] [--author LOGIN]
        [--max-failures N] [--log-tail-kb N] [--size-warn-mb N]
        [--dry-run] [--yes]
```

`<run-dir | run-id>` is a path to one run directory, or a run id that the script looks up under `--runs` (default `runs`). `--to` takes `owner/repo#123` or the URL of a pull request or an issue. The script does not accept a bare `#123`. In a fork workflow, `origin` is the developer's fork while the pull request lives upstream, and the two repositories share no numbering. A bare number could send evidence that holds private pixels to the wrong thread, and `--yes` would skip the only check. The script posts. It runs no scenario and calls no AI provider.

`--max-failures` (default 10) caps the failed-scenario blocks, and the comment states how many it omitted.

### Credentials

The script reads a token from `GITHUB_TOKEN` or `GH_TOKEN`. When neither is set and the `gh` CLI is installed, it asks `gh auth token`. It never writes the token to disk, to the run directory, or to a log. A comment needs write access to pull requests or issues. The `release` sink also needs write access to repository contents.

### Where the evidence goes

GitHub has no documented endpoint that uploads an image or a video into a comment. The script therefore posts text and links, and `--sink` chooses what the links point to.

| Sink | What it does | Who can open the links |
|---|---|---|
| `none` (default) | Uploads nothing. The comment names each file by its path inside the run directory, and `--evidence-base-url` turns those paths into links to a copy that the developer hosts. | Whoever can open the base URL. |
| `release` | Uploads the failing screenshots, the videos, and a zip of the run as assets of one release per target, named `bajutsu-evidence #<number>`. The comment links each asset. | A draft release is visible only to people with write access to the repository. `--publish` makes the release public to everyone who can see the repository. |

The default is `none` because an upload is outward-facing and the script should not make one unless the developer asked. A developer who already ran `bajutsu run --evidence-store` ([BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md)) passes the public base URL of the store's prefix (a public bucket path, or a CDN in front of it) as `--evidence-base-url`, and a screenshot then appears inline in the comment. The renderer percent-encodes each path segment before it joins the base, so a run id or a file name holding a space, a `#`, or a `?` still produces a link that resolves. A presigned URL signs one object, so it cannot serve as a base URL. The script does not call the uploader of BE-0110, so it needs no `s3` or `gcs` extra.

A reviewer without write access gets a 404 on a draft release's links. A developer who targets such a reviewer, or an issue opened by an outside contributor, passes `--publish`. The same release serves a pull request and an issue, because both share one numbering space in a repository. The script never deletes a release, and the plan lists the release it reuses so that cleanup stays a visible, manual step. Each release uses the tag `bajutsu-evidence-<number>` and is created with `prerelease: true` and `make_latest: "false"`, so it never becomes the repository's latest release. Publishing creates that tag and fires any workflow that listens for a published release. The script finds an existing release by listing releases and matching the tag name exactly, because the tag endpoint does not return drafts. The plan states whether the reused release is a draft or published. When `--publish` would publish an existing draft, the plan also lists the assets already on it, since they become public too. A spike on a draft release found that the asset API serves each file as an attachment, and that a draft asset's public link answers 404 without write access. Whether a published release shows an image inline stayed unmeasured.

### What the comment holds

The renderer produces the same Markdown as the Action. The header gives the verdict, the counts, and the run id. Each failed scenario gets a block with its name, the first failed step, and the failure reason. The renderer reads `manifest.json`, or `<sid>/result.json` when a run left no manifest. It escapes every string it copies from the run. A scenario name, a failure reason, and an artifact path go inside a code span, or a fenced block when the text spans lines, delimited by a backtick run one longer than the longest run inside it. The renderer strips `<!--` and `-->` repeatedly until none remain, so text from the app cannot add a mention, HTML, or a second marker. The Action's item states the same rule, and the two stay identical.

The sticky comment carries the hidden marker `<!-- bajutsu-evidence:<key> -->` on its first line, compared after stripping trailing whitespace and a carriage return. `--key` defaults to `local`, so a local comment and a CI comment on the same pull request stay separate. `--key` must match `[A-Za-z0-9._-]+` over its whole value, as an anchored match (`re.fullmatch`), because an unanchored `re.match` would accept `local-->` on its prefix. Any other value exits non-zero before any network call, because a `-->` or a newline would close the marker early and post a duplicate on every run.

The script finds its own comment through the login that `GET /user` returns for the token. For a token that has no user, such as a GitHub App installation token, the developer names the login with `--author`.

### Confirming before it uploads

A screenshot or a video can show private data, and a deleted release asset can stay cached. Before any write, the script prints a plan: the target, the repository's visibility, the release it will create or reuse and whether `--publish` will publish it, the tag `bajutsu-evidence-<number>` that publishing creates together with the `release: published` and tag-push workflows in the target repository that the tag would fire, each file, and the total size. It asks for confirmation. `--yes` skips the question for a script. `--dry-run` prints the plan and the Markdown it would post, then stops without any network write.

The script never changes the run directory. It copies the files it will upload into a temporary staging directory, truncates there each oversize `deviceLog` to its last `--log-tail-kb` kilobytes, and uploads from that copy. The run directory keeps its full logs, and the links in `report.html` still resolve. The `release` sink applies `--size-warn-mb` to the staged set as a whole: it skips the zip first, then any single screenshot or video that still exceeds the threshold, so no run uploads an unbounded asset. The `none` sink uploads nothing, so the guard applies to `release` alone. The plan and the comment state every truncation and every skipped file.

The run redacts text when it writes each artifact, including `manifest.json`, `<sid>/result.json`, and the device log ([BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md)). The pixels of a screenshot or a video carry no redaction, and the confirmation exists for that reason.

### Exit status

A developer ran the script to post, so a failure to post exits non-zero with the reason. This differs from `run --evidence-store`, whose upload is a side effect of a run and degrades to a warning. The exit status never reflects the run's verdict. A failed run still posts, and the script exits zero when the post succeeds. No LLM runs anywhere in it.

### Placement, tests, and gate coverage

The script lives in `scripts/`, the repository's home for helper scripts, so `make typecheck` (`mypy bajutsu demos scripts`) and `ruff` check it without a change. A test file imports it as `tests/test_devicefarm_submit.py` imports the Device Farm submitter. The tests run against a fake GitHub HTTP server, so the gate needs no network. The script deliberately does not import `scripts/gh_cli.py`, the shared wrapper through which the roadmap scripts invoke `gh`. That wrapper drives the `gh` CLI, whereas this file must stay copyable alone into a repository with no Bajutsu checkout, and it talks to the REST API with the standard library. Its only `gh` call is `gh auth token`, as a fallback for the token.

The coverage ratchet measures `bajutsu/` alone, so it does not hold this script. Keeping the script outside the package is the point of the item, and the tests carry the cost. [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md) moved the Device Farm core into `bajutsu/` to gain that measurement, and a later item can do the same here if the script grows.

### One script for two entry points

The Action item currently places its script in the Action's directory. The two items reconcile on one file. This item proposes `scripts/post_evidence.py`, which the Action loads from the checkout of its own repository, and the work breakdown names that step.

### Documentation

`docs/reporting.md` and its `docs/ja/` mirror gain a section that shows a local run posted with `--dry-run` first and then for real.

## Alternatives considered

- **A `bajutsu post` subcommand.** A subcommand would reuse the report code directly. We rejected it because it would turn a GitHub-dependent convenience into a versioned CLI contract, and because a team without a Bajutsu checkout could not copy it. A later item can promote the script if it proves stable.
- **A `--post` flag on `run`.** One command would run and post. `run` would then depend on the network and on GitHub credentials, and a run killed partway would never post.
- **Extend `bajutsu export`.** The command already builds a zip. A zip is the wrong unit for a comment, though, because a reviewer wants the failing screenshot and the reason first.
- **The web upload endpoint that the browser uses.** It would put an image inline in a comment. It is undocumented and can change without notice, so a tool that depends on it breaks silently.
- **A draft release as the default sink.** Draft releases are the least visible option. A reviewer without write access would see a 404, so the script makes the developer choose `release` and `--publish` explicitly.
- **Commit the evidence to a branch.** A branch gives stable URLs. It grows the repository and writes to its history, so it stays out of the defaults.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Spike: the draft half ran on a throwaway repository: the asset API serves each file as an attachment, and a draft asset's public link answers 404 without write access. The published half was not pursued, because the item is rejected.
- [ ] Shared renderer: one script that both this item and the Action run, with the Action item's script location reconciled to `scripts/post_evidence.py`.
- [ ] GitHub client: token resolution, `GET /user`, the find-or-update sticky comment with paging, the `--key` check, and `--author`.
- [ ] Sinks: `none` with `--evidence-base-url`, and `release` with `--publish`, the tag scheme, `make_latest: false`, the lookup by tag name, and the draft-or-published state in the plan.
- [ ] Staging and guards: the temporary staging copy, log truncation, the `--size-warn-mb` guard, the confirmation, `--dry-run`, `--yes`, and the exit status.
- [ ] Tests: a fake GitHub HTTP server that the gate runs offline, covering the sticky comment, the CRLF marker match, the `--key` rejection, the no-manifest case, and a run directory left unchanged.
- [ ] Documentation: a `docs/reporting.md` section and its `docs/ja/` mirror.

## References

- [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md): exports a run as a zip.
- [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md): uploads a run's evidence to object storage.
- [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md): the precedent for a delivery that runs after the verdict.
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md): redaction at the artifact boundary.
- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md): the precedent for moving a script's core into `bajutsu/` to measure its coverage.
- [BE-0500](../BE-0500-run-set-github-check/BE-0500-run-set-github-check.md): publishes a run-set group as a GitHub check run.
- [`docs/reporting.md`](../../docs/reporting.md): the `manifest.json` schema the script reads.
