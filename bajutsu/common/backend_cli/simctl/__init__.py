"""simctl wrapper — erase / boot / launch / openurl / io.

Command builders are pure and unit-tested. Execution goes through an injectable
runner so the device-touching part stays thin and swappable in tests.
"""

from ._functions import _DEVICE_BLOCKING_SUBCOMMANDS as _DEVICE_BLOCKING_SUBCOMMANDS
from ._functions import _DEVICE_BLOCKING_TIMEOUT_S as _DEVICE_BLOCKING_TIMEOUT_S
from ._functions import _GLOBAL_DOMAIN as _GLOBAL_DOMAIN
from ._functions import _LOCALE_RE as _LOCALE_RE
from ._functions import _SIMCTL_TIMEOUT_S as _SIMCTL_TIMEOUT_S
from ._functions import _logger as _logger
from ._functions import _probe_timed_out as _probe_timed_out
from ._functions import _subcommand_of as _subcommand_of
from ._functions import _timeout_for as _timeout_for
from ._functions import (
    addmedia_cmd,
    boot_cmd,
    booted_udids,
    bootstatus_cmd,
    child_env,
    clear_location_cmd,
    create_cmd,
    create_device,
    data_container_cmd,
    device_available,
    device_booted,
    device_catalog,
    device_error,
    device_type_identifier,
    device_type_label,
    device_type_of,
    erase_cmd,
    export_globals_cmd,
    foreground_cmd,
    get_app_container_cmd,
    home_cmd,
    install_cmd,
    keychain_reset_cmd,
    language_of,
    launch_cmd,
    list_all_devices_cmd,
    list_booted_cmd,
    list_devices_cmd,
    list_devicetypes_cmd,
    locale_args,
    newest_iphone_device_type,
    openurl_cmd,
    pbcopy_cmd,
    pbpaste_cmd,
    privacy_cmd,
    push_cmd,
    real_run,
    record_video_cmd,
    resolve_udid,
    runtime_label,
    screenshot_cmd,
    set_location_cmd,
    shutdown_cmd,
    status_bar_clear_cmd,
    status_bar_override_cmd,
    system_locale_cmds,
    terminate_cmd,
    uninstall_cmd,
    validated_device_arg,
    validated_locale,
    validated_udid,
)
from ._shared import _LANGUAGES_KEY as _LANGUAGES_KEY
from ._shared import _LOCALE_KEY as _LOCALE_KEY
from ._shared import RunFn
from .device_error import DeviceError
from .device_timeout import DeviceTimeout
from .env import _NO_TCC_SERVICE as _NO_TCC_SERVICE
from .env import _PBCOPY_MAX_ATTEMPTS as _PBCOPY_MAX_ATTEMPTS
from .env import _PBCOPY_RETRY_DELAY_S as _PBCOPY_RETRY_DELAY_S
from .env import _PBCOPY_TIMEOUT_EXIT as _PBCOPY_TIMEOUT_EXIT
from .env import _PBCOPY_TIMEOUT_S as _PBCOPY_TIMEOUT_S
from .env import Env

__all__ = [
    "DeviceError",
    "DeviceTimeout",
    "Env",
    "RunFn",
    "addmedia_cmd",
    "boot_cmd",
    "booted_udids",
    "bootstatus_cmd",
    "child_env",
    "clear_location_cmd",
    "create_cmd",
    "create_device",
    "data_container_cmd",
    "device_available",
    "device_booted",
    "device_catalog",
    "device_error",
    "device_type_identifier",
    "device_type_label",
    "device_type_of",
    "erase_cmd",
    "export_globals_cmd",
    "foreground_cmd",
    "get_app_container_cmd",
    "home_cmd",
    "install_cmd",
    "keychain_reset_cmd",
    "language_of",
    "launch_cmd",
    "list_all_devices_cmd",
    "list_booted_cmd",
    "list_devices_cmd",
    "list_devicetypes_cmd",
    "locale_args",
    "newest_iphone_device_type",
    "openurl_cmd",
    "pbcopy_cmd",
    "pbpaste_cmd",
    "privacy_cmd",
    "push_cmd",
    "real_run",
    "record_video_cmd",
    "resolve_udid",
    "runtime_label",
    "screenshot_cmd",
    "set_location_cmd",
    "shutdown_cmd",
    "status_bar_clear_cmd",
    "status_bar_override_cmd",
    "system_locale_cmds",
    "terminate_cmd",
    "uninstall_cmd",
    "validated_device_arg",
    "validated_locale",
    "validated_udid",
]
