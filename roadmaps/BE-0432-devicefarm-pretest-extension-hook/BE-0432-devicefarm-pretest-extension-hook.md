**English** · [日本語](BE-0432-devicefarm-pretest-extension-hook-ja.md)

# BE-0432 — A generic pre_test hook in the Device Farm test spec

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0432](BE-0432-devicefarm-pretest-extension-hook.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0432") |
| Implementing PR | [#2033](https://github.com/bajutsu-e2e/bajutsu/pull/2033) |
| Topic | Device-cloud execution |
| Related | [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md) |
<!-- /BE-METADATA -->

## Introduction

`render_test_spec` renders a Device Farm custom-environment test spec. The spec runs a set of
[scenarios](../../docs/glossary.md#scenario-authoring) on a reserved device.
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md)
introduced the function. It now lives in `bajutsu/common/cloud/devicefarm/_functions.py`.
`bajutsu.common.cloud.devicefarm` re-exports it.

Today the `pre_test` phase of the rendered spec runs one command: the reserved-device visibility
probe. A caller may need the device to do something else first. The work must finish before the
`bajutsu run` calls of the `test` phase start. That caller has no way to add a command to `pre_test`.
The sole alternative is reimplementing everything `render_test_spec` already renders.

This proposal adds a `pre_test_commands` parameter. A caller uses it to splice its own commands into
the `pre_test` phase. Device-side setup specific to one deployment's backend can then live entirely
outside `bajutsu/`.

## Motivation

Prime directive 3 keeps Bajutsu app-agnostic: per-app differences live in configuration, never
inside the tool. A concrete case surfaced this constraint. The case arose during integration with a
staging backend behind an IP allowlist.

Reaching that backend from a Device Farm device needs a per-run authenticated network relay.
A device-side script must configure the relay before the scenario's own traffic starts. This setup
belongs to one deployment's backend. Another deployment might need something different:

- a Virtual Private Network (VPN) client
- a Mobile Device Management (MDM) profile
- nothing at all

None of this setup belongs inside Bajutsu. Bajutsu must stay usable regardless of what a caller's
backend requires.

Today a caller has two options. Both defeat the purpose of a shared, tested `render_test_spec`.

The first option forks the output of `render_test_spec`. To insert a single command, the fork
duplicates the following:

- the Python bootstrap
- the probe
- the `test` and `post_test` phases
- artifact collection
- YAML (YAML Ain't Markup Language) rendering

The second option maintains a parallel test-spec generator outside Bajutsu. That keeps two
implementations of the same shape in sync by hand.

`build_package` already has an `extra_texts` parameter. It lets a caller ship an arbitrary file,
such as a setup script, into the test package. Nothing today makes Device Farm run that file during
`pre_test`.

## Detailed design

`render_test_spec` gains a `pre_test_commands: Sequence[str] = ()` parameter. Each entry appends to
the `pre_test.commands` list, in order, after the existing visibility probe:

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

`render_test_spec` treats each entry as an opaque, already-shell-safe string. A Device Farm test
spec already has this trust boundary, because every phase runs arbitrary shell.

The treatment mirrors how `build_package`'s `extra_texts` passes caller content through verbatim.
One difference stays: `extra_texts` content is inert data inside the zip. A `pre_test` command runs
on the Device Farm machine.

The treatment differs from how `render_test_spec` quotes `scenarios`, `target`, and `config` today.
Those three compose into one command that the function builds itself. They trace back to request
or workflow text input, so the function quotes them. A caller-supplied command list is the
caller's own construction instead.

For that reason, `pre_test_commands` stays a Python-API-only parameter. No `serve` endpoint, no
config field, and no `BatchRequest` field may carry it.

`DeviceFarmBatchProvider` is the one in-tree caller. It builds its `render_test_spec` arguments
from a Hypertext Transfer Protocol (HTTP) request body. Routing this parameter through that path
would hand a client a shell on a machine that holds the run's Amazon Web Services (AWS) role
credentials.

The default `()` renders nothing extra. Every existing caller and every existing test keeps
generating byte-identical output.

`pre_test_commands` pairs with `build_package`'s `extra_texts` parameter:

```python
build_package(entries, out_zip, extra_texts={"configure-proxy.sh": script_text})
```

Together, the two parameters let a caller assemble a full `pre_test` setup: the script content and
the command that runs it. The caller assembles this setup entirely from outside `bajutsu/`.
Bajutsu never inspects or interprets what the setup does.

## Alternatives considered

- **A callback invoked during rendering (`Callable[[], list[str]]`).** Rejected.
  `render_test_spec` is a pure function today: it takes input and returns rendered YAML text.
  A callback would run caller code during rendering. Passing an already-computed list offers the
  same result without that cost. A callback would also complicate testing, because a test would
  mock the callback instead of asserting against a plain list.
- **A dedicated `proxy_config` or `vpn_config` parameter.** Rejected outright. This is the
  app-specific special-casing that prime directive 3 forbids. Deployments need entirely different
  device-side setups:

  - a VPN client
  - an HTTP proxy
  - an MDM profile
  - nothing at all

  Bajutsu should offer one generic parameter rather than grow a new one per backend shape.
- **Extending `build_package`'s `extra_texts` alone.** Insufficient without a matching `pre_test`
  parameter. A caller could ship a setup script into the test package. Nothing in the rendered
  `pre_test.commands` runs it, so Device Farm would never invoke the script.

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
  wires to this parameter (no request-sourced value reaches it) — a unit test walks the
  `render_test_spec` call site's abstract syntax tree (AST), so a renamed `BatchRequest`
  field or a `**mapping` splat still trips the check.
- [x] Reject a bare `str` for `pre_test_commands` — and, symmetrically, for `scenarios` —
  since `Sequence[str]` matches one but iterating it would splice one command per
  character; each rejection has a unit test.

Log:

- [#2033](https://github.com/bajutsu-e2e/bajutsu/pull/2033) — Added `pre_test_commands`
  to `render_test_spec`, appended verbatim (unquoted, like `build_package`'s `extra_texts`)
  to the `pre_test` phase after the visibility probe. Kept it a Python-API-only hook — no
  `serve`, config, or `BatchRequest` field wires to it — and covered the default-identity,
  verbatim-in-order, bare-string-rejection, and no-wiring cases with unit tests. Documented
  the hook in `docs/devicefarm.md` and its Japanese mirror.

## References

[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md)
introduces `render_test_spec` and `build_package`.
