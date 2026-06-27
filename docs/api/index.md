# Bajutsu API reference

The public surface of the Python core, generated from its docstrings and **typed signatures**
(read statically — no module is imported — so the lazy/optional backends are never loaded).

This reference is built by `make docs` and is **not** part of the deterministic `make check` gate.
It complements the hand-written, bilingual guides under [`docs/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/docs):
those explain concepts and workflows; this maps the public API.

- **[Driver & selectors](drivers.md)** — the backend-agnostic `Driver` protocol and the shared
  `Selector` / `Element` types.
- **[Runner](runner.md)** — running scenarios through a device pool and writing the report.
- **[Assertions](assertions.md)** — the machine-checkable assertions the deterministic runner evaluates.
- **[Network](network.md)** — the `NetworkExchange` model the `request` / `event` assertions match.
