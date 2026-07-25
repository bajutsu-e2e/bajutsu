# Captured real-model fixtures (BE-0295)

This directory holds real-model tool-use responses captured once and replayed as permanent
regression fixtures for the `record` and `crawl` propose loops
([BE-0295](../../../roadmaps/BE-0295-record-crawl-real-model-verification/BE-0295-record-crawl-real-model-verification.md)).

A committed `record.json` / `crawl.json` is the raw tool-use a real model produced for the showcase
`controls` screen — the one shape a hand-built `FakeBlock` cannot supply, because a fake is only ever
the shape a test author expected. Once present, `tests/test_real_model_fixtures.py` replays each on
every run (`test_committed_*_fixture_parses`); while absent, those tests skip (signal-first, the
BE-0282 precedent), so the deterministic gate stays hermetic.

## Capturing a fixture

Capturing needs a real credential (`ANTHROPIC_API_KEY` or a configured `ai.provider`), so it is not
part of the default gate. With one configured, run the key-gated capture tests:

```bash
uv run pytest tests/test_real_model_fixtures.py -m live -k capture
```

Each writes its response here (`record.json`, `crawl.json`). Review the captured payload, then commit
it so the replay tests activate for everyone.
