**English** · [日本語](BE-XXXX-artifact-redaction-boundary-ja.md)

# BE-XXXX — Redact at the artifact boundary so no writer can emit a plaintext secret

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-artifact-redaction-boundary.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Security hardening |
| Related | [BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md), [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md), [BE-0097](../BE-0097-crawl-ai-data-sovereignty/BE-0097-crawl-ai-data-sovereignty.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0120](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization.md), [BE-0130](../BE-0130-default-network-secret-redaction/BE-0130-default-network-secret-redaction.md), [BE-0151](../BE-0151-screenshot-secret-capture-warning/BE-0151-screenshot-secret-capture-warning.md), [BE-0153](../BE-0153-encode-aware-secret-redaction/BE-0153-encode-aware-secret-redaction.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu redacts secrets per writer. The `Redactor` in
[`bajutsu/evidence/redaction.py`](../../bajutsu/evidence/redaction.py) masks configured keys, labels,
and known secret values, and the evidence subsystem calls it on the element tree, the device log, and
each network exchange before writing them. Every other writer is on its honor to do the same, and
`crawl`'s artifact writers do not.

This item moves redaction from a habit to a boundary. Every write into a run directory goes through
one sink that requires a redactor, so a writer cannot emit an unredacted artifact by forgetting to
ask. Masking becomes default-on for the two cases a caller should never have to configure: a field the
platform itself marks secret, and a field whose identifier or label names a credential. The leak that
motivated this item is the second case — the field was called `settings.apikey`, and nothing masked
it. A pattern backstop then covers the case neither default anticipates: a value whose field name
suggests nothing but whose shape is a recognizable credential.

That leak reached two artifacts. A `crawl --guide ai` run wrote
`type settings.apikey='sk-ant-api03-…'` into `screenmap.json` and the generated screen-map report. The
value was synthetic, invented by the model to satisfy a field asking for an API key, and it was never
committed. The mechanism that carried it there is real: nothing on that path redacts, and one function
put the value somewhere no configured rule could reach it.

## Motivation

### What is already guaranteed

Several items built the current guarantee, and each holds where it was applied.

- [BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables.md) binds a secret through
  `${secrets.X}` rather than a literal in a scenario.
- [BE-0130](../BE-0130-default-network-secret-redaction/BE-0130-default-network-secret-redaction.md)
  masks a set of credential-bearing headers by default, so `network.json` never hands over a live
  token merely because a scenario omitted `redact:`. Releasing a default takes an explicit, visible
  `redact.unmaskHeaders`.
- [BE-0153](../BE-0153-encode-aware-secret-redaction/BE-0153-encode-aware-secret-redaction.md) masks a
  known secret value in its common encodings, since a percent-encoded token never appears in its raw form.
- [BE-0120](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization.md)
  tokenizes a secret an authoring agent typed during `record` into `${secrets.*}` instead of writing
  the literal into the drafted scenario.
- [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) and
  [BE-0097](../BE-0097-crawl-ai-data-sovereignty/BE-0097-crawl-ai-data-sovereignty.md) redact the
  textual input sent to an AI provider, on the authoring paths and on the `crawl --guide ai` path.
- [BE-0151](../BE-0151-screenshot-secret-capture-warning/BE-0151-screenshot-secret-capture-warning.md)
  warns up front that a screenshot cannot be masked, rather than implying pixels are protected.

Every one of those items is `Implemented`. What none of them establishes is that a *new* writer
inherits the guarantee.

### The gap is enforcement, not configuration

`crawl` already holds everything it needs to redact. The command builds a redactor from the resolved
target configuration and the environment's secret values, at
[`bajutsu/cli/_shared.py:137`](../../bajutsu/cli/_shared.py), and hands it to the AI guide so an
outbound prompt is scrubbed. The guide is the only recipient. The same module then writes the screen
map without passing the redactor it just built, and the two modules that render `crawl`'s artifacts —
[`bajutsu/crawl/serialize.py`](../../bajutsu/crawl/serialize.py) and
[`bajutsu/crawl/report.py`](../../bajutsu/crawl/report.py) — contain no reference to redaction at all.
So BE-0097 protected what `crawl` sends to a model and left what `crawl` writes to disk unprotected —
and the report it writes is a self-contained HTML file meant to be shared.

The wider shape of the problem is that there is no single place a run's bytes pass through. Writers
call `Path.write_text` and `json.dumps` directly across the command modules, the crawl package, and
the report renderers. Adding a writer is therefore adding a path that may or may not redact, and
nothing tells the author which they chose.

### One function puts a value beyond every configured rule

The redactor masks a value in three ways: after a configured key, when an element's label is
configured, and when the value matches a known secret literal. A crawl action defeats all three.
[`bajutsu/crawl/core.py:145`](../../bajutsu/crawl/core.py) builds an action's human-readable label as
`f"type {what}={self.value!r}"`, so the typed text is interpolated into a free-text string and
serialized as the `action` field of every screen-map node and edge.

No configured rule reaches it. The value is not a network header, so the default header set does not
apply. The string is not an element, so label-based masking does not apply. And a value the AI guide
invented is not a known secret literal, because it was never bound through `${secrets.X}` — the
crawl invented it during exploration. Masking rules that depend on knowing the value in advance
cannot cover a value the tool itself generates.

### Redaction is inert by default for anything but a header

`Redactor.active` is true only when a key, a label, or a known value is configured. When a run
configures none, `redact_text` and `redact_elements` return their input unchanged. Header masking is
the deliberate exception, and BE-0130 made it default-on for exactly the reason that applies here:
protection that arrives only when someone remembers to ask for it is protection that is absent on the
runs that need it most. A `crawl` has no scenario at all, so it never carries a `redact:` block, and
element and free-text masking are inert on that path even once the redactor is wired in.

The same inertness reaches a real password field. A remaining local run holds
`type auth.password='Passw0rd!2026'`, written verbatim. The platform marked that field
`secureTextField`, so its secrecy was known at capture time and no configuration should have been
required.

## Detailed design

### The boundary

One sink owns every write into a run directory. A caller hands it a *relative name* and content, never
a path; the sink resolves the name against the run directory, redacts, serializes, and writes. The sink
is the only code in the repository that writes into a run directory, so redaction is a property of the
location rather than of the caller's diligence.

The sink takes content *before* serialization, which its entry points make explicit. Two of the rules
this item makes default-on are structural rather than textual: the masked-input trait keys on an
element's trait, and the credential-named default keys on an element's identifier or label to mask that
element's value. `Redactor` already separates the three shapes accordingly —
[`bajutsu/evidence/redaction.py`](../../bajutsu/evidence/redaction.py) has `redact_elements` for an
element tree, `redact_exchange` for a network exchange, and `redact_text` for free text. Once an
element tree is a `JSON` string the identifier-to-value pairing is gone, so a sink that only scanned
serialized text could not apply either default, and moving the network writer onto such a sink would
silently drop the header masking BE-0130 made default-on. The sink therefore has one entry point per
shape — an element tree, network exchanges, a screen map, free text — each applying the matching
redactor method and then serializing. It is a small typed API rather than a generic text writer, which
is the cost of keeping the structural rules inside the boundary instead of back in the callers.

The pattern backstop runs last, over the serialized text, so it catches a value the structural rules
did not reach.

The sink also accepts opaque bytes for content it cannot inspect — screenshots, video, an archive —
and records that the content was written unmasked, keeping
[BE-0151](../BE-0151-screenshot-secret-capture-warning/BE-0151-screenshot-secret-capture-warning.md)'s
honesty: an image is not claimed to be protected.

Withholding the path is what makes the boundary enforceable. A caller that never receives the run
directory as a `Path` cannot write into it without the sink, so the guarantee rests on what a module
can reach rather than on what a reviewer notices. Two mechanical checks pin that down, and neither
needs to decide at analysis time whether a runtime path value points into a run directory.

- **An import contract.** No module except the sink may reach the run-directory *write* provider.
  This repository already runs such contracts in the gate — `lint-imports` keeps three today — and a
  contract states a property of the whole import graph rather than a list of writers, so a module that
  does not exist yet is covered the moment it does.
- **A literal check.** The filesystem run root is derived in one function, and a run-root path literal
  outside that function fails the gate. Together with the contract, this closes the remaining way to
  rebuild the path without importing the provider.

Both checks are decidable from source, which an inspection of runtime path values would not be. That
distinction is the reason the design withholds the path instead of scanning for suspicious writes: a
scan would have to guess which `write_text` call lands in a run directory, and it would either
false-positive on a legitimate config, cache, or temporary-file write or need a curated module list —
reintroducing the on-its-honor gap this item removes.

### Reading is not writing

The boundary governs writing alone, and saying so matters because the run directory's largest consumer
only reads it. `serve` lists runs, mounts the `runs/<id>/` tree, resolves a job's `manifest.json`, and
compares evidence across runs; [`bajutsu/serve/`](../../bajutsu/serve/) reaches run paths in several
places, and this item's own rejection of encryption rests on those files staying plainly readable. A
contract that forbade every module but the sink from resolving a run path would break all of it.

So the run directory is reached through two providers, and only one of them writes.

- **A read accessor** answers read questions about a run: list its artifact names, read one as text or
  bytes, open one as a stream. `serve`, the evidence readers, `export`, and the comparison commands
  import it, and it is not restricted, because none of those operations can create an artifact.
  Crucially it never returns a `Path`. A path into a run directory is writable, so handing one out
  would let any unrestricted importer call `write_text` on it and reach the directory without the
  sink — the contract would hold on paper while the boundary leaked.
- **The write sink** is what the import contract restricts. Nothing but the sink may reach it, so every
  byte entering a run directory still crosses redaction.

Between them, no component outside the sink ever holds a writable handle into a run directory. That is
what makes the contract mean what it says rather than merely regulating which module imports which.

Two consequences follow for existing code. `serve`'s `/runs/` route prefix and its run-id regular
expression are `URL` and stdout patterns rather than filesystem run roots, so the literal check does
not touch them; the check is scoped to deriving the filesystem root. And `serve`'s artifact store does
write into a run directory when a remote worker uploads its output, so that path moves onto the sink.
The uploading case is the one place the receiving side may hold no secret values for the run that
produced the content, since the run happened elsewhere. That limitation reaches only the rules which
need to know a value in advance: a configured key, a configured label, and a known secret literal. The
pattern backstop needs none of them, so the sink still runs it over uploaded text. Raw passthrough is
reserved for the bytes it cannot inspect at all — images, video, an archive. The sink records which of
the two an upload received, so an artifact is never described as scrubbed by rules that did not run.

### Masking that needs no configuration

Two defaults join BE-0130's header set, and both take the same explicit, visible opt-out that
`redact.unmaskHeaders` established.

**A field the platform marks secret.** A masked-input element has its value masked, and a typed or
filled value aimed at such a field is masked in whatever artifact records the action. The platform
already stated the field's secrecy; a configuration file should not have to restate it.

That default needs a normalized trait before it can hold everywhere, because no such trait exists
today. `secureTextField` is a raw XCUITest type rather than one of the normalized tokens in
[`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py), which carries only `button`, `link`,
`notEnabled`, and `selected`. The web backend maps every `input` to `textField`
([`bajutsu/dom.py`](../../bajutsu/dom.py)), so a password input arrives indistinguishable from a plain
one, and the adb backend never emits a secret-input trait at all. Keying the default on
`secureTextField` as it stands would protect iOS and silently leave web and Android unprotected, which
prime directive 3 does not allow: one construct must mean the same thing on every backend.

Each backend can supply the signal, so normalization is the work rather than a blocker. A new
normalized trait joins `base.Trait`, and each driver maps its own source onto it: XCUITest's
`secureTextField`, the web backend's `input[type=password]`, and the password flag the Android
accessibility node already exposes. The driver conformance suite
([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) pins the trait on
every backend, so a backend that stops reporting it fails the suite rather than quietly dropping the
default.

**A field whose identifier or label names a credential.** A default vocabulary — `password`,
`passwd`, `secret`, `token`, `apikey`, `api_key`, `credential`, `otp`, `pin` — matched against an
element's identifier and label, case-insensitively and on word boundaries. This is the default that
would have masked `settings.apikey`. The vocabulary is small and documented rather than clever,
because a rule an author cannot predict is a rule an author cannot rely on.

### Values the tool generates

A masking rule keyed on a known secret cannot cover a value Bajutsu produced itself, which is what the
AI guide does when it invents a realistic API key. Two changes address that class.

First, an input value stops being interpolated into a human-readable string.
`Action.describe` emits `type <target>` and leaves the value in the action's own `value` field, so
exactly one place decides whether the value is written and the sink's rules can reach it. The screen
map keeps a replayable action, since `perform` already reads `value` directly and never parses the
description.

Second, the sink applies a pattern backstop before writing text. High-confidence credential shapes are
masked wherever they appear: `sk-ant-` for an Anthropic key, `AKIA` for an AWS access key id,
`gh[pousr]_` for a GitHub token, a three-segment JSON Web Token (`JWT`), and a `PEM`
private-key header. A match is masked and reported as a warning naming the artifact, because a value
reaching the backstop means an earlier rule should have caught it. The patterns are literal regular
expressions, so no model is consulted and prime directive 1 is untouched.

### What this does not guarantee

The item does not make it impossible for a secret to reach an artifact, and claiming otherwise would
be false. Three residues remain, and each is named rather than papered over.

- **Pixels stay unmasked.** A screenshot of a filled password field still shows it. BE-0151 already
  established the warning; this item does not change that.
- **An arbitrary value in an unmarked field can survive.** A secret typed into a field the platform
  does not mark, whose identifier and label name nothing credential-like, whose value was never bound
  through `${secrets.X}`, and whose shape matches no backstop pattern, is indistinguishable from
  ordinary text. Detecting it would need semantic judgment, which prime directive 1 keeps off this
  path.
- **The backstop's vocabulary is finite.** A credential format nobody added a pattern for passes it.

What the item does guarantee is narrower and checkable: no writer can bypass redaction, the two cases
whose secrecy is knowable at capture time are masked without configuration, and a recognizable
credential shape is masked wherever it occurs. The residue above is documented so an operator can
judge before sharing an artifact rather than after.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

1. **The run-directory sink.**

   Introduce the single write path for run output, taking the run's `Redactor`. Give it one entry point
   per content shape — an element tree, network exchanges, a screen map, free text — each applying the
   matching redactor method (`redact_elements`, `redact_exchange`, `redact_text`) and then serializing,
   with the pattern backstop over the serialized result. Every entry point takes a relative name rather
   than a path, so the run directory is never handed to a caller. Add an opaque-bytes entry point for
   images, video, and archives, which records that the content was written unmasked.

2. **Route every existing writer through the sink.**

   Convert the direct writes in the command modules, the crawl package, the report renderers, and
   `serve`'s artifact store. The known sites include
   [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py),
   [`bajutsu/crawl/report.py`](../../bajutsu/crawl/report.py),
   [`bajutsu/crawl/flows.py`](../../bajutsu/crawl/flows.py), the run archive writers, and the local
   artifact store in [`bajutsu/serve/`](../../bajutsu/serve/) that receives a remote worker's upload.
   Behavior for the evidence subsystem is unchanged, since it already redacts; it moves to the sink so
   one rule covers everything. The network writer moves onto the exchange entry point specifically, so
   BE-0130's default header masking keeps running inside the boundary rather than being left behind in
   the caller. Reading is untouched: `serve` and the evidence readers move onto the read
   accessor, which returns content and names rather than paths.

3. **The enforced boundary.**

   Split the run directory's read accessor, which returns content rather than a path, from its
   write provider, then add the `lint-imports`
   contract that forbids every module but the sink from reaching the write provider, plus the check
   that fails a filesystem run-root literal outside the deriving function. Both are decidable from
   source and cover a module that does not exist yet, so a future writer cannot bypass redaction by
   being added somewhere the gate was not told to look. Reading stays unrestricted.

4. **A normalized masked-input trait, and its default masking.**

   Add the masked-input trait to `base.Trait` and map each backend's own source onto it: XCUITest's
   `secureTextField`, the web backend's `input[type=password]`, and the Android accessibility node's
   password flag. Add a driver conformance case so every backend reports it. Then mask such an
   element's value, and any typed or filled value aimed at such a field, with no configuration, and add
   the visible opt-out alongside `redact.unmaskHeaders`.

5. **Default masking for a credential-named identifier or label.**

   Add the documented default vocabulary matched against an element's identifier and label, with the
   same opt-out. Cover the crawl action path, so an action targeting such a field is masked in the
   screen map.

6. **Stop interpolating a value into an action description.**

   Change [`bajutsu/crawl/core.py`](../../bajutsu/crawl/core.py) so `Action.describe` names the target
   without the value, and keep the value in the action's own field. Confirm replay is unaffected,
   since `perform` reads the field rather than the description.

7. **The pattern backstop.**

   Mask high-confidence credential shapes in any text the sink writes, and warn with the artifact
   named. Keep the patterns as literal regular expressions in one documented place.

8. **Tests.**

   Cover each default masking rule; the crawl action no longer carrying its value into a description;
   the backstop masking each pattern and warning once; the sink's unmasked-bytes path recording what it
   wrote; and a `crawl` run over a fake driver whose screen map holds no plaintext value for a
   credential-named or platform-marked field.

9. **Docs.**

   Document the two new defaults, their opt-out, the backstop, and the three residues in
   [`docs/evidence.md`](../../docs/evidence.md) and its Japanese mirror, next to BE-0130's header
   default. State plainly what is and is not protected, since an operator decides whether to share a
   report on the strength of that statement.

### Prime directives preserved

- **AI never judges.**

  Every rule is a configured name, a trait, a word-boundary match, or a literal regular expression. No
  model is consulted, and nothing here touches the `run` verdict.

- **Determinism first.**

  Redaction is a pure function of the content and the resolved configuration, so the same run produces
  the same artifact bytes. Both boundary checks read source alone. No fixed `sleep` and no condition wait is
  involved.

- **App-agnostic.**

  The defaults key on platform traits and on identifier and label text, not on any app. Per-app
  additions stay in `targets.<name>.redact`, where redaction configuration already lives. The
  masked-input trait is normalized across every backend and pinned by the conformance suite, so the
  default does not mean one thing on iOS and another on web or Android.

## Alternatives considered

- **Wire the existing redactor into the crawl writers and stop there.**

  Rejected as the whole answer, though unit 2 includes it. It fixes the writers that leak today and
  leaves the next writer exposed, because nothing would prevent a new direct `write_text`. It also
  would not have masked `settings.apikey`: the redactor is inert without configuration, a crawl
  carries no `redact:` block, and the value was invented rather than bound.

- **Mask the value inside `Action.describe` rather than removing it.**

  Rejected. Masking at the point of formatting puts the decision in a display helper, where the next
  formatter will make it again. Removing the value from the string leaves exactly one field to govern,
  which is the smaller surface.

- **Scan artifacts with gitleaks after a run instead of redacting during it.**

  Rejected as the primary mechanism. A post-hoc scan is what surfaced this leak, so it detects well,
  but by then the plaintext is on disk and possibly already shared, and a scan cannot mask in place
  without rewriting an artifact whose checksum may already be referenced. Detection also fails closed
  only if a run fails on a finding, which would put a scanner on the verdict path. The backstop in
  unit 7 keeps the pattern idea where it helps: before the bytes land.

- **Encrypt run directories rather than redact them.**

  Rejected. Encryption protects an artifact at rest and does nothing once the artifact is decrypted to
  be read, shared, or attached to a pull request, which is the moment that matters. It also would
  break the plain-file inspection that the evidence subsystem and the `serve` UI depend on.

- **Refuse to write any artifact when a secret is bound.**

  Rejected. Evidence exists to explain a failure, and withholding it on the runs that bind
  credentials would remove the diagnosis exactly where investigation is hardest. Masking preserves the
  artifact's structure while removing the value.

- **Ask the model whether a value looks sensitive.**

  Rejected outright. It would put a model on the path that decides what is written about a run, it
  would make artifact bytes non-deterministic, and prime directive 1 forbids the run path from
  depending on a model's judgment.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — the run-directory sink that applies the redactor
- [ ] Unit 2 — every existing writer routed through the sink
- [ ] Unit 3 — the path-free read accessor / write sink split, its import contract, and the literal check
- [ ] Unit 4 — a normalized masked-input trait on every backend, and its default masking
- [ ] Unit 5 — default masking for a credential-named identifier or label
- [ ] Unit 6 — the crawl action description stops carrying its value
- [ ] Unit 7 — the credential-shape pattern backstop
- [ ] Unit 8 — tests
- [ ] Unit 9 — docs for the defaults, the opt-out, and the residues

## References

- [`bajutsu/evidence/redaction.py`](../../bajutsu/evidence/redaction.py) — the `Redactor` this item makes unavoidable
- [`bajutsu/evidence/core.py`](../../bajutsu/evidence/core.py) — the evidence writers that already redact
- [`bajutsu/crawl/core.py`](../../bajutsu/crawl/core.py) — `Action.describe`, which interpolates a typed value into free text
- [`bajutsu/crawl/serialize.py`](../../bajutsu/crawl/serialize.py) — screen-map serialization that carries the action string
- [`bajutsu/crawl/report.py`](../../bajutsu/crawl/report.py) — the self-contained screen-map report
- [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py) — where a run's redactor is built today
- [BE-0130](../BE-0130-default-network-secret-redaction/BE-0130-default-network-secret-redaction.md) — the default-on header masking this item follows
- [BE-0097](../BE-0097-crawl-ai-data-sovereignty/BE-0097-crawl-ai-data-sovereignty.md) — crawl's outbound AI redaction, whose written-artifact counterpart is missing
- [BE-0151](../BE-0151-screenshot-secret-capture-warning/BE-0151-screenshot-secret-capture-warning.md) — the precedent for naming what cannot be masked
- [BE-0120](../BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization.md) — secret tokenization on the `record` path
- [BE-0153](../BE-0153-encode-aware-secret-redaction/BE-0153-encode-aware-secret-redaction.md) — encoding-aware masking of a known value
