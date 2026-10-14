"""A scenario's preconditions: the target's `app`, `runsOn`, and `run` groups, overridden per scenario.

The groups take the target's names (BE-0452), so a field has one spelling at both levels. The
scenario schema stays a portable inner contract (it never imports the config), so `runsOn` blocks are
kept as written here and checked against each platform's registered model when a run resolves the
scenario against its targets (`bajutsu.common.config.preconditions`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, model_validator

from bajutsu.common.scenario.models._base import _Model

from ._functions import _coerce_system_alert_handling
from .system_alert_handling import SystemAlertHandling

# The on-disk type of a `systemAlertHandling` field, shared by the scenario and the target config so
# the two layers accept exactly the same forms. The boolean carries on and off (BE-0401): `false`
# disables the guard, a mapping is on and holds the policy, and `None` (the key absent) inherits the
# layer above. `{ enabled: false, rules: [...] }` — a policy the runtime discarded without a word —
# is not representable.
SystemAlertHandlingField = Annotated[
    Literal[False] | SystemAlertHandling | None,
    BeforeValidator(_coerce_system_alert_handling),
]

# Where each flat key of the pre-BE-0452 `preconditions` went; the loader reads none of them, and a
# stored run's plan is rewritten by `load_stored_scenario_file` instead.
FLAT_PRECONDITION_KEYS: dict[str, str] = {
    "erase": "preconditions.run.erase",
    "reinstall": "preconditions.app.reinstall",
    "launchEnv": "preconditions.app.launch.env",
    "launchArgs": "preconditions.app.launch.args",
    "deeplink": "preconditions.app.launch.deeplink",
    "locale": "preconditions.runsOn.ios.locale",
    "seedPhotos": "preconditions.runsOn.ios.seedPhotos",
}


class PreconditionsLaunch(_Model):
    """`preconditions.app.launch`: what the scenario adds to the target's launch.

    `env` merges over the target's key by key, `args` append after the target's, and `deeplink`
    replaces the target's.
    """

    env: dict[str, str] = Field(default_factory=dict)
    args: list[str] = Field(default_factory=list)
    deeplink: str | None = None


class PreconditionsApp(_Model):
    """`preconditions.app`: the scenario's override of the target's `app` launch and install fields.

    A scenario cannot set a field that identifies or builds the app (`id`, `path`, `build`,
    `startWhen`, `idNamespaces`); the target owns those, so they fail as unknown here.
    """

    # How the app is (re)installed before the run, when the target gives an `app.path`: `clean`
    # uninstalls first, `overwrite` installs over the existing app and keeps its data container.
    # Unset inherits the target's `app.reinstall`, then the built-in `clean` (BE-0452).
    reinstall: Literal["clean", "overwrite"] | None = None
    launch: PreconditionsLaunch = Field(default_factory=PreconditionsLaunch)


class PreconditionsRun(_Model):
    """`preconditions.run`: the scenario's own run policy (BE-0452).

    Each value sits between a command-line flag and the target's `run`: a flag wins over the
    scenario, and the scenario over the target. `secrets` stays a target-level union, so it is not
    accepted here.
    """

    # Wipe the whole device before the test — apps, data, settings. None (unset) inherits the
    # target's `run.erase` and then the built-in off (BE-0177); `run` resolves it to a concrete bool
    # before dispatch, so `None` behaves as off downstream.
    erase: bool | None = None
    # The alert guard runs on by default; unset means "on, tap the prompt's default button" (see
    # SystemAlertHandling). The scenario's rules are checked before the target's.
    system_alert_handling: SystemAlertHandlingField = Field(
        default=None, alias="systemAlertHandling"
    )
    # Dismiss a blocking TipKit tip when one is in the way; iOS only, so a scenario that drives no
    # iOS target cannot set it. Off unless asked for: a tip is sometimes the very thing a scenario
    # asserts on.
    tip_kit_handling: bool | None = Field(default=None, alias="tipKitHandling")
    # False skips the `hooks.setup` of every target the scenario declares (the target it runs
    # against, when it declares none). The targets' cleanup still runs, since cleanup runs on every
    # path out of a scenario.
    inherit_setup: bool = Field(default=True, alias="inheritSetup")


class Preconditions(_Model):
    """Per-test environment setup, grouped as the target's `app`, `runsOn`, and `run` (BE-0452)."""

    app: PreconditionsApp = Field(default_factory=PreconditionsApp)
    # Keyed by platform, because its fields differ by platform. A block for a platform the run does
    # not drive has no effect. Each block is checked against that platform's registered `runsOn`
    # model, restricted to the fields a scenario may set, when the run resolves the scenario. Paths in
    # an iOS block's `seedPhotos` resolve against the scenario file when it loads.
    runs_on: dict[str, dict[str, Any]] = Field(default_factory=dict, alias="runsOn")
    run: PreconditionsRun = Field(default_factory=PreconditionsRun)

    @model_validator(mode="before")
    @classmethod
    def _reject_flat_keys(cls, data: Any) -> Any:
        # BE-0452 regrouped the flat keys and reads none of them, so name each one's new home rather
        # than leaving Pydantic's generic extra-field error.
        if isinstance(data, dict):
            for old, new in FLAT_PRECONDITION_KEYS.items():
                if old in data:
                    raise ValueError(f"scenario field 'preconditions.{old}' moved to '{new}'")
        return data

    def runs_on_for(self, platform: str) -> dict[str, Any]:
        """The scenario's `runsOn` block for *platform*, or an empty block when it writes none."""
        return self.runs_on.get(platform) or {}

    def resolved_locale(self, target_locale: str, platform: str = "ios") -> str:
        """The locale this scenario runs under: its own `runsOn` value, else the target's locale.

        The one place the precedence lives, so everything that acts on it agrees — the app's launch
        arguments, the Simulator's own system language, and the system-alert label lookup that
        predicts what SpringBoard renders (BE-0320). Takes the target's value rather than the whole
        config, keeping the scenario schema a portable inner contract.

        Args:
            target_locale: The target's resolved `runsOn.locale`.
            platform: The driven target's platform, whose `runsOn` block may override the locale.
        """
        locale = self.runs_on_for(platform).get("locale")
        return locale if isinstance(locale, str) and locale else target_locale

    def resolve_seed_photos(self, resolve: Callable[[str], str]) -> None:
        """Rewrite every `runsOn` block's `seedPhotos` through *resolve*, in place.

        A file loader passes the scenario-file-relative resolution `dataFile` uses (`contained_ref`),
        so a path never escapes the suite root and no later reader depends on the working directory.
        """
        for block in self.runs_on.values():
            photos = block.get("seedPhotos")
            if isinstance(photos, list):
                block["seedPhotos"] = [resolve(str(ref)) for ref in photos]

    def seed_photos_for(self, platform: str) -> list[str] | None:
        """The scenario's `seedPhotos` for *platform*, or None when the scenario does not set them."""
        photos = self.runs_on_for(platform).get("seedPhotos")
        return [str(p) for p in photos] if isinstance(photos, list) else None
