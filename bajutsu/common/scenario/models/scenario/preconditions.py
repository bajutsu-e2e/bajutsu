"""The per-test environment setup a scenario declares."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from bajutsu.common.scenario.models._base import _Model


class Preconditions(_Model):
    """Per-test environment setup."""

    # Wipe the whole simulator (simctl erase) before the test — apps, data, settings. The app is
    # reinstalled fresh each run (see `reinstall`), so a full wipe is only needed when a test wants a
    # pristine device (no other apps / default settings). None (unset) inherits the target config's
    # `erase` and then the built-in off (BE-0177); an explicit true/false pins it for this scenario.
    # `run` resolves this to a concrete bool before dispatch, so `None` behaves as off downstream.
    erase: bool | None = None
    # How the app is (re)installed before each run, when the app config gives an `appPath`:
    #   clean     — uninstall then install (fresh app + data; the default)
    #   overwrite — install over the existing app (keeps its data container)
    reinstall: Literal["clean", "overwrite"] = "clean"
    launch_args: list[str] = Field(default_factory=list, alias="launchArgs")
    launch_env: dict[str, str] = Field(default_factory=dict, alias="launchEnv")
    deeplink: str | None = None
    locale: str | None = None
    setup: str | None = None
    # Image files to seed into the iOS Simulator's photo library before `selectPhotos` addresses it
    # by ordinal position (`Env.add_media`, one `simctl addmedia` call per path, in order). Paths are
    # scenario-file-relative, resolved the same way `dataFile` is (`contained_ref`). Lives here,
    # beside `erase`/`reinstall`, rather than on `Scenario` — the field the seeding gate below already
    # reads, with no separate plumbing through `launch_driver` / `RunEnvironment.start` needed.
    seed_photos: list[str] = Field(default_factory=list, alias="seedPhotos")

    @model_validator(mode="after")
    def _seed_photos_needs_erase(self) -> Self:
        # `_prepare_simulator` only seeds on the cold-and-erase path — the same wipe that already
        # guarantees a known-empty library. Without this check, `seedPhotos` set alongside `erase:
        # false` (or unset) would silently seed nothing, leaving `selectPhotos: { indices: [...] }`
        # to address whatever the Simulator's ambient library happens to contain — non-reproducible
        # by omission rather than by the design the seeding exists to replace (prime directive 2).
        if self.seed_photos and not self.erase:
            raise ValueError("preconditions.seedPhotos requires preconditions.erase: true")
        return self

    def resolved_locale(self, target_locale: str) -> str:
        """The locale this scenario runs under: its own override, else the target config's `locale`.

        The one place the precedence lives, so everything that acts on it agrees — the app's launch
        arguments, the Simulator's own system language, and the system-alert label lookup that
        predicts what SpringBoard renders (BE-0320). Takes the target's value rather than the whole
        config, keeping the scenario schema a portable inner contract.
        """
        return self.locale or target_locale
