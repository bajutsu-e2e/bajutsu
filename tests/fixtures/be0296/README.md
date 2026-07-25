# BE-0296 triage real-model fixtures

A captured real-model `diagnose` response for the `triage --ai` path, replayed as a permanent
regression fixture by `tests/test_real_model_triage_fixtures.py`. A hand-built `FakeBlock` is only
ever the shape the test author expected; this file is the shape a real model actually produced.

- `triage.json` — the `diagnose` tool-use blocks (`[{ "name": "diagnose", "input": {...} }]`)
  captured from a live run over the committed showcase `controls` golden with a selector-rename
  failure. The committed-replay test skips whenever this file is absent.

## Capturing / refreshing the fixture

The capture test is key-gated (`@pytest.mark.live` + a credential gate), so it is deselected by
default. Any configured AI provider works. Inside a Claude Code session the `claude-code` backend
needs no API key — it shells out to the `claude` CLI and reuses its own credential:

```bash
BAJUTSU_AI_PROVIDER=claude-code uv run pytest tests/test_real_model_triage_fixtures.py \
  -m live -k capture
```

With an Anthropic API key instead, set `ANTHROPIC_API_KEY` and drop the `BAJUTSU_AI_PROVIDER`
override. The test asserts the diagnosis parses, then round-trips the saved payload back through the
replay path so a broken capture fails fast rather than committing a fixture that cannot be reloaded.
