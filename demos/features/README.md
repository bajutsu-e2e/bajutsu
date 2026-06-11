# Feature examples

Runnable demos for the scenario-authoring features (tags, parameterized shared steps,
data-driven runs, secret variables, device control).

## Run without a Simulator (FakeDriver)

Drives the real `load → expand → run` pipeline against the in-memory FakeDriver and
prints what each feature did — no Simulator or idb needed:

```bash
uv run python demos/features/run_demo.py
```

The per-feature scenario files it loads: [`tags.yaml`](tags.yaml),
[`shared_steps.yaml`](shared_steps.yaml) (+ [`_components/login.yaml`](_components/login.yaml)),
[`data_driven.yaml`](data_driven.yaml), [`secrets.yaml`](secrets.yaml),
[`device.yaml`](device.yaml).

## Run on a real Simulator (idb backend)

[`sample_features.yaml`](sample_features.yaml) runs against the bundled `BajutsuSample`
app. It exercises shared steps + secret input + device control entirely on the **settled
Home screen** (`SAMPLE_LOGGED_IN=1`), so the assertions are deterministic.

```bash
# Build + install the sample first (see sample/README.md), then:
PASSWORD='s3cr3t' uv run bajutsu run demos/features/sample_features.yaml \
  --app sample --config demos/features/demo.config.yaml --no-erase --no-network
```

[`demo.config.yaml`](demo.config.yaml) declares `secrets: [PASSWORD]` so
`${secrets.PASSWORD}` resolves from the environment; its literal value is masked in every
run artifact.

> **Why the Home screen and not a login flow?** idb's `describe-all` can momentarily
> return an empty accessibility tree *during* a screen transition (e.g. right after a
> login submit), which makes a `wait`/`expect` on the destination flaky — unrelated to the
> features above. Asserting on a settled screen avoids that. Hardening the idb driver to
> retry on an empty tree is tracked as M1 on-device work.
