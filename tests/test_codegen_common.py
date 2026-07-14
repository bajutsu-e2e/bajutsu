"""Tests for the shared codegen helpers (BE-0255): identifier / class-name / duration / regex."""

from __future__ import annotations

from bajutsu.codegen.common import (
    class_name,
    ident,
    is_plain_substring,
    ms,
    network_unsupported,
)


def test_ident_sanitizes_and_prefixes() -> None:
    assert ident("Login flow") == "test_Login_flow"
    assert ident("my-flow_v2") == "test_my_flow_v2"
    assert ident("!!!") == "test_scenario"
    # A method identifier cannot start with a digit — prefixed `_`.
    assert ident("2fa flow") == "test__2fa_flow"


def test_class_name_takes_suffix_and_guards_digit() -> None:
    assert class_name("login_flow", "UITests") == "LoginFlowUITests"
    assert class_name("login_flow", "UITest") == "LoginFlowUITest"
    assert class_name("!!!", "UITests") == "GeneratedUITests"
    # The digit-prefix guard applies to every target, not just UI Automator (BE-0255).
    assert class_name("2fa_flow", "UITests") == "_2FaFlowUITests"


def test_ms_truncates_to_int() -> None:
    assert ms(1.0) == 1000
    assert ms(0.1) == 100
    assert ms(2.5) == 2500


def test_is_plain_substring() -> None:
    assert is_plain_substring("Submit")
    assert is_plain_substring("hello world")
    assert not is_plain_substring("Sub.*mit")
    assert not is_plain_substring("^start")


def test_network_unsupported_names_the_subject() -> None:
    assert network_unsupported("XCUITest") == (
        "XCUITest has no network interception; assert via a mock/proxy; not generated"
    )
    assert network_unsupported("the adb backend") == (
        "the adb backend has no network interception; assert via a mock/proxy; not generated"
    )
