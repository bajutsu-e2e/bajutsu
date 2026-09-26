"""Tests for serve's batch-provider bootstrap: register the Device Farm concrete from the env (BE-0336).

serve's fan-out resolves a cloud-batch job's provider by kind; this bootstrap is what fills the
registry with the real AWS-backed ``devicefarm`` provider when — and only when — the environment names
a Device Farm project. The boto3 client and presigned-URL transfer are monkeypatched here so the
wiring is exercised without the ``aws`` extra or any real AWS call.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from bajutsu.serve import batch_bootstrap
from bajutsu.serve import batch_provider as bp


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    # The registry is process-global; snapshot and restore it so a registration in one test never
    # leaks into another (mirrors test_batch_provider.py and the serve env-snapshot fixtures).
    saved = dict(bp._PROVIDERS)
    try:
        yield
    finally:
        bp._PROVIDERS.clear()
        bp._PROVIDERS.update(saved)


def test_source_root_points_at_the_checkout_root() -> None:
    # Serving from this checkout, the Device Farm package root is the source tree that holds
    # pyproject.toml + tests/ (what the test spec `pip install`s and Device Farm validates), not the
    # config's own directory. The check runs against the real tree — no mock — since that is the exact
    # thing being asserted.
    root = batch_bootstrap.bajutsu_source_root()
    assert root is not None
    assert (root / "pyproject.toml").is_file()
    assert (root / "tests").is_dir()
    assert (root / "bajutsu").is_dir()


def _recording_client(regions: list[str]) -> Callable[[str], object]:
    """A `_make_devicefarm_client` stand-in that records the region it was asked for."""

    def make(region: str) -> object:
        regions.append(region)
        return object()

    return make


def test_registers_devicefarm_when_project_arn_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    regions: list[str] = []
    monkeypatch.setattr(
        batch_bootstrap,
        "_make_devicefarm_client",
        _recording_client(regions),
    )
    monkeypatch.setattr(batch_bootstrap, "HttpTransfer", object)

    # The ARN embeds a *different* region on purpose: the default must come from the code, not be
    # parsed back out of the ARN, so an ARN naming ap-northeast-1 must still reach the client as
    # us-west-2 (Device Farm's control plane lives only there).
    registered = batch_bootstrap.register_batch_providers(
        {"DEVICEFARM_PROJECT_ARN": "arn:aws:devicefarm:ap-northeast-1:1:project:abc"}
    )

    assert registered == ["devicefarm"]
    provider = bp.resolve("devicefarm")
    assert isinstance(provider, bp.DeviceFarmBatchProvider)
    assert provider._project_arn == "arn:aws:devicefarm:ap-northeast-1:1:project:abc"
    assert regions == ["us-west-2"]


def test_region_override_reaches_the_client_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    regions: list[str] = []
    monkeypatch.setattr(
        batch_bootstrap,
        "_make_devicefarm_client",
        _recording_client(regions),
    )
    monkeypatch.setattr(batch_bootstrap, "HttpTransfer", object)

    # ARN region and the override differ, so the region reaching the client must be DEVICEFARM_REGION,
    # not the one embedded in the ARN.
    batch_bootstrap.register_batch_providers(
        {
            "DEVICEFARM_PROJECT_ARN": "arn:aws:devicefarm:us-east-1:1:project:abc",
            "DEVICEFARM_REGION": "eu-west-1",
        }
    )

    assert regions == ["eu-west-1"]


def test_no_devicefarm_without_project_arn(monkeypatch: pytest.MonkeyPatch) -> None:
    # No project ARN → nothing registered, so a mis-dispatched cloud-batch job fails loud at resolve()
    # rather than silently vanishing (fail-closed, prime directive 2).
    def _boom(_region: str) -> object:
        raise AssertionError("the client factory must not run without a project ARN")

    monkeypatch.setattr(batch_bootstrap, "_make_devicefarm_client", _boom)

    registered = batch_bootstrap.register_batch_providers({})

    assert registered == []
    with pytest.raises(ValueError, match="unknown batch provider 'devicefarm'"):
        bp.resolve("devicefarm")


def test_register_batch_providers_raises_when_client_factory_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `register_batch_providers` propagates a client-factory failure (missing boto3, bad credentials)
    # so the serve() caller can catch it and keep the process running rather than dying at boot.
    from bajutsu.common.cloud.devicefarm import DeviceFarmError

    monkeypatch.setattr(
        batch_bootstrap,
        "_make_devicefarm_client",
        lambda _region: (_ for _ in ()).throw(DeviceFarmError("cloud-batch dispatch needs boto3")),
    )
    monkeypatch.setattr(batch_bootstrap, "HttpTransfer", object)

    with pytest.raises(DeviceFarmError, match="boto3"):
        batch_bootstrap.register_batch_providers(
            {"DEVICEFARM_PROJECT_ARN": "arn:aws:devicefarm:us-west-2:1:project:abc"}
        )


# ---------------------------------------------------------------------------
# BAJUTSU_BATCH_HOOKS loading (BE-0435)
# ---------------------------------------------------------------------------


def _patch_aws(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the AWS seams so register_batch_providers doesn't need boto3 or HTTP."""
    monkeypatch.setattr(batch_bootstrap, "_make_devicefarm_client", _recording_client([]))
    monkeypatch.setattr(batch_bootstrap, "HttpTransfer", object)


