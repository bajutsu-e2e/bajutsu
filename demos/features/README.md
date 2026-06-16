# Feature examples

**English** · [日本語](README.ja.md)

> **Looking for the Web UI tour?** To drive a real Simulator from the browser and collect every
> evidence type (screenshots, video, logs, network, visual regression, system alerts), see
> **[WEBUI.md](WEBUI.md)** — the headline demo for iOS developers. The map of all demos is in
> [`demos/README.md`](../README.md).

The scenario-authoring features — tags, parameterized shared steps, data-driven runs, secret
variables, device control — shown in three ways.

## On a real Simulator (`make -C demos features`)

Runs the feature scenarios against the dedicated [`demo` app](../app/README.md) on a booted
Simulator via idb — tags + a parameterized shared `login` component + a secret variable.
Deterministic, **no API key**:

```bash
make -C demos features
```

This runs the `smoke`-tagged scenarios of
[`demos/app/scenarios/features.yaml`](../app/scenarios/features.yaml) (excluding the `slow`
one), expanding the shared [`_components/login.yaml`](../app/scenarios/_components/login.yaml)
and resolving `${secrets.PASSWORD}` from the environment (the literal value is masked in every
run artifact). Try `--tag` / `--exclude` to pick a different subset.

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

## On the sample app (idb)

[`sample_features.yaml`](sample_features.yaml) runs against the bundled `BajutsuSample`
app. It exercises shared steps + secret input + device control entirely on the **settled
Home screen** (`SAMPLE_LOGGED_IN=1`), so the assertions are deterministic.

```bash
# Build the sample first (make -C demos/features sample-build; see demos/features/app/README.md), then:
PASSWORD='s3cr3t' uv run bajutsu run --scenario demos/features/sample_features.yaml \
  --app sample --config demos/features/demo.config.yaml --no-erase --no-network
```

[`demo.config.yaml`](demo.config.yaml) declares `secrets: [PASSWORD]` so
`${secrets.PASSWORD}` resolves from the environment; its literal value is masked in every
run artifact.

> **Note on the Home screen:** idb's `describe-all` can momentarily return an empty
> accessibility tree during a screen transition (e.g. right after a login submit), making
> a `wait`/`expect` on the destination flaky — unrelated to the features above. Asserting
> on a settled screen avoids that. Hardening the idb driver to retry on an empty tree is
> tracked as M1 on-device work.
