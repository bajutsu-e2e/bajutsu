"""The serve-operations facade contract (BE-0127).

`operations` was split from one god-module into a package of resource submodules, with the
package `__init__` re-exporting the full public surface so callers keep reaching everything
through `ops.<name>` unchanged. These tests pin that contract: the facade stays complete, and
each function actually lives in its resource submodule (so the split is real, not aliased away).
"""

from __future__ import annotations

import bajutsu.serve.operations as ops


def test_every_facade_export_is_a_real_attribute() -> None:
    # `__all__` is the advertised surface; a name listed but not bound would break `ops.<name>`.
    for name in ops.__all__:
        assert hasattr(ops, name), name


def test_public_functions_live_in_their_resource_submodule() -> None:
    # A representative function per submodule — their `__module__` proves the body was relocated,
    # not left behind and merely re-exported from a still-monolithic module.
    expected = {
        "config_info": "bajutsu.serve.operations.config",
        "bind_git_config": "bajutsu.serve.operations.config",
        "doctor_check": "bajutsu.serve.operations.doctor",
        "start_run": "bajutsu.serve.operations.dispatch",
        "format_sse": "bajutsu.serve.operations.sse",
        "bind_upload_config": "bajutsu.serve.operations.upload",
        "start_capture": "bajutsu.serve.operations.capture",
        "read_scenario": "bajutsu.serve.operations.reads",
        "start_enrich": "bajutsu.serve.operations.enrich",
        "worker_lease": "bajutsu.serve.operations.worker",
    }
    for name, module in expected.items():
        assert getattr(ops, name).__module__ == module, name


def test_shared_helpers_live_in_common() -> None:
    # The cross-module private helpers are the only ones intentionally hoisted into `_common`;
    # keeping them there is what stops the submodules from importing each other (no cycles).
    from bajutsu.serve.operations import _common

    for name in ("_device_args", "_resolve_org_or_forbid", "_default_driver_factory"):
        assert getattr(_common, name).__module__ == "bajutsu.serve.operations._common", name
