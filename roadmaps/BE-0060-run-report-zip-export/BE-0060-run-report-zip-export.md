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

This item adds a one-step way to export a finished `run`'s output directory as a single `.zip`. The archive holds `report.html`, its `manifest.json`, `junit.xml`, the executed `scenario.yaml`, and **all** of the evidence: screenshots, video, `network.json`, `device.log`, and the rest.

Two surfaces offer the export, and they share **one archiver**:

- a **Download** button in the `bajutsu serve` web UI, per run, in History and on the Replay result;
- a **CLI** path: `bajutsu run --zip` for the inline case, and `bajutsu export <run>` to archive an existing run after the fact.

The archive bundles the whole `runs/<id>/` directory under a top-level `<id>/` folder. The **relative** asset links in `report.html` then resolve offline, and the report opens by double-click with no server.

This is a Tier-1 convenience. It adds no LLM anywhere and never touches `run`'s pass/fail. It inherits the secret-scrubbing that the run already applied.

## Motivation

A run's output is **already complete and self-describing**. The deterministic runner writes `report.html`, `manifest.json`, `junit.xml`, the executed scenario, and the evidence tree. Yet that output is **trapped on the machine that produced it**. No first-class way lifts a *complete* copy out. The gap bites in four concrete ways:

1. **`report.html` is not portable on its own.** The report references its evidence (screenshots, the interval video, `network.json`) by **relative link**. The local web UI exists partly to serve that evidence so those links resolve ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)). Copy just the HTML out, and you get a report with broken images and no video. A *working* report moves only as the whole directory, intact, with its layout preserved.
2. **Sharing a failure is a daily need.** Attaching a run to a bug ticket, handing it to a teammate who has no Mac or Simulator, and keeping an offline record of a release run all need one portable file. Today people run `zip -r runs/<id>` by hand. That is easy to get wrong, because zipping from the wrong root breaks the relative links. It is also unavailable to anyone who is not sitting at that shell.
3. **A hosted or remote `serve` gives the browser no file-system access.** `serve` may run on a remote worker or a shared host ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)). The `runs/` directory then lives on that machine, not the user's. A browser user has *no* way to retrieve a complete run. They can view the embedded report but cannot take it with them. Only a download endpoint closes this gap.
4. **CI wants the report as a single artifact.** With a zip, uploading run results from CI is one `upload-artifact` step over one file. Without it, CI ships a directory tree and hopes the relative layout survives. `bajutsu run --zip` makes "produce the artifact" part of the same command that produced the verdict.

No archive handling exists in the codebase today. No `zipfile`, `tarfile`, or `make_archive` call appears anywhere. This item supplies the missing **export** half of the report subsystem. It is a pure packaging concern, so it fits the determinism-first contract by construction.

## Detailed design

### One archiver, two surfaces

A single archiver is the shared core. Given a `run_dir`, it produces a zip of that directory's contents. Both the CLI and `serve` call it, so the two surfaces can never diverge on what a "run zip" contains.

The archiver belongs next to the existing report writers (`bajutsu/report/`), alongside `manifest.py` and `html.py`. It builds on the **stdlib `zipfile`** and adds no dependency. That keeps parity with `serve` being stdlib-only and the gate dependency-light.

### Scope: the whole run directory

The archive contains everything under `runs/<id>/`: `report.html`, `manifest.json`, `junit.xml`, the executed `scenario.yaml`, and every per-scenario and per-step evidence file.

Entry names are **relative to the run dir and rooted under a single top-level `<id>/` folder**. Unzipping yields `<id>/report.html`, with the evidence laid out exactly as `serve` serves it. The report's relative links therefore resolve unchanged. That is the point of bundling the directory instead of the lone HTML.

The directory walk is **sorted**, and file mtimes may be pinned to a fixed value. The same run dir then yields a reproducible zip. Byte-stability is a nicety here, not a contract, unlike evidence ([DESIGN §2](../../DESIGN.md)).

### CLI surface

Two commands share the one archiver:

* **`bajutsu run … --zip`.** After a run finishes and the report is written, the command also emits `runs/<id>.zip` and prints its path. This is the CI-friendly form: one command yields both the verdict and the uploadable artifact. The flag runs *after* the deterministic verdict is decided, so it cannot influence pass/fail.
* **`bajutsu export <run-id|path> [-o out.zip]`.** This standalone command archives an already-existing run. It is the natural home of the shared archiver and the answer for "I want the zip for a run I did earlier". The default output is `runs/<id>.zip`, a sibling of the run dir. `-o/--output` overrides it. The command refuses to overwrite an existing file without `--force`, mirroring how `record` never silently overwrites.

The exact spelling is a small open detail. The load-bearing decision is that both commands call the same archiver.

### serve surface

A new endpoint, `GET /runs/<id>/archive.zip`, streams the zip with `Content-Type: application/zip` and `Content-Disposition: attachment; filename="<id>.zip"`.

