**English** · [日本語](BE-XXXX-post-evidence-gh-attach-ja.md)

# BE-XXXX — Attach a run's screenshots and videos to a pull request or issue comment through gh

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-post-evidence-gh-attach.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2532](https://github.com/bajutsu-e2e/bajutsu/pull/2532) (units 2-7) |
| Topic | Integration with external services |
| Related | [BE-0503](../BE-0503-local-evidence-post/BE-0503-local-evidence-post.md), [BE-0504](../BE-0504-pr-evidence-comment-action/BE-0504-pr-evidence-comment-action.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a third sink, `--sink attach`, to `scripts/post_evidence.py`, the helper that
[BE-0503](../BE-0503-local-evidence-post/BE-0503-local-evidence-post.md) ships. The sink hands the
failing screenshots and the failing scenarios' videos to the GitHub CLI (`gh`) through its
`--attach` flag. GitHub then hosts the files, and the comment shows each screenshot inline and plays each
video at the end of the comment. The sink also uploads the whole run as a zip and links it, so a
reviewer who wants more than the previews opens the zip. The script's unit stays the run: it
uploads no standalone file.

The script still posts after the verdict and never changes it. It still uses the standard library
alone. The only addition is running `gh` as a subprocess, for this sink alone.

## Motivation

BE-0503 posts a comment of text and links, because it assumed that GitHub offers no documented way
to put an image into a comment. That assumption no longer holds. The help text of `gh pr comment`
and `gh issue comment` in `gh` 2.105.0 documents `--attach`, which uploads an image or a video and
rewrites a reference in the comment body to the uploaded file. A public write-up of the flag adds
details that the help text does not carry. A spike on a throwaway repository measured most of them,
and the section on measured behavior below records what it found and what it could not reach.

The two existing sinks each leave a gap. The `none` sink needs a copy that the developer hosts. The
`release` sink uploads assets that a browser downloads rather than displays, and a draft release
returns 404 to anyone without write access. A spike on a draft release recorded both facts.

The observable difference is the comment itself. After `--sink attach`, a failing screenshot appears
inline in the pull request, and a reviewer sees a failure without opening a link. A test can check
the mechanism behind it: the `gh` command line that the script builds carries one `--attach` for
each staged file, and the body references each file at the place where it belongs.

## Detailed design

### The sink

`--sink attach` joins `none` and `release`. For each failing scenario, up to `--max-failures`, the
script stages the failing step's screenshots and the scenario's videos in a temporary directory,
beside a body file that holds the rendered Markdown. The body comes from the same renderer as the
other sinks, with each file's link set to `./<name>`. The script gives each staged file a unique
name, so two scenarios never collide.

A screenshot is passed as `--attach './name#alt'`, and the body references it as `![alt](./name)`.
A video is passed as `--attach './name'` and is never referenced in the body. The spike found that
`gh` refuses an alt text on a video, and that it rewrites a video reference into a plain link, which
GitHub does not play. An unreferenced video is appended as a bare URL at the end of the comment,
and that form renders as a player. The comment therefore names each video by its path under the
scenario, and the videos play together at its end.

`gh --attach` takes media alone, and the spike showed that it refuses a zip before it posts. The
zip of the run therefore goes up as an asset of the release `bajutsu-evidence-<number>`, with the
rules of the `release` sink in BE-0503: a draft prerelease that never becomes the latest release,
`--publish` as an opt-in, and a plan that lists the workflows publishing would fire. The script
uploads the zip first and posts the comment second, so a failed upload posts nothing. The comment
links the zip as `Whole run`. A zip above `--size-warn-mb` is skipped with a note, and the comment
then carries no link. A device log or a JSON file never goes up on its own. The release needs
write access to repository contents, and the draft opens for people with write access alone.

### The `gh` call

The script chooses the subcommand from the target. `GET /repos/<owner>/<repo>/issues/<number>`
answers for a pull request and an issue alike, and the reply carries a `pull_request` key for a
pull request. The script then runs `gh pr comment` or `gh issue comment` with `--repo`, `--body-file`,
and one `--attach` per file.

Three rules keep `gh` on the instance and the files that the plan showed:

- The script passes `--repo github.com/<owner>/<repo>` and builds the child environment with
  `GH_TOKEN` set and `GH_HOST`, `GH_ENTERPRISE_TOKEN`, `GITHUB_TOKEN`, and `GITHUB_ENTERPRISE_TOKEN`
  removed. The token never appears on a command line, in the plan, or on disk.
- The script runs `gh` with the staging directory as its working directory, so `./name` means the
  same file in `--attach` and in the body.
- The sink supports `github.com` alone. When `GITHUB_API_URL` names another host, the script exits
  before any request. A missing `gh`, or a version older than the floor, exits non-zero with the
  reason before any write.

### Guards

The help text states one limit: a command takes at most 50 files. The spike confirmed that `gh`
rejects a file of another type, and that it rejects an image above 10 MB, both before it posts
anything. An 11 MB video went through on the test account, so the video cap depends on the owner's
plan. The script keeps 10 MB as its default (`--attach-video-mb`, 1 to 100) and skips the 50-file
excess, any file of another type, any image above 10 MB, and any video above its limit. The plan
and the comment name every skip. The version floor of 2.99.0 comes from the public write-up, and
the spike could not measure it: only `gh` 2.105.0 was at hand.

### The sticky comment

`gh` creates a comment. Through `--edit-last`, it edits only the current user's last comment, which
another tool may own. The spike showed that `--edit-last` composes with `--attach`, so it can serve
as a later optimization when this key's comment is the last one. The script posts the new comment
first and deletes the older ones afterward, because that works wherever the old comment sits.

Deletion follows four rules:

- The script takes a snapshot before it posts: the comments of the same key and the same author, by
  the marker rule of BE-0503. It deletes exactly those ids, and it never queries again.
- It deletes through `DELETE /repos/<owner>/<repo>/issues/comments/<id>` with its own REST client,
  never through `gh --delete-last`, which would remove the comment that `gh` just created.
- It skips any id that matches the URL that `gh` printed for the new comment.
- A delete that fails does not stop the others. The script reports each failed id and the error, and
  it exits non-zero. The new comment has posted, and the next run lists the remaining ones again.

A rerun therefore moves the comment to the end of the thread and notifies subscribers once more. The
plan lists each comment it will delete, and the confirmation covers the deletion.

When `gh` exits non-zero, the script deletes nothing and exits non-zero. It prints the error of `gh`
and warns that the comment may exist with some attachments, and that uploaded files may already be
reachable by URL. The spike measured one case: `gh` checks every file before it posts, so an
unsupported file, a missing file, or an oversize image creates no comment at all. A failure during
the upload itself could not be provoked, so the warning stays.

### Privacy

In a public repository, an uploaded file answers an anonymous request with `200` and its media type,
and no attachment disposition. Anyone with the URL can see it. The plan states that line for a
public repository, and the confirmation exists for that reason. `--yes` accepts that exposure.

Deleting a comment removes the comment and keeps its files. The spike deleted a comment and fetched
its image anonymously afterward: the request still answered `200`. The plan says so next to the
public-repository line, so the sticky comment's deletion never suggests that older evidence is
gone. Redaction applies to the text of a run, not to pixels, as BE-0503 states.

### Measured behavior

The spike ran on a throwaway public repository, with `gh` 2.105.0 and a token given only through
`GH_TOKEN`, against an empty `gh` configuration directory. It used dummy files alone.

| Question | Result |
|---|---|
| Does `gh` work with `GH_TOKEN` alone? | Yes, for `gh issue create` and `gh issue comment`. |
| Does the marker stay the first line of the body? | Yes. |
| How is an image reference rewritten? | `![alt](./name)` becomes `![alt](<hosted URL>)`, and the page shows the image. |
| How is a video reference rewritten? | `![v](./name)` and `[v](./name)` become a plain link, which is not a player. A bare path is not rewritten. |
| How does a video play? | An unreferenced video is appended as a bare URL, and that renders as a player. |
| Does an alt text on a video work? | No: `gh` refuses it before it posts. |
| Which types and sizes does `gh` take? | It lists png, jpg, jpeg, gif, webp, svg, mp4, mov, and webm. It refuses an image above 10 MB. |
| What happens to an unsupported file? | `gh` refuses before it posts, and no comment is created. |
| Does `gh --attach` take a zip? | No: it refuses the file before it posts. |
| Does `--edit-last` compose with `--attach`? | Yes. |
| Who can open an uploaded file? | Anyone, in a public repository. |
| Do the files outlive a deleted comment? | Yes. |

Not measured, for lack of the means: the 2.99.0 version floor, GitHub Enterprise Server, a video cap
on a free plan, and a failure that happens during the upload.

### Tests and documentation

The tests put a fake `gh` executable on `PATH` and reuse the fake GitHub server of BE-0503. They
check the command line, the staged files, the subcommand choice, the version and host refusals, each
guard, the deletion after success, no deletion after failure, and a run directory left unchanged.
`docs/reporting.md` and its `docs/ja/` mirror gain the sink.

## Alternatives considered

- **The upload endpoint that the browser uses.** It is undocumented and can change without notice,
  and BE-0503 rejected it for that reason. `gh --attach` is the documented route to the same result.
- **Keep only the `release` sink.** A release downloads and does not display, and a draft returns 404
  to a reviewer without write access. The sink stays for the zip and for hosts that `gh --attach`
  does not support.
- **Edit the last comment with `--edit-last`.** It edits only the current user's last comment, which
  another tool may own. A later item can use it as an optimization once the spike confirms that it
  composes with `--attach`.
- **Write the upload protocol into the script.** No documented API exists, and the script would
  then copy an undocumented one.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Spike: on a throwaway thread, measure what this item takes from the help text and the public write-up. Confirm that `gh pr comment --attach` and `gh issue comment --attach` work with a token given only through `GH_TOKEN`. Record the version floor, the accepted types, and the size caps. Record how `gh` rewrites a video reference, and whether `--edit-last` composes with `--attach`. Check that the marker stays the first line after the rewrite, what a partial failure leaves behind, and whether files outlive a deleted comment.
- [x] CLI and plan: `--sink attach`, `--attach-video-mb`, and the plan's lines for files, skips, deletions, and privacy.
- [x] Staging and guards: media types, size caps, the file cap, unique staged names, and the body references.
- [x] The `gh` call: the version check, the host refusal, the subcommand choice, the child environment and working directory, and the error report that covers a partial failure.
- [x] The sticky comment: the snapshot, the deletion by id after a successful post, and the report of a failed delete.
- [x] Tests: a fake `gh` and the fake GitHub server of BE-0503.
- [x] The run zip: the zip as an asset of the evidence release, uploaded before the comment, with the size guard and `--publish`.
- [x] Documentation: a `docs/reporting.md` section and its `docs/ja/` mirror.

Log:

- [#2532](https://github.com/bajutsu-e2e/bajutsu/pull/2532) — Added the proposal, the `attach` sink in `scripts/post_evidence.py`, its tests with a fake `gh`, and the documentation in both languages. The spike then ran on a throwaway repository, and the item records what it measured. It changed how videos are posted: they are attached unreferenced and play at the end of the comment. The same PR then added the run zip as a release asset, because `gh --attach` refuses a zip.

## References

- [BE-0503](../BE-0503-local-evidence-post/BE-0503-local-evidence-post.md): the script this item extends.
- [BE-0504](../BE-0504-pr-evidence-comment-action/BE-0504-pr-evidence-comment-action.md): the Action that renders through the same script.
- The help text of `gh pr comment --attach` and `gh issue comment --attach` (`gh` 2.105.0), the source of the flag, the 50-file limit, the alt-text syntax, and the body rewrite rule.
- The help text of `gh issue create --attach`, the source of the partial-failure behavior that this item infers for comments.
