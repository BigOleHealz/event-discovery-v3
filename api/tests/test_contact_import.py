import pytest

from app.contact_import import normalize_phone


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("4155552671", "+14155552671"),
        ("(415) 555-2671", "+14155552671"),
        ("1 (415) 555-2671", "+14155552671"),
        ("+1 (415) 555-2671", "+14155552671"),
        ("4165550123", "+14165550123"),
        ("+44 20 7946 0958", "+442079460958"),
        ("tel:+44-20-7946-0958", "+442079460958"),
    ],
)
def test_normalize_phone_defaults_to_one_and_preserves_explicit_codes(
    value: str, expected: str
) -> None:
    assert normalize_phone(value) == expected


@pytest.mark.parametrize(
    "value", ["12345", "5552671", "not a number", "+999123456789", "4155552671 ext 9"]
)
def test_normalize_phone_still_rejects_invalid_numbers_and_extensions(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_phone(value)
