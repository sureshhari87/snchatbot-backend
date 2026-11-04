import pytest

from sona_ai.concept_validation import validated_concept


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "brief",
        {},
        {"name": "Ring", "design_brief": "Brief", "materials": "gold", "craft_notes": []},
        {"name": "Ring", "design_brief": "Brief", "materials": [None], "craft_notes": []},
        {"name": " ", "design_brief": "Brief", "materials": [], "craft_notes": []},
    ],
)
def test_malformed_provider_brief_falls_back(value):
    assert validated_concept(value) is None


def test_valid_brief_is_trimmed_and_unknown_provider_fields_are_dropped():
    value = validated_concept(
        {
            "name": " Ring ",
            "design_brief": " Floral ring ",
            "materials": [" Gold "],
            "craft_notes": [" Confirm weight "],
            "internal": "must not be forwarded",
        }
    )
    assert value == {
        "name": "Ring",
        "design_brief": "Floral ring",
        "materials": ["Gold"],
        "craft_notes": ["Confirm weight"],
    }
