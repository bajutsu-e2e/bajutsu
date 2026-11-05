"""The per-user signing file that drives a device runner build (BE-0456).

Signing belongs to the person running Bajutsu, not to a target: the runner is generic, and the
target config is committed and shareable, so a team ID written there would reach every other user.
The file therefore lives outside the repository and is found by an explicit path, an environment
variable, or the XDG config location, in that order.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from bajutsu.common import _yaml

from .errors import DeviceRunnerError

SIGNING_FILE_ENV = "BAJUTSU_SIGNING_FILE"

# Derived from `bundleIdPrefix`. The `bajutsu.` segment keeps the runner's identifiers apart from the
# user's own apps under the same prefix, whose install the runner would otherwise overwrite.
_HOST_SUFFIX = "bajutsu.runner-host"
_UITESTS_SUFFIX = "bajutsu.runner-uitests"


class SigningError(DeviceRunnerError):
    """The signing file is missing, unreadable, or invalid."""


class _Model(BaseModel):
    # Strict like the target config: a misspelt key is an error, never a silently ignored setting.
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class BundleIds(_Model):
    """The two runner identifiers, named outright instead of derived from a prefix."""

    host: str = Field(min_length=1)
    uitests: str = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if self.host == self.uitests:
            raise ValueError("bundleIds.host and bundleIds.uitests must differ")
        return self


class ManualProfiles(_Model):
    """One provisioning profile per signed product."""

    host: str = Field(min_length=1)
    # The UI-test bundle's `.xctrunner` app, the product a device actually installs and launches.
    runner: str = Field(min_length=1)


class ManualSigning(_Model):
    """The certificate and profile(s) a manual-signing build pins."""

    identity: str = Field(min_length=1)
    profile: str | None = Field(default=None, min_length=1)
    profiles: ManualProfiles | None = None

    @model_validator(mode="after")
    def _one_profile_form(self) -> Self:
        if (self.profile is None) == (self.profiles is None):
            raise ValueError("manual signing needs exactly one of `profile` or `profiles`")
        return self

    def host_profile(self) -> str:
        """The profile name or UUID that signs the runner host app."""
        return self.profile or self._profiles().host

    def runner_profile(self) -> str:
        """The profile name or UUID that signs the UI-test bundle's ``.xctrunner`` app."""
        return self.profile or self._profiles().runner

    def _profiles(self) -> ManualProfiles:
        assert self.profiles is not None  # guaranteed by _one_profile_form when profile is unset
        return self.profiles


class SigningConfig(_Model):
    """A validated signing file: the runner's identifiers, the team, and how to sign."""

    bundle_id_prefix: str | None = Field(default=None, alias="bundleIdPrefix", min_length=1)
    bundle_ids: BundleIds | None = Field(default=None, alias="bundleIds")
    team_id: str = Field(alias="teamId", min_length=1)
    signing: Literal["automatic", "manual"] = "automatic"
    manual: ManualSigning | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.bundle_id_prefix is None) == (self.bundle_ids is None):
            raise ValueError("give exactly one of `bundleIdPrefix` or `bundleIds`")
        if self.signing == "manual" and self.manual is None:
            raise ValueError("`signing: manual` needs a `manual` block with `identity`")
        return self

    @property
    def host_bundle_id(self) -> str:
        """The runner host app's bundle identifier."""
        if self.bundle_ids is not None:
            return self.bundle_ids.host
        return f"{self.bundle_id_prefix}.{_HOST_SUFFIX}"

    @property
    def uitests_bundle_id(self) -> str:
        """The UI-test bundle's identifier; its ``.xctrunner`` app appends ``.xctrunner``."""
        if self.bundle_ids is not None:
            return self.bundle_ids.uitests
        return f"{self.bundle_id_prefix}.{_UITESTS_SUFFIX}"

    @property
    def manual_signing(self) -> ManualSigning | None:
        """The manual block when this file signs manually, else ``None`` (an unused block is inert)."""
        return self.manual if self.signing == "manual" else None


def default_signing_path(env: Mapping[str, str] | None = None) -> Path:
    """``$XDG_CONFIG_HOME/bajutsu/signing.yaml``, falling back to ``~/.config/bajutsu/signing.yaml``."""
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "bajutsu" / "signing.yaml"


def find_signing_file(
    explicit: Path | None = None, env: Mapping[str, str] | None = None
) -> Path | None:
    """Locate the signing file: *explicit*, then ``BAJUTSU_SIGNING_FILE``, then the XDG default.

    The first source that names a file wins. A path named explicitly or by the environment variable
    must exist — it is a stated intent, and falling through to the default would build under a
    different identity than the user asked for — while an absent default file simply means "none".

    Args:
        explicit: The ``--signing`` path; only ``bajutsu runner build`` passes one.
        env: The environment to read; defaults to ``os.environ``.

    Returns:
        The signing file's path, or ``None`` when no source names one and the default is absent.

    Raises:
        SigningError: An explicit or environment-named path does not exist, or the default path
            exists but is not a file.
    """
    env = os.environ if env is None else env
    if explicit is not None:
        if not explicit.is_file():
            raise SigningError(f"signing file not found: {explicit} (from --signing)")
        return explicit
    named = env.get(SIGNING_FILE_ENV)
    if named:
        path = Path(named).expanduser()
        if not path.is_file():
            raise SigningError(f"signing file not found: {path} (from ${SIGNING_FILE_ENV})")
        return path
    default = default_signing_path(env)
    if default.exists() and not default.is_file():
        raise SigningError(f"signing file path is not a file: {default}")
    return default if default.is_file() else None


def describe_lookup(env: Mapping[str, str] | None = None) -> str:
    """The two locations a run consults, for an error that has to say where it looked."""
    return f"${SIGNING_FILE_ENV} or {default_signing_path(env)}"


def load_signing(path: Path) -> SigningConfig:
    """Read and validate the signing file at *path*.

    Raises:
        SigningError: The file cannot be read, is not a YAML mapping, or fails validation.
    """
    try:
        raw = _yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise SigningError(f"cannot read signing file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SigningError(f"signing file {path} must be a YAML mapping")
    try:
        return SigningConfig.model_validate(raw)
    except ValidationError as exc:
        raise SigningError(f"invalid signing file {path}:\n{exc}") from exc
