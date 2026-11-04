# Bajutsu API reference

This page lists the public surface of the Python core. The reference is generated from docstrings and **typed signatures**.
The generator reads them statically and imports no module, so the lazy and optional backends are never loaded.

`make docs` builds this reference, and the deterministic `make check` gate does **not** include it.
It complements the hand-written, bilingual guides under [`docs/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/docs):
those guides explain concepts and workflows, and this reference maps the public API.

- **[Driver & selectors](drivers.md)** — the backend-agnostic `Driver` protocol and the shared
  `Selector` and `Element` types.
- **[Runner](runner.md)** — running scenarios through a device pool and writing the report.
- **[Assertions](assertions.md)** — the machine-checkable assertions the deterministic runner evaluates.
- **[Network](network.md)** — the `NetworkExchange` model that the `request` and `event` assertions match.
