**English** · [日本語](BE-0432-devicefarm-pretest-extension-hook-ja.md)

# BE-0432 — A generic pre_test hook in the Device Farm test spec

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0432](BE-0432-devicefarm-pretest-extension-hook.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0432") |
| Topic | Device-cloud execution |
| Related | [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md) |
<!-- /BE-METADATA -->

## Introduction

`render_test_spec` renders a Device Farm custom-environment test spec.
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md)
introduced the function, which now lives in `bajutsu/common/cloud/devicefarm/_functions.py`
and is re-exported from `bajutsu.common.cloud.devicefarm`. The rendered spec runs a
set of scenarios on a reserved device.

Today its `pre_test` phase runs one command: the reserved-device visibility probe. A
caller may need the device to do something else before the `test` phase's
`bajutsu run` calls start, and has no way to add a command to `pre_test`. The single
alternative is reimplementing everything `render_test_spec` already renders.

This proposal adds a `pre_test_commands` parameter. A caller uses it to splice its
own commands into the `pre_test` phase. Device-side setup specific to one
deployment's backend can then live entirely outside `bajutsu/`.

## Motivation

Prime directive 3 keeps Bajutsu app-agnostic. Per-app differences live in
configuration, never inside the tool. A concrete case surfaced this constraint. It
arose while integrating Bajutsu with a staging backend behind an IP allowlist.

Reaching that backend from a Device Farm device needs a per-run authenticated
network relay. A device-side script must configure that relay first, before the
scenario's own traffic starts. That setup belongs to one deployment's backend.
Another deployment might need something different:

- a Virtual Private Network (VPN) client
- a Mobile Device Management (MDM) profile
- nothing at all

None of this setup belongs inside Bajutsu. Bajutsu must stay usable regardless of what a
caller's backend requires.

Today a caller has two options, and both defeat the purpose of a shared, tested
`render_test_spec`. Forking its output duplicates the following, to insert a single
command:

- the Python bootstrap
- the probe
- the `test` and `post_test` phases
- artifact collection
- YAML rendering

Maintaining a parallel test-spec generator outside Bajutsu is the other option. It
keeps two implementations of the same shape in sync by hand.

`build_package` already has an `extra_texts` parameter. It lets a caller ship an
arbitrary file, such as a setup script, into the test package. Nothing today makes
Device Farm actually run that file during `pre_test`.

## Detailed design

`render_test_spec` gains a `pre_test_commands: Sequence[str] = ()` parameter. Each
entry appends to the `pre_test.commands` list. Entries append in order, after the
existing visibility probe:

```python
render_test_spec(
    scenarios,
    target=target,
    config=config,
    platform="ios",
    pre_test_commands=["bash configure-proxy.sh"],
)
```

renders:

```yaml
phases:
  pre_test:
    commands:
    - <existing platform probe>
    - bash configure-proxy.sh
```

`render_test_spec` treats each entry as an opaque, already-shell-safe string. A
Device Farm test spec already has this trust boundary: every phase runs arbitrary
shell. This mirrors how `build_package`'s `extra_texts` passes caller content
through verbatim — though `extra_texts` content stays inert data inside the zip,
while a `pre_test` command executes on the Device Farm host.

It differs from how `render_test_spec` quotes `scenarios`, `target`, and `config`
today. Those three compose into one command the function itself builds, and they
trace back to request or workflow text input, so the function quotes them. A
caller-supplied command list is the caller's own construction instead.

`pre_test_commands` stays a Python-API-only hook for that reason: no `serve`
endpoint, no config field, and no `BatchRequest` field may carry it.
`DeviceFarmBatchProvider` — the one in-tree caller — builds its `render_test_spec`
arguments from an HTTP request body, so routing this parameter through that path
would hand a client shell on a host holding the run's AWS role credentials.

The default `()` renders nothing extra. Every existing caller, and every existing
test, keeps generating byte-identical output.

`pre_test_commands` pairs with `build_package`'s `extra_texts` parameter:

```python
build_package(entries, out_zip, extra_texts={"configure-proxy.sh": script_text})
```

Together, a caller assembles a full `pre_test` setup. That setup has two parts: the
script content, and the command that runs it. A caller assembles this setup
entirely from outside `bajutsu/`. Bajutsu never inspects or interprets what that
setup does.

## Alternatives considered

- **A callback invoked during rendering (`Callable[[], list[str]]`).** Rejected.
  `render_test_spec` is a pure function today, taking input and returning rendered
  YAML text. Accepting a callback would run caller code during rendering, for no
  benefit over passing an already-computed list. It would also complicate testing:
  a test would mock a callback rather than assert against a plain list.
- **A dedicated `proxy_config` or `vpn_config` parameter.** Rejected outright. This
  is the app-specific special-casing that prime directive 3 forbids. Deployments
  need entirely different device-side setups:

  - a VPN client
  - an HTTP proxy
  - an MDM profile
  - nothing at all

  Bajutsu should offer one generic hook, rather than grow a parameter per backend
  shape.
- **Extending only `build_package`'s `extra_texts`.** Insufficient alone, without a
  matching pre_test hook. A caller could ship a setup script into the test package.
  Device Farm would never invoke it, though, since nothing in the rendered
  `pre_test.commands` runs it.

## Progress

- [x] Add `pre_test_commands: Sequence[str] = ()` to `render_test_spec`, appended to
  `pre_test.commands` after the existing probe.
- [x] Add a unit test confirming the default (`()`) renders output identical to
  today's.
- [x] Add a unit test confirming passed commands appear verbatim, in order, right
  after the probe.
- [x] Update `render_test_spec`'s docstring for the new parameter and its
  shell-safety boundary.
- [x] Confirm that no `serve` endpoint, no config field, and no `BatchRequest` field
  wires to this parameter (no request-sourced value reaches it) — a unit test asserts
  `BatchRequest` carries no such field.

## References

[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md)
introduces `render_test_spec` and `build_package`.