The endpoint serves **through the existing `ArtifactStore` abstraction** (`bajutsu/serve/artifacts.py`). That abstraction already confines all run-file access and prevents path traversal. The interface gains an `archive(run_id)` method. `LocalArtifactStore` implements it by walking the confined run dir. Any future hosted store implements it on its own side, where the worker that holds the files zips them. Reusing this boundary keeps the handler from touching the file system directly.

In the UI, a **Download** button appears per run in the **History** list and on the **Replay** result view, beside the embedded report ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)). One click downloads `<id>.zip`. For a large run (video plus many screenshots), the store builds the archive into a temp file and streams it. Memory stays bounded, because the whole zip never sits in RAM.

### Determinism, the gate, and secrets

* **No LLM, no effect on the verdict.** The export is post-hoc packaging of artifacts that the deterministic `run` already produced. `run`'s pass/fail is computed before any zip, and independently of it. `--zip` only adds a packaging step after the verdict. Prime directives 1 and 2 ([CLAUDE.md](../../CLAUDE.md)) hold by construction.
* **Linux-testable.** Zipping a directory needs no Simulator. Unit tests exercise the archiver and the endpoint on the existing Linux gate, against a fixture run dir.
* **Secrets stay scrubbed.** A run already redacts secret values from its artifacts ([BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md)). The zip packages what is on disk, so it inherits that scrub and re-introduces nothing. The archiver must archive **strictly the run dir**. It never reaches out to `.env`, config, or anything above the run dir.

### The test contract (machine-checkable)

Tests that need no Simulator pin the archiver and the endpoint:

- (a) The zip contains exactly the run-dir tree under a single `<id>/` root.
- (b) `report.html` and every asset that it references are present, so its relative links resolve.
- (c) Nothing outside the run dir leaks in.
- (d) No known secret value appears in any zipped byte.
- (e) The `serve` endpoint sets `application/zip` and `attachment`, and it resolves through `ArtifactStore`, so path traversal stays impossible.

### Out of scope

- Multi-run bundles.
- A lean "report plus referenced assets only" subset (see *Alternatives*).
- Cloud upload and share-links, which are a hosting concern ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) and [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)).
- Retention and cleanup of old runs.

## Alternatives considered

* **Zip `report.html` only (the single self-contained file).** Rejected as the default. The report links its evidence by relative path ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)), so a lone HTML loses every screenshot, the video, and the network log. The whole-run zip makes the report actually portable. A lean "report plus only the assets it references" variant could serve size-sensitive cases later. It is an optimization on top, not the safe default.
* **Tell users to run `zip -r runs/<id>` themselves.** This works at a local Mac shell, but it has three flaws. (a) It gives nothing to a *browser* user of a hosted or remote `serve`, who has no file-system access. (b) It is manual and easy to get wrong, because zipping from the wrong root breaks the report's relative links. (c) It leaves the UI and CI without a shared, stable contract for "what a run zip is". A built-in archiver with a fixed internal layout fixes all three.
* **A different archive format or an external packaging dependency.** Rejected. The stdlib `zipfile` is universal, opens by double-click on every OS, and adds no dependency. That is consistent with `serve` being stdlib-only and the gate staying dependency-light. A tarball would be less friendly to the Windows users whom a shared report may reach.
* **Always build the zip inside `run` (no flag).** Rejected. Most runs never need the zip, and building it would add I/O and disk to every CI run. The export stays opt-in (`--zip`) or on demand (`export` and the serve button).
* **A dedicated "run report export / sharing" roadmap topic.** Deferred, following the [BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) precedent of not splitting a topic for a single item. This item is filed under *Authoring experience* for now. If siblings appear (cloud upload, share-links, alternative export formats), a dedicated topic can be carved out then.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

* [CLAUDE.md](../../CLAUDE.md) and [DESIGN §2](../../DESIGN.md): AI never judges, and determinism comes first. The archiver adds no LLM and runs after the verdict.
* [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md): the embedded report and the relative-link evidence serving that this item extends, and the place where the Download button lives.
* [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) and [BE-0016 — Self-hosting of the web UI](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md): why a download endpoint matters when the browser has no access to the worker's file system.
* [BE-0032 — Secret variables](../BE-0032-secret-variables/BE-0032-secret-variables.md): the existing artifact scrub that the zip inherits.
* [BE-0018 — Return evidence as MCP resources](../BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources.md): the adjacent "expose run artifacts" surface (MCP). The zip is the human, file-download equivalent.
* `bajutsu/report/` (`manifest.py`, `html.py`), `bajutsu/serve/artifacts.py`, [cli.md](../../docs/cli.md), and [evidence.md](../../docs/evidence.md): the report writers, the confined artifact store, and the run-output and evidence layout that this item packages.
