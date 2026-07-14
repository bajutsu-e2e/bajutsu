"""Unit tests for the shared AI-path prompt fragments (BE-0246 Unit 5)."""

from bajutsu.ai.prompts import NEVER_JUDGE_BOUNDARY, render_elements


def _el(
    identifier: str | None = None,
    label: str | None = None,
    value: str | None = None,
    traits: list[str] | None = None,
) -> dict[str, object]:
    return {
        "identifier": identifier,
        "label": label,
        "value": value,
        "traits": traits or [],
    }


def test_compact_drops_empty_addressing_fields() -> None:
    lines = render_elements([_el("a", "A")], compact=True)
    assert lines == ["- id='a' label='A'"]


def test_compact_keeps_every_present_field_in_fixed_order() -> None:
    lines = render_elements([_el("f", "Email", "you@x.co", ["textField"])], compact=True)
    assert lines == ["- id='f' label='Email' value='you@x.co' traits=['textField']"]


def test_compact_element_addressable_only_by_traits_still_renders() -> None:
    lines = render_elements([_el(traits=["button"])], compact=True)
    assert lines == ["- traits=['button']"]


def test_verbose_emits_all_four_fields_even_when_empty() -> None:
    lines = render_elements([_el("a", "A")], compact=False)
    assert lines == ["- id='a' label='A' value='' traits=[]"]


def test_application_root_is_skipped_in_both_modes() -> None:
    screen = [_el("root", traits=["application"]), _el("a", "A")]
    assert render_elements(screen, compact=True) == ["- id='a' label='A'"]
    assert render_elements(screen, compact=False) == ["- id='a' label='A' value='' traits=[]"]


def test_element_with_no_addressing_field_is_skipped() -> None:
    assert render_elements([_el()], compact=True) == []
    assert render_elements([_el()], compact=False) == []


def test_missing_traits_key_is_treated_as_empty() -> None:
    # A mapping lacking the `traits` key must not raise (structural, not TypedDict-strict).
    assert render_elements([{"identifier": "a"}], compact=True) == ["- id='a'"]


def test_never_judge_boundary_states_the_prime_directive() -> None:
    assert "pass/fail" in NEVER_JUDGE_BOUNDARY
