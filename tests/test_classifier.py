from __future__ import annotations

import json

import pytest

from classify_document import _inference_config, _parse_response


def test_classifier_selects_text_after_reasoning_block():
    response = {
        "stopReason": "end_turn",
        "output": {
            "message": {
                "content": [
                    {
                        "reasoningContent": {
                            "reasoningText": {"text": "analysis"}
                        }
                    },
                    {
                        "text": json.dumps(
                            {"doc_type": "invoice", "layout": "standard"}
                        )
                    },
                ]
            }
        },
    }

    assert _parse_response(response)["doc_type"] == "invoice"


def test_classifier_rejects_incomplete_stop_reason():
    with pytest.raises(RuntimeError, match="max_tokens"):
        _parse_response(
            {
                "stopReason": "max_tokens",
                "output": {"message": {"content": [{"text": "{}"}]}},
            }
        )


def test_classifier_inference_settings_are_configurable():
    assert _inference_config(1200, None) == {"maxTokens": 1200}
    assert _inference_config(1600, 0) == {
        "maxTokens": 1600,
        "temperature": 0.0,
    }


@pytest.mark.parametrize(
    ("max_tokens", "temperature"),
    [(0, None), (True, None), (1200, True), (1200, 1.1)],
)
def test_classifier_rejects_invalid_inference_settings(
    max_tokens,
    temperature,
):
    with pytest.raises(ValueError):
        _inference_config(max_tokens, temperature)
