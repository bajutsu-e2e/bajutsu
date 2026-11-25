**English** · [日本語](BE-XXXX-post-evidence-gh-attach-ja.md)

# BE-XXXX — Attach a run's screenshots and videos to a pull request or issue comment through gh

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-post-evidence-gh-attach.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Integration with external services |
| Related | [BE-0503](../BE-0503-local-evidence-post/BE-0503-local-evidence-post.md), [BE-0504](../BE-0504-pr-evidence-comment-action/BE-0504-pr-evidence-comment-action.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a third sink, `--sink attach`, to `scripts/post_evidence.py`, the helper that
[BE-0503](../BE-0503-local-evidence-post/BE-0503-local-evidence-post.md) ships. The sink hands the
failing screenshots and the failing scenarios' videos to the GitHub CLI (`gh`) through its
`--attach` flag. GitHub then hosts the files, and the comment shows each screenshot inline and each
video as a player. The developer hosts nothing and creates no release.

The script still posts after the verdict and never changes it. It still uses the standard library
alone. The only addition is running `gh` as a subprocess, for this sink alone.

## Motivation

BE-0503 posts a comment of text and links, because it assumed that GitHub offers no documented way
to put an image into a comment. That assumption no longer holds. The help text of `gh pr comment`
and `gh issue comment` in `gh` 2.105.0 documents `--attach`, which uploads an image or a video and
rewrites a reference in the comment body to the uploaded file. A public write-up of the flag adds
details that the help text does not carry: the release that introduced it, the accepted types, the
size limits, and the platforms it supports. This item treats those details as unverified until the
spike measures them.

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

A screenshot is passed as `--attach './name#alt'`. The help text says that a video takes no alt text,
so a video is passed as `--attach './name'` with no `#`. Whether `gh` rewrites the reference to a
video when it appears as `[Video](./name)` or as `![video](./name)` is unmeasured, and the spike
decides it. The fallback is documented: a file that the body does not reference is appended at the
end of the comment.

The sink uploads media alone. A zip, a device log, and a JSON file are out of scope, so the comment
carries no link to the whole run. A developer who wants the zip uses the `release` sink.

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

The help text states one limit: a command takes at most 50 files. The remaining limits come from the
public write-up, and the spike confirms them: the accepted types (PNG, JPEG, GIF, WebP, SVG, MP4,
MOV, WebM), an image cap of 10 MB, a video cap of 10 MB or 100 MB depending on the owner's plan, and
a version floor of 2.99.0. The script skips a file of another type, an image above 10 MB, a video
above `--attach-video-mb` (default 10), and any file past the 50th. The plan and the comment name
every skip. If the spike shows that `gh` rejects such a file by itself, a later item can drop the
matching guard.

### The sticky comment

`gh` creates a comment. Through `--edit-last`, it edits only the current user's last comment, which
another tool may own, and whether `--edit-last` composes with `--attach` is unmeasured. The script
therefore posts the new comment first and deletes the older ones afterward.

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
reachable by URL. The help text of `gh issue create` documents that a partial failure still creates
the issue and prints its URL. This item infers the same behavior for `gh ... comment --attach`, and
the spike measures it.

### Privacy

A public write-up reports that files uploaded to a public repository are visible without signing
in. The plan states that line for a public repository, and the confirmation exists for that reason.
`--yes` accepts that exposure.

Deleting a comment removes the comment. GitHub may keep serving the files that the comment uploaded
at their URLs, which the spike checks. The plan says so next to the public-repository line, so the
sticky comment's deletion never suggests that older evidence is gone. Redaction applies to the text
of a run, not to pixels, as BE-0503 states.

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

- [ ] Spike: on a throwaway thread, measure what this item takes from the help text and the public write-up. Confirm that `gh pr comment --attach` and `gh issue comment --attach` work with a token given only through `GH_TOKEN`. Record the version floor, the accepted types, and the size caps. Record how `gh` rewrites a video reference, and whether `--edit-last` composes with `--attach`. Check that the marker stays the first line after the rewrite, what a partial failure leaves behind, and whether files outlive a deleted comment.
- [x] CLI and plan: `--sink attach`, `--attach-video-mb`, and the plan's lines for files, skips, deletions, and privacy.
- [x] Staging and guards: media types, size caps, the file cap, unique staged names, and the body references.
- [x] The `gh` call: the version check, the host refusal, the subcommand choice, the child environment and working directory, and the error report that covers a partial failure.
- [x] The sticky comment: the snapshot, the deletion by id after a successful post, and the report of a failed delete.
- [x] Tests: a fake `gh` and the fake GitHub server of BE-0503.
- [x] Documentation: a `docs/reporting.md` section and its `docs/ja/` mirror.

## References

- [BE-0503](../BE-0503-local-evidence-post/BE-0503-local-evidence-post.md): the script this item extends.
- [BE-0504](../BE-0504-pr-evidence-comment-action/BE-0504-pr-evidence-comment-action.md): the Action that renders through the same script.
- The help text of `gh pr comment --attach` and `gh issue comment --attach` (`gh` 2.105.0), the source of the flag, the 50-file limit, the alt-text syntax, and the body rewrite rule.
- The help text of `gh issue create --attach`, the source of the partial-failure behavior that this item infers for comments.
