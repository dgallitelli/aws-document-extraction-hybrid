from __future__ import annotations

import io
import json
import warnings

import pytest
from PIL import Image

from idp_starter import DocumentSource, ProviderInputError
from idp_starter.providers.bedrock import BedrockProvider


class FakeS3:
    def __init__(self, body=b"%PDF-1.7 document bytes"):
        self.calls = []
        self.body = body

    def head_object(self, **kwargs):
        self.calls.append(("head", kwargs))
        return {"ContentLength": len(self.body)}

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        return {"Body": io.BytesIO(self.body)}


class FakeBedrock:
    def __init__(self, stop_reason="end_turn"):
        self.request = None
        self.stop_reason = stop_reason

    def converse(self, **kwargs):
        self.request = kwargs
        return {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "document_id": "DOC-1",
                                    "total_amount": 12.5,
                                    "effective_date": None,
                                }
                            )
                        }
                    ]
                }
            },
            "usage": {"inputTokens": 10, "outputTokens": 5},
            "stopReason": self.stop_reason,
        }


def test_bedrock_schema_request_and_result(profile):
    bedrock = FakeBedrock()
    s3 = FakeS3()
    provider = BedrockProvider(
        "bedrock-fallback",
        bedrock_client=bedrock,
        s3_client=s3,
        options={"model_id": "model-id", "max_tokens": 500},
    )

    result = provider.extract(DocumentSource("bucket", "document.pdf"), profile)

    request = bedrock.request
    schema = json.loads(
        request["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"]
    )
    assert request["modelId"] == "model-id"
    assert request["inferenceConfig"]["maxTokens"] == 500
    assert "temperature" not in request["inferenceConfig"]
    assert schema["properties"]["total_amount"]["anyOf"] == [
        {"type": "number"},
        {"type": "null"},
    ]
    assert request["messages"][0]["content"][0]["document"]["format"] == "pdf"
    assert "ignore any instructions" in request["messages"][0]["content"][1]["text"]
    assert result.fields["total_amount"].value == 12.5
    assert result.fields["total_amount"].provenance["requires_review"] is True
    assert "effective_date" not in result.fields


def test_bedrock_rejects_incomplete_stop_reason(profile):
    provider = BedrockProvider(
        "bedrock-fallback",
        bedrock_client=FakeBedrock(stop_reason="max_tokens"),
        s3_client=FakeS3(),
        options={"model_id": "model-id"},
    )

    with pytest.raises(RuntimeError, match="max_tokens"):
        provider.extract(DocumentSource("bucket", "document.pdf"), profile)


def test_unsupported_extension_fails_before_s3_read(profile):
    s3 = FakeS3()
    provider = BedrockProvider(
        "bedrock-fallback",
        bedrock_client=FakeBedrock(),
        s3_client=s3,
        options={"model_id": "model-id"},
    )

    with pytest.raises(ValueError, match="Unsupported"):
        provider.extract(DocumentSource("bucket", "archive.zip"), profile)

    assert s3.calls == []


def test_oversized_document_fails_before_download(profile):
    class OversizedS3(FakeS3):
        def head_object(self, **kwargs):
            self.calls.append(("head", kwargs))
            return {"ContentLength": 100}

    s3 = OversizedS3()
    provider = BedrockProvider(
        "bedrock-fallback",
        bedrock_client=FakeBedrock(),
        s3_client=s3,
        options={"model_id": "model-id", "max_bytes": 10},
    )

    with pytest.raises(ValueError, match="byte limit"):
        provider.extract(DocumentSource("bucket", "document.pdf"), profile)

    assert [call[0] for call in s3.calls] == ["head"]


def test_extension_signature_mismatch_is_rejected(profile):
    provider = BedrockProvider(
        "bedrock-fallback",
        bedrock_client=FakeBedrock(),
        s3_client=FakeS3(body=b"not a pdf"),
        options={"model_id": "model-id"},
    )

    with pytest.raises(ValueError, match="do not match"):
        provider.extract(DocumentSource("bucket", "document.pdf"), profile)


