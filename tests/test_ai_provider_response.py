import pytest

from sona_ai.engine import response_json_object


def envelope(text, status="completed"):
    return {
        "status": status,
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
    }


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        envelope('{"reply":"partial"}', "incomplete"),
        envelope("[]"),
        envelope("null"),
        envelope("not json"),
        {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
        {"status": "completed", "output": [None]},
    ],
)
def test_invalid_provider_response_is_not_a_customer_result(data):
    assert response_json_object(data) is None


def test_completed_json_response():
    assert response_json_object(envelope('{"reply":"Hello"}')) == {"reply": "Hello"}
