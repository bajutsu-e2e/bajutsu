# Feature examples

**English** · [日本語](README.ja.md)

> **Looking for the Web UI tour?** To drive a real Simulator from the browser and collect every
> evidence type (screenshots, video, logs, network, visual regression, system alerts), see
> **[demos/showcase/WEBUI.md](../showcase/WEBUI.md)** — the headline demo for iOS developers. The
> map of all demos is in [`demos/README.md`](../README.md).

The scenario-authoring features — tags, parameterized shared steps, data-driven runs, secret
variables, device control — shown in three ways.

## On a real Simulator (`make -C demos features`)

Runs the feature scenarios against the [showcase](../showcase/README.md) app on a booted
Simulator via idb — tags + a parameterized shared component + a secret variable. Deterministic,
**no API key**:

```bash
make -C demos features
```

This runs the `smoke`-tagged scenarios of
[`demos/showcase/scenarios/menu/features.yaml`](../showcase/scenarios/menu/features.yaml)
(excluding the `slow` one), expanding the shared "navigate + seed" component
[`_components/search_for.yaml`](../showcase/scenarios/menu/_components/search_for.yaml) and
resolving `${secrets.PASSWORD}` from the environment (the literal value is masked in every run
artifact). Try `--tag` / `--exclude` to pick a different subset.

## No Mac? The full catalog on a fake device (FakeDriver)

Drives the real `load → expand → run` pipeline against the in-memory FakeDriver and prints what
each feature did — no Simulator or idb needed, and it covers the **full** set (tags, shared
steps, data-driven, secrets, device control):

```bash
uv run python demos/features/run_demo.py
```

The per-feature scenario files it loads: [`tags.yaml`](tags.yaml),
[`shared_steps.yaml`](shared_steps.yaml) (+ [`_components/login.yaml`](_components/login.yaml)),
[`data_driven.yaml`](data_driven.yaml), [`secrets.yaml`](secrets.yaml),
[`device.yaml`](device.yaml).

> The offline catalog above is the FakeDriver feature reference; the on-device feature tour is
> the `make -C demos features` showcase run at the top of this page. (The older `sample`-app
> feature scenarios in this directory are superseded by the showcase and slated for removal —
> BE-0079.)
