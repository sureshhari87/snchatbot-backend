import pytest

import main


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"status": "incomplete", "output_text": "Unfinished advice"},
        {"status": "failed", "output_text": "Wrong"},
        {"choices": "bad"},
        {"choices": [None]},
        {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]},
        {"choices": [{"message": {"content": ["not text"]}}]},
        {"output_text": "unsafe", "output": [{"content": [{"type": "refusal"}]}]},
    ],
)
def test_incomplete_or_malformed_main_reply_is_rejected(body):
    assert main.extract_llm_generated_text(body) == ""


def test_completed_main_response_keeps_all_text_parts():
    assert (
        main.extract_llm_generated_text(
            {
                "status": "completed",
                "output": [
                    {"type": "reasoning"},
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "Choose your size."},
                            {"type": "output_text", "text": "Confirm final weight."},
                        ],
                    },
                ],
            }
        )
        == "Choose your size. Confirm final weight."
    )


def test_legacy_completed_chat_response_still_supported():
    assert (
        main.extract_llm_generated_text(
            {"choices": [{"finish_reason": "stop", "message": {"content": "Hello"}}]}
        )
        == "Hello"
    )
