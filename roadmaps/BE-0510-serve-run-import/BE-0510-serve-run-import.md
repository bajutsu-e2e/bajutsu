**English** · [日本語](BE-0510-serve-run-import-ja.md)

# BE-0510 — Import a finished local run into serve and return its URL

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0510](BE-0510-serve-run-import.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0510") |
| Implementing PR | [#2561](https://github.com/bajutsu-e2e/bajutsu/pull/2561), [#2586](https://github.com/bajutsu-e2e/bajutsu/pull/2586) |
| Topic | Hosting the web UI |
| Related | [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md), [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md), [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets a script hand a finished run to `serve`. A new route, `POST /api/runs/import`, receives
the run as a zip. It checks the zip, stores the run where `serve` keeps runs, records it in the run
history, and replies with the run's URL. A small standalone script, `scripts/upload_run.py`, builds the
zip from a run directory and calls the route. The URL then opens the run's report for the members of
the organization.

## Motivation

`serve` persists a run when a worker executed it, or when an administrator's `rescan` finds its tree on
the server's disk
([BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md)). The worker asks for
presigned upload URLs, uploads the tree, and reports the result. `run --evidence-store` uploads
straight to a bucket and leaves no entry in the history
([BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md)). A run that a developer
produced on a laptop has no way in, so its evidence stays on that laptop.

The observable difference is a link. After `python3 scripts/upload_run.py runs/<runId> --server <url>`,
`GET <server>/runs/<runId>/report.html` serves the report to a signed-in member of the organization,
and the run appears in that organization's history. A malformed zip gets `400`, and nothing is stored.

## Detailed design

### Receiving the zip

The route takes a raw `application/zip` body, as `/api/upload` does. It reuses `BoundedZipReceiver` and
the two `_stream_bounded_body` helpers, the stdlib handler's and the FastAPI app's. The route adds its
own cap, `MAX_RUN_IMPORT_BYTES`, of 2 GiB, twice the bundle cap, because screenshots and videos
barely compress. The 4 GiB uncompressed ceiling that `extract_bundle` enforces is a separate bound,
and the cap does not imply it: a 2 GiB body of logs and JSON can cross it. The route reports that
rejection as an oversized run, and it does not reuse the function's zip-bomb wording. Both backends
declare the route, so a test of the route registry covers both.

### Validating the run

The route checks the zip's entry list before it extracts anything. The zip must hold exactly one
top-level directory, named for the run id, and the name must pass `valid_run_id`. That directory must hold a readable `manifest.json`, a JSON object with a boolean `ok` and a list
`scenarios`, and a `scenario.yaml`, which the regeneration below reads. When the manifest carries a
`runId`, it must equal the directory name, so a report never names a different run. The route
refuses an entry named `.deleted`, the tombstone that marks a run as trashed.

The route keeps only the entries whose extension, compared without regard to case, is on a list of
passive types: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.mp4`, `.webm`, `.json`, `.txt`, `.log`,
`.yaml`, and `.yml`. It drops every other entry and lists the dropped paths in its reply, so a real run
always imports. `serve` serves a run's files from its own origin, so a page, an XML document, or an
SVG image that the client uploaded would run with the cookie of whoever opens it, an administrator
included. The opt-in `rawTree` capture on Android writes two XML files, `hierarchy.raw.xml` and
`hierarchy.parsed-input.xml` (`docs/evidence.md`). The route drops them, and their links in the report
answer `404`. `serve` already sends `X-Content-Type-Options: nosniff` on every response
(`gate.HARDENING_HEADERS`). On a hosted object store, though, a run file is a `302` to the store's
signed URL, so no header that `serve` sets reaches the bytes. The allowlist is therefore the whole
defence there, and not a second layer behind a header.

The route also drops the uploaded `report.html`, `junit.xml`, and `ctrf.json`, which every run writes,
and it regenerates all three with `rebake(<staging>/<runId>)` from `bajutsu/common/report/load.py`, the re-render of
[BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md). `rebake` reads `manifest.json` and `scenario.yaml`. An `OSError` or a `ValueError` from it answers `400` and removes the staging directory. The stored copies are therefore always the server's. A hosted store has no render-on-view, and it serves the stored `report.html`. The regeneration therefore runs right after the extraction, in place, while the manifest is still in the tree, and before the upload.

The manifest and each `<sid>/result.json` name artifacts, and the report template puts those names
into `href` and `src` attributes. Every such path must therefore be a relative POSIX path with no
scheme, no leading `/`, and no `..` segment, and it must name an entry that the zip holds. The check
reads the zip's full entry list, before the extension filter above, so an artifact that the filter
drops is not a `400`. Its link answers `404` instead. Any other value answers `400`.

The route then extracts the zip with `extract_bundle` into a fresh staging directory. That call already
rejects zip-slip paths, symlinks, too many entries, and a compression ratio that suggests a zip bomb.
Any failure answers `400` and removes the staging directory.

### Reserving the id

A run id has only second granularity, and the history keys it globally, so two machines, or two
organizations, can mint the same one. The route therefore reserves the id before it places any file. It first purges a stale reservation
of its own organization, then checks the other places that can hold the id, and then reserves. The
stale purge comes first, because an orphan that a crash left would otherwise fail every retry. It
answers `409` when the target prefix already holds objects or a `.deleted` tombstone. Without a
repository, the tree is the history, and the route answers `409` when `runs_dir/<runId>` or
`runs_dir/.trash/<runId>` exists.

The reservation is the `if_absent` insert of `_record_run(..., if_absent=True)`, called directly and
made public for this use, and not `persist_run`, which swallows every exception. `_record_run` gains a
`status` argument, because it writes `done` today. The route reserves with the status `importing`.
After the manifest lands, it flips the row to `done` with a compare-and-set that lands only while
the row is still the `importing` reservation that this request wrote. If the insert reports that a
live row exists, the route answers `409`, with the same body whichever organization holds the id, so
the answer discloses nothing. Only the insert closes the race between two concurrent imports. A
failure while it writes the row answers `5xx`. The route never overwrites a run, and a rename would
need a rewrite of the manifest, so this item does not offer one.

Three changes make that status safe. `list_runs`, on the `Repository` protocol and in
`sql_repository.py`, gains a filter that leaves out `importing` rows, so every history and
flakiness caller inherits it. A crash between the two steps leaves an `importing` row, and
possibly objects under its prefix. The stale purge in the route handles the caller's own
organization. It calls `get_run(run_id)`, and when the row belongs to the caller's organization, has
the status `importing`, and has a `created_at` more than an hour old, it removes the orphaned
objects with `delete_keys(list_keys(<prefix><runId>/))` and calls `purge_run(run_id, org_id=...)`,
so the id is reusable. `get_run` is not scoped to an organization, so the route never purges another
organization's row. A sweep at server start, which may cross tenants, purges the stale
`importing` rows of every organization the same way and removes leftover staging directories.

### Placing the run

On a local file store, the route extracts into a staging directory beside `runs_dir`, on the same
filesystem, and `os.rename`s it into place in one step. On a hosted store, the route works in `<staging>/<runId>/`, where the regeneration has already run. It moves `manifest.json` out of that tree and calls `upload_tree(store, <staging>/<runId>, <the organization's artifact prefix>)`. It then
puts `manifest.json` with `put_file`, so a half-copied run never reads as finished. `upload_tree`
collects its failures and does not raise, so the route treats any entry in the summary's failures as
an error. It then answers `5xx`, removes what it uploaded with `delete_keys(list_keys(<prefix><runId>/))`,
and purges the reserved row with `purge_run(run_id, org_id)`. A failed local placement purges the row
the same way.

### Recording the run

The route sets `provenance.source` to `import` in the stored manifest. Provenance is run-identity
metadata and never part of the verdict, and the mark tells a reader that the client, not a worker,
asserted the verdict. The audit event names the importer: `api-key:<id>`, or the login of a signed-in
editor. The history row takes its organization from the principal, its label from the manifest, and
`ok` from `bool(manifest["ok"])`.

### The reply and the URL

The reply is JSON: `{runId, ok, url: "/runs/<runId>/report.html", archive: "/runs/<runId>/archive.zip"}`.
`serve` knows only its own origin, and it has no setting for a public base URL, so the reply carries
paths. The script joins them to the server address that it was given.

Those routes already exist, and they go through the gate. A reader needs a signed-in session of the
organization. No share link exists for a run, so a reviewer outside the organization cannot open the
URL, and an API key cannot read a run either. The documentation says so.

### Authorization

The route needs a caller with the editor role. `required_role` has an early branch that returns no role
for any `POST` under `/api/runs/`, and `_EDITOR_PATHS` is consulted only after that branch. The route
therefore gets an explicit case, `POST /api/runs/import` as `editor`, placed before the early branch.
The route also goes into the allowlist of organization API keys that the item on organization-scoped
API keys introduces.

### The upload script

`scripts/upload_run.py` uses the standard library alone and imports nothing from `bajutsu`. It takes a
run directory or a run id, builds the zip with the run id as its single top-level directory, and posts
it. It reads the server address from `--server` and the key from the `BAJUTSU_SERVE_KEY` variable or a
`--key-file`. It never takes the key as an argument, because an argument lands in shell history. It
refuses an `http://` server unless the host is loopback, so a key never crosses the network in the
clear. It sends the key as an unredirected header, so a redirect never carries it to another host. It
prints the run's URL and archive URL on two lines. `--dry-run` prints what it would send and sends
nothing. The exit status says whether the import succeeded, and it never reflects the run's verdict.

### Redaction

The machine that produced the run already redacted its text when it wrote each artifact
([BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md)). The server
does not scan the run again. Pixels in screenshots and videos carry no redaction. The documentation
tells a developer to check them before the upload.

### Tests and documentation

The tests cover a valid import on a local store and on a fake object store, and an import of a real
run tree. They cover each rejection: a bad zip, a traversal path, a symlink, a missing manifest, a missing or malformed `scenario.yaml`, a bad run id, a manifest `runId` that differs from the directory name, a `.deleted` entry, and an artifact path with a scheme or a `..` segment. They also cover
the dropped entries: an HTML, SVG, or XML entry in any letter case is dropped and listed in the reply.
The real run tree includes a `rawTree` capture, and its two XML files are dropped. On the hosted
store, `GET /runs/<runId>/report.html` returns `200` after the import. A stale `importing` row of the caller's
organization is purged with its orphaned objects, and another organization's row is left alone. The
flip to `done` is a compare-and-set, and a zip that is too large once uncompressed gets the
oversized-run message. They also cover the `409` collision across organizations and under two concurrent imports,
the rollback after a failed placement, the role check, the key path, the refusal of a plain `http://`
server, and the redirect rule. The route registry test and the stdlib import guard cover the new route.
`docs/self-hosting.md`, `docs/cli.md`, and their `docs/ja/` mirrors describe the route. `docs/reporting.md`
and its mirror gain a section on the upload script.

## Alternatives considered

- **Extend `rescan`.** It reads the server's own disk and only an administrator may call it. A laptop
  cannot place a tree there.
- **The presigned-URL flow of a worker.** It is bound to a leased job and to the worker token. A script
  has neither.
- **`run --evidence-store`.** It needs cloud credentials on the laptop, and it records no history entry,
  so the report has no URL on `serve`.
- **Reuse `/api/upload`.** That route binds a configuration bundle to a session. Its machinery fits the
  receiving, and its placement does not fit a run.
- **Serve the imported files under a sandbox policy.** A `Content-Security-Policy: sandbox` header
  would let uploaded pages stay. The `file://` store already adds it on its object route, but a hosted
  store answers a run file with a `302` to a signed URL, and `serve` cannot set a header on those
  bytes. An allowlist of passive types at the door works on both stores.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] The route, receiving: the raw body, the cap, both backends, and the entry-list checks, including the extension allowlist with its dropped-entry list, the tombstone refusal, the artifact-path checks, and the `scenario.yaml` and `runId` checks.
- [x] The route, storing: the collision checks, the `importing` status with the `list_runs` filter, the compare-and-set flip, the stale purge of the caller's organization and the start-up sweep, the regeneration of the report files, the placement on a local and a hosted store, the rollback, and the recording.
- [x] Authorization: the explicit editor case before the early branch, and the key allowlist.
- [x] The upload script: the zip, the key from the environment or a file, the transport rules, and the URL output.
- [x] Tests: the cases above, the route registry, and the stdlib import guard.
- [x] Documentation: the `docs/` pages and their `docs/ja/` mirrors.

Log:

- [#2561](https://github.com/bajutsu-e2e/bajutsu/pull/2561): everything but the key allowlist shipped, and the editor case is in place. The allowlist waits for
  [BE-0509](../BE-0509-serve-org-api-keys/BE-0509-serve-org-api-keys.md), which creates it. Until
  then the script authenticates with the shared token. A server with OAuth refuses that token. The
  table lists where the implementation differs from the design.

| Point | What shipped, and why |
|---|---|
| Flip to `done` | A new `Repository.finish_import`, since the protocol had no compare-and-set |
| Local start | A rename for the `default` organization alone, whose runs tree the store mounts. Other organizations go through the object store |
| `--browsers` runs | The path check reads a `<sid>/result.json` from `<engine>/`, where its paths start |
| Redirects | The script follows none. A followed redirect would turn the `POST` into a `GET` that imports nothing |
| Start-up sweep | It removes staging directories more than an hour old, not every one |

- [#2586](https://github.com/bajutsu-e2e/bajutsu/pull/2586): `POST /api/runs/import` is now on the BE-0509 key allowlist, the first route there. An
  `editor` key imports into its own organization, so the script works on a server with OAuth. A
  `viewer` key gets `403`.

## References

- [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md): the `rescan` route and the run history that the import feeds.
- [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md): the evidence store that the import does not replace.
- [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md): the zip layout of a run, which the script reuses.
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md): redaction at the artifact boundary.