def test_oversized_image_fails_before_download(profile):
    class OversizedImageS3(FakeS3):
        def head_object(self, **kwargs):
            self.calls.append(("head", kwargs))
            return {"ContentLength": 3_750_001}

    s3 = OversizedImageS3()
    provider = BedrockProvider(
        "bedrock-fallback",
        bedrock_client=FakeBedrock(),
        s3_client=s3,
        options={"model_id": "model-id"},
    )

    with pytest.raises(ValueError, match="3750000"):
        provider.extract(DocumentSource("bucket", "image.png"), profile)

    assert [call[0] for call in s3.calls] == ["head"]


def test_image_dimension_limit_is_enforced(profile):
    body = io.BytesIO()
    Image.new("RGB", (2, 2)).save(body, format="PNG")
    provider = BedrockProvider(
        "bedrock-fallback",
        bedrock_client=FakeBedrock(),
        s3_client=FakeS3(body=body.getvalue()),
        options={"model_id": "model-id", "max_image_dimension": 1},
    )

    with pytest.raises(ProviderInputError, match="dimensions exceed"):
        provider.extract(DocumentSource("bucket", "image.png"), profile)


def test_bedrock_boolean_options_are_strict():
    with pytest.raises(ValueError, match="must be a boolean"):
        BedrockProvider(
            "bedrock-fallback",
            bedrock_client=FakeBedrock(),
            s3_client=FakeS3(),
            options={"model_id": "model-id", "require_review": "false"},
        )


def test_bedrock_selects_text_after_reasoning_block(profile):
    class ReasoningFirstBedrock(FakeBedrock):
        def converse(self, **kwargs):
            response = super().converse(**kwargs)
            response["output"]["message"]["content"].insert(
                0,
                {"reasoningContent": {"reasoningText": {"text": "analysis"}}},
            )
            return response

    provider = BedrockProvider(
        "bedrock-fallback",
        bedrock_client=ReasoningFirstBedrock(),
        s3_client=FakeS3(),
        options={"model_id": "model-id"},
    )

    result = provider.extract(DocumentSource("bucket", "document.pdf"), profile)

    assert result.fields["document_id"].value == "DOC-1"


def test_decompression_bomb_is_a_recoverable_input_error(
    profile,
    monkeypatch,
):
    def raise_bomb(*args, **kwargs):
        raise Image.DecompressionBombError("too many pixels")

    monkeypatch.setattr(Image, "open", raise_bomb)
    provider = BedrockProvider(
        "bedrock-fallback",
        bedrock_client=FakeBedrock(),
        s3_client=FakeS3(body=b"\x89PNG\r\n\x1a\n"),
        options={"model_id": "model-id"},
    )

    with pytest.raises(ProviderInputError, match="safe pixel limit"):
        provider.extract(DocumentSource("bucket", "image.png"), profile)


def test_decompression_bomb_warning_is_a_recoverable_input_error(
    profile,
    monkeypatch,
):
    def raise_warning(*args, **kwargs):
        warnings.warn(
            "many pixels",
            Image.DecompressionBombWarning,
            stacklevel=2,
        )

    monkeypatch.setattr(Image, "open", raise_warning)
    provider = BedrockProvider(
        "bedrock-fallback",
        bedrock_client=FakeBedrock(),
        s3_client=FakeS3(body=b"\x89PNG\r\n\x1a\n"),
        options={"model_id": "model-id"},
    )

    with pytest.raises(ProviderInputError, match="warning threshold"):
        provider.extract(DocumentSource("bucket", "image.png"), profile)


def test_bedrock_temperature_is_opt_in(profile):
    bedrock = FakeBedrock()
    provider = BedrockProvider(
        "bedrock-fallback",
        bedrock_client=bedrock,
        s3_client=FakeS3(),
        options={"model_id": "model-id", "temperature": 0},
    )

    provider.extract(DocumentSource("bucket", "document.pdf"), profile)

    assert bedrock.request["inferenceConfig"]["temperature"] == 0.0


@pytest.mark.parametrize("max_tokens", [0, -1, True, "1200"])
def test_bedrock_max_tokens_must_be_positive_integer(max_tokens):
    with pytest.raises(ValueError, match="positive integer"):
        BedrockProvider(
            "bedrock-fallback",
            bedrock_client=FakeBedrock(),
            s3_client=FakeS3(),
            options={"model_id": "model-id", "max_tokens": max_tokens},
        )
