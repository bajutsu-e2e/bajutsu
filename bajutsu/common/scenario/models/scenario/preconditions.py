"""A scenario's preconditions: the target's `app` and `run` groups overridden, and the device state.

`app` and `run` take the target's names (BE-0452), so a field has one spelling at both levels. Where
a run happens belongs to the target alone (BE-0469): a scenario names a target that declares the
`runsOn` it needs, and sets only the device state it applies under `device`.
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
    "erase": "preconditions.app.reinstall: erase",
    "reinstall": "preconditions.app.reinstall",
    "launchEnv": "preconditions.app.launch.env",
    "launchArgs": "preconditions.app.launch.args",
    "deeplink": "preconditions.app.launch.deeplink",
    "locale": "preconditions.device.ios.locale",
    "seedPhotos": "preconditions.device.ios.seedPhotos",
}

# The device state BE-0469 moved from `preconditions.runsOn.<platform>` to `preconditions.device.ios`.
_DEVICE_STATE = ("locale", "seedPhotos")


def _runs_on_moves(runs_on: Any) -> list[str]:
    """Where each field of a scenario's former `preconditions.runsOn` went (BE-0469).

    Device state moved to `preconditions.device.ios`; a condition has no scenario-level home, so its
    message points at the target's `runsOn` path instead.
    """
    if not isinstance(runs_on, dict):
        return ["preconditions.runsOn was removed: name a target whose runsOn declares the device"]
    moves: list[str] = []
    for platform, block in runs_on.items():
        for key in block if isinstance(block, dict) else ():
            old = f"preconditions.runsOn.{platform}.{key}"
            if key in _DEVICE_STATE and platform == "ios":
                moves.append(f"{old} moved to preconditions.device.ios.{key}")
            elif key in _DEVICE_STATE:
                # Only iOS ever read a scenario's device state, so another platform's has no home.
                moves.append(
                    f"{old} was removed: platform {platform!r} takes no scenario device state"
                )
            else:
                # The web block already sits at its destination (`browser`). This mirrors
                # `condition_path` in `common/devices/runs.py`, which the portable scenario schema
                # may not import.
                where = f"runsOn.{key}" if platform == "web" else f"runsOn.{platform}.{key}"
                moves.append(
                    f"{old} was removed: a scenario no longer declares where it runs; "
                    f"name a target whose {where} declares it"
                )
    return moves or ["preconditions.runsOn was removed: name a target that declares its runsOn"]


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

    # How much state the run wipes before launch, weakest first (BE-0473): `overwrite` installs over
    # the existing app and keeps its data container, `clean` uninstalls first, `erase` wipes the whole
    # device (on Android, the same as `clean`). Unset inherits the target's `app.reinstall`, then the
    # built-in `clean` (BE-0452).
    reinstall: Literal["overwrite", "clean", "erase"] | None = None
    launch: PreconditionsLaunch = Field(default_factory=PreconditionsLaunch)


class PreconditionsRun(_Model):
    """`preconditions.run`: the scenario's own run policy (BE-0452).

    Each value sits between a command-line flag and the target's `run`: a flag wins over the
    scenario, and the scenario over the target. `secrets` stays a target-level union, so it is not
    accepted here.
    """

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

    @model_validator(mode="before")
    @classmethod
    def _reject_moved_keys(cls, data: Any) -> Any:
        # The loader reads no old key (BE-0473), so the error is the migration path.
        if isinstance(data, dict) and "erase" in data:
            raise ValueError(
                "preconditions.run.erase moved to preconditions.app.reinstall (BE-0473): "
                "write `erase` for true, and `clean` or `overwrite` for false"
            )
        return data


class PreconditionsIosDevice(_Model):
    """`preconditions.device.ios`: the iOS device state a scenario applies over its target's (BE-0469).

    Each field replaces the target's `runsOn.ios` value of the same name when set.
    """

    # The app's and the Simulator's language; a value that differs from the target's forces a cold
    # respawn (BE-0320).
    # An empty value would silently fall back to the target's locale, so it fails instead.
    locale: str | None = Field(default=None, min_length=1)
    # Images seeded into the photo library on the erase path, in order. Paths resolve against the
    # scenario file when it loads; None keeps the target's list, and the merged list needs an
    # effective `app.reinstall: erase`.
    seed_photos: list[str] | None = Field(default=None, alias="seedPhotos")


class PreconditionsDevice(_Model):
    """`preconditions.device`: device state keyed by destination; only iOS has any (BE-0469).

    A block for a platform the run does not drive has no effect.
    """

    ios: PreconditionsIosDevice = Field(default_factory=PreconditionsIosDevice)


class Preconditions(_Model):
    """Per-test environment setup: the target's `app` and `run` overridden, and the device state."""

    app: PreconditionsApp = Field(default_factory=PreconditionsApp)
    device: PreconditionsDevice = Field(default_factory=PreconditionsDevice)
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
            if "runsOn" in data:
                raise ValueError("; ".join(_runs_on_moves(data["runsOn"])))
        return data

    def _ios_device(self, platform: str) -> PreconditionsIosDevice | None:
        # The device block that applies to a target driven as *platform*; only iOS has one.
        return self.device.ios if platform == "ios" else None

    def resolved_locale(self, target_locale: str, platform: str = "ios") -> str:
        """The locale this scenario runs under: its own `device` value, else the target's locale.

        The one place the precedence lives, so everything that acts on it agrees — the app's launch
        arguments, the Simulator's own system language, and the system-alert label lookup that
        predicts what SpringBoard renders (BE-0320). Takes the target's value rather than the whole
        config, keeping the scenario schema a portable inner contract.

        Args:
            target_locale: The target's resolved `runsOn.ios.locale`.
            platform: The platform the target is driven as; only an iOS one reads the scenario's
                `device.ios.locale`.
        """
        device = self._ios_device(platform)
        locale = device.locale if device is not None else None
        return locale or target_locale

    def resolve_seed_photos(self, resolve: Callable[[str], str]) -> None:
        """Rewrite `device.ios.seedPhotos` through *resolve*, in place.

        A file loader passes the scenario-file-relative resolution `dataFile` uses (`contained_ref`),
        so a path never escapes the suite root and no later reader depends on the working directory.
        """
        photos = self.device.ios.seed_photos
        if photos is not None:
            self.device.ios.seed_photos = [resolve(ref) for ref in photos]

    def seed_photos_for(self, platform: str) -> list[str] | None:
        """The scenario's `seedPhotos` for *platform*, or None when the scenario does not set them."""
        device = self._ios_device(platform)
        return list(device.seed_photos) if device and device.seed_photos is not None else None