def test_no_hooks_env_var_produces_empty_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_aws(monkeypatch)
    batch_bootstrap.register_batch_providers(
        {"DEVICEFARM_PROJECT_ARN": "arn:aws:devicefarm:us-west-2:1:project:abc"}
    )
    provider = bp.resolve("devicefarm")
    assert isinstance(provider, bp.DeviceFarmBatchProvider)
    assert provider._hooks == []


def test_bajutsu_batch_hooks_loads_hook_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    # BAJUTSU_BATCH_HOOKS=module:factory → importlib loads the module, calls factory(), wires result.
    import sys
    from types import ModuleType

    _patch_aws(monkeypatch)
    sentinel = object()
    fake_mod = ModuleType("_test_hook_module_single")
    fake_mod.make_hook = lambda: sentinel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_test_hook_module_single", fake_mod)

    batch_bootstrap.register_batch_providers(
        {
            "DEVICEFARM_PROJECT_ARN": "arn:aws:devicefarm:us-west-2:1:project:abc",
            "BAJUTSU_BATCH_HOOKS": "_test_hook_module_single:make_hook",
        }
    )

    provider = bp.resolve("devicefarm")
    assert isinstance(provider, bp.DeviceFarmBatchProvider)
    assert provider._hooks == [sentinel]


def test_bajutsu_batch_hooks_loads_multiple_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    from types import ModuleType

    _patch_aws(monkeypatch)
    hook_a = object()
    hook_b = object()
    mod_a = ModuleType("_test_hook_mod_a")
    mod_a.factory = lambda: hook_a  # type: ignore[attr-defined]
    mod_b = ModuleType("_test_hook_mod_b")
    mod_b.factory = lambda: hook_b  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_test_hook_mod_a", mod_a)
    monkeypatch.setitem(sys.modules, "_test_hook_mod_b", mod_b)

    batch_bootstrap.register_batch_providers(
        {
            "DEVICEFARM_PROJECT_ARN": "arn:aws:devicefarm:us-west-2:1:project:abc",
            "BAJUTSU_BATCH_HOOKS": "_test_hook_mod_a:factory, _test_hook_mod_b:factory",
        }
    )

    provider = bp.resolve("devicefarm")
    assert isinstance(provider, bp.DeviceFarmBatchProvider)
    assert provider._hooks == [hook_a, hook_b]


def test_bajutsu_batch_hooks_invalid_format_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # An entry without the module:factory colon separator must raise, not silently skip.
    _patch_aws(monkeypatch)

    with pytest.raises(ValueError, match="module:factory"):
        batch_bootstrap.register_batch_providers(
            {
                "DEVICEFARM_PROJECT_ARN": "arn:aws:devicefarm:us-west-2:1:project:abc",
                "BAJUTSU_BATCH_HOOKS": "bad_entry_no_colon",
            }
        )
