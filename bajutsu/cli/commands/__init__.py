"""CLI command modules with no owning feature (`doctor` / `lint` / `schema` / `report`).

Each `<name>.py` defines its command function(s) and a `register(app)` that wires them onto the
shared Typer app (see bajutsu/cli/__init__.py). A command that belongs to a feature lives with it
instead (`bajutsu.run.cli`, `bajutsu.record.cli`, …) — this package is only what is left over.
"""
