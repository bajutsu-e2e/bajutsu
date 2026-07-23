**English** · [日本語](BE-0060-run-report-zip-export-ja.md)

# BE-0060 — Download / export a run report as a zip

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0060](BE-0060-run-report-zip-export.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0060") |
| Implementing PR | [#194](https://github.com/bajutsu-e2e/bajutsu/pull/194) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

A one-step way to take a finished `run`'s output directory — `report.html` together with its
`manifest.json`, `junit.xml`, the executed `scenario.yaml`, and **all** of its evidence
(screenshots, video, `network.json`, `device.log`, …) — and obtain it as a single `.zip`. It is
offered from two surfaces that share **one archiver**: a **Download** button in the `bajutsu serve`
web UI (per run, in History and on the Replay result), and a **CLI** path (`bajutsu run --zip` for
the inline case, `bajutsu export <run>` to archive an existing run after the fact). The archive
bundles the whole `runs/<id>/` directory under a top-level `<id>/` folder, so `report.html`'s
**relative** asset links resolve offline — the report works by double-click, with no server. This
is a Tier-1 convenience: it adds no LLM anywhere, never touches `run`'s pass/fail, and inherits the
secret-scrubbing the run already applied.

## Motivation

A run's output is **already complete and self-describing** — the deterministic runner writes
`report.html`, `manifest.json`, `junit.xml`, the executed scenario, and the evidence tree — yet it
is **trapped on the machine that produced it**, with no first-class way to lift a *complete* copy
out. The gap bites in four concrete ways:

1. **`report.html` is not portable on its own.** The report references its evidence — screenshots,
   the interval video, `network.json` — by **relative link**; the local web UI exists partly to
   serve that evidence so those links resolve ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)).
   Copy just the HTML out and you get a report with broken images and no video. The only way to
   move a *working* report is to move the whole directory, intact, with its layout preserved.
2. **Sharing a failure is a daily need.** Attaching a run to a bug ticket, handing it to a teammate
   who has no Mac or Simulator, or keeping an offline record of a release run all require one
   portable file. Today people resort to `zip -r runs/<id>` by hand — easy to get wrong (zip from
   the wrong root and the relative links break) and unavailable to anyone not sitting at that
   shell.
3. **A hosted or remote `serve` gives the browser no file-system access.** When `serve` runs on a
   remote worker or a shared host ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
   [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)), the
   `runs/` directory lives on that machine, not the user's. A browser user has *no* way to retrieve
   a complete run — they can view the embedded report but cannot take it with them. A download
   endpoint is the only mechanism that closes this.
4. **CI wants the report as a single artifact.** Uploading run results from CI is one
   `upload-artifact` step over one file when a zip exists, versus shipping a directory tree and
   hoping the relative layout survives. `bajutsu run --zip` makes "produce the artifact" part of the
   same command that produced the verdict.

No archive handling exists in the codebase today (no `zipfile` / `tarfile` / `make_archive` use
anywhere) — this is the missing **export** half of the report subsystem, and it is a pure
packaging concern that fits the determinism-first contract by construction.

## Detailed design

### One archiver, two surfaces

A single archiver — given a `run_dir`, it produces a zip of that directory's contents — is the
shared core; both the CLI and `serve` call it, so the two surfaces can never diverge in what a
"run zip" contains. It belongs next to the existing report writers (`bajutsu/report/`), alongside
`manifest.py` / `html.py`. It is built on the **stdlib `zipfile`**, adding no dependency — keeping
parity with `serve` being stdlib-only and the gate dependency-light.

### Scope: the whole run directory

The archive contains everything under `runs/<id>/`: `report.html`, `manifest.json`, `junit.xml`,
the executed `scenario.yaml`, and every per-scenario / per-step evidence file. Entry names are
**relative to the run dir and rooted under a single top-level `<id>/` folder**, so unzipping yields
`<id>/report.html` with the evidence laid out exactly as `serve` serves it. The report's relative
links therefore resolve unchanged — the whole point of bundling the directory rather than the lone
HTML. The directory walk is **sorted** (and file mtimes may be pinned to a fixed value) so the same
run dir yields a reproducible zip; byte-stability is a nicety here, not a contract (unlike evidence,
[DESIGN §2](../../DESIGN.md)).

### CLI surface

Two ergonomics, sharing the one archiver:

* **`bajutsu run … --zip`** — after a run finishes and the report is written, also emit
  `runs/<id>.zip` (its path printed). This is the CI-friendly form: one command yields both the
  verdict and the uploadable artifact. The flag runs *after* the deterministic verdict is decided,
  so it cannot influence pass/fail.
* **`bajutsu export <run-id|path> [-o out.zip]`** — a standalone command that archives an
  already-existing run, the natural home of the shared archiver and the answer for "I want the zip
  for a run I did earlier". Default output is `runs/<id>.zip` (sibling of the run dir); `-o/--output`
  overrides; it refuses to overwrite an existing file without `--force`, mirroring how `record`
  never silently overwrites.

(The exact spelling is a small open detail; the load-bearing decision is that both call the same
archiver.)

### serve surface

A new endpoint — `GET /runs/<id>/archive.zip` — streams the zip with
`Content-Type: application/zip` and `Content-Disposition: attachment; filename="<id>.zip"`. It is
served **through the existing `ArtifactStore` abstraction** (`bajutsu/serve/artifacts.py`), which
already confines all run-file access and prevents path traversal: the interface gains an
`archive(run_id)` method that `LocalArtifactStore` implements by walking the confined run dir, and
that any future hosted store implements on its side (the worker that holds the files zips them
there). Reusing this boundary keeps the handler from touching the file system directly.

In the UI, a **Download** button appears per run in the **History** list and on the **Replay**
result view, beside the embedded report ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)).
One click downloads `<id>.zip`. For a large run (video plus many screenshots) the store builds the
archive into a temp file and streams it, keeping memory bounded rather than holding the whole zip in
RAM.

### Determinism, the gate, and secrets

* **No LLM, no effect on the verdict.** This is post-hoc packaging of artifacts the deterministic
  `run` already produced. `run`'s pass/fail is computed before — and independently of — any zip;
  `--zip` only adds a packaging step after the verdict. Prime directives 1 and 2
  ([CLAUDE.md](../../CLAUDE.md)) hold by construction.
* **Linux-testable.** Zipping a directory needs no Simulator, so the archiver and the endpoint are
  unit-tested on the existing Linux gate against a fixture run dir.
* **Secrets stay scrubbed.** Secret values are already redacted from artifacts after a run
  ([BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md)); the zip
  packages what is on disk, so it inherits that scrub and re-introduces nothing. The archiver must
  archive **strictly the run dir** — never reaching out to `.env`, config, or anything above it.

### The test contract (machine-checkable)

The archiver and endpoint are pinned by tests that need no Simulator: (a) the zip contains exactly
the run-dir tree under a single `<id>/` root; (b) `report.html` and every asset it references are
present, so its relative links resolve; (c) nothing outside the run dir leaks in; (d) no known
secret value appears in any zipped byte; (e) the `serve` endpoint sets `application/zip` +
`attachment` and resolves through `ArtifactStore` (so path traversal stays impossible).

### Out of scope

Multi-run bundles; a lean "report + referenced assets only" subset (see *Alternatives*); cloud
upload or share-links (a hosting concern, [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
/ [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)); and
retention / cleanup of old runs.

## Alternatives considered

* **Zip `report.html` only (the single self-contained file).** Rejected as the default: the report
  links its evidence by relative path ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)),
  so a lone HTML loses every screenshot, the video, and the network log. The whole-run zip is what
  makes the report actually portable. A lean "report + only the assets it references" variant could
  be offered later for size-sensitive cases, but it is an optimization on top, not the safe default.
* **Tell users to `zip -r runs/<id>` themselves.** Works at a local Mac shell, but (a) gives nothing
  to a *browser* user of a hosted/remote `serve` who has no file-system access; (b) is manual and
  easy to get wrong — zip from the wrong root and the report's relative links break; (c) leaves the
  UI and CI without a shared, stable contract for "what a run zip is". A built-in archiver with a
  fixed internal layout fixes all three.
* **A different archive format / an external packaging dependency.** Rejected: stdlib `zipfile` is
  universal, opens by double-click on every OS, and adds no dependency — consistent with `serve`
  being stdlib-only and the gate staying dependency-light. A tarball would be less friendly to the
  Windows users a shared report may reach.
* **Always build the zip inside `run` (no flag).** Rejected: most runs never need it, and it would
  add I/O and disk to every CI run. Keep it opt-in (`--zip`) or on demand (`export` / the serve
  button).
* **A dedicated "run report export / sharing" roadmap topic.** Deferred, following the
  [BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) precedent of
  not splitting a topic for a single item: this is filed under *Authoring experience* for now. If
  siblings appear (cloud upload, share-links, alternative export formats), a dedicated topic can be
  carved out then.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

* [CLAUDE.md](../../CLAUDE.md), [DESIGN §2](../../DESIGN.md) — AI never judges; determinism
  first. The archiver adds no LLM and runs after the verdict.
* [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)
  — the embedded report and the relative-link evidence serving this extends; where the Download
  button lives.
* [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
  [BE-0016 — Self-hosting of the web UI](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
  — why a download endpoint matters when the browser has no access to the worker's file system.
* [BE-0032 — Secret variables](../BE-0032-secret-variables/BE-0032-secret-variables.md)
  — the existing artifact scrub the zip inherits.
* [BE-0018 — Return evidence as MCP resources](../BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources.md)
  — the adjacent "expose run artifacts" surface (MCP); the zip is the human / file-download
  equivalent.
* `bajutsu/report/` (`manifest.py`, `html.py`), `bajutsu/serve/artifacts.py`,
  [cli.md](../../docs/cli.md), [evidence.md](../../docs/evidence.md) — the report writers, the
  confined artifact store, and the run-output and evidence layout this packages.
