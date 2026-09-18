from __future__ import annotations

import json

import pytest

import extract_claude
import process_document


class DummyResult:
    def to_dict(self, *, include_raw=False):
        result = {"status": "accepted"}
        if include_raw:
            result["raw_text"] = {"provider": "full document"}
        return result


def test_process_document_prefers_explicit_profile(monkeypatch):
    captured = {}

    def fake_process(**kwargs):
        captured.update(kwargs)
        return DummyResult()

    monkeypatch.setenv("IDP_PROFILE", "environment.json")
    monkeypatch.setattr(process_document, "process_s3_document", fake_process)

    result = process_document.process_document(
        "bucket",
        "key.pdf",
        profile_path="explicit.json",
        region="us-east-1",
    )

    assert result == {"status": "accepted"}
    assert captured["profile_path"] == "explicit.json"


def test_process_document_raw_artifacts_are_opt_in(monkeypatch):
    monkeypatch.setattr(
        process_document,
        "process_s3_document",
        lambda **kwargs: DummyResult(),
    )

    default_result = process_document.process_document(
        "bucket",
        "key.pdf",
        profile_path="profile.json",
    )
    raw_result = process_document.process_document(
        "bucket",
        "key.pdf",
        profile_path="profile.json",
        include_raw=True,
    )

    assert "raw_text" not in default_result
    assert raw_result["raw_text"]["provider"] == "full document"


def test_known_model_alias_requires_its_environment_variable(
    tmp_path,
    monkeypatch,
):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "name": "document",
                "version": "1",
                "fields": {"value": {}},
                "providers": [
                    {
                        "name": "bedrock",
                        "type": "bedrock",
                        "model_id": "profile-model",
                    }
                ],
            }
        )
    )
    monkeypatch.delenv("BEDROCK_HAIKU_MODEL_ID", raising=False)

    with pytest.raises(ValueError, match="BEDROCK_HAIKU_MODEL_ID"):
        extract_claude.extract_document(
            "bucket",
            "document.pdf",
            model="haiku",
            profile_path=profile_path,
        )


def test_public_textract_normalizer_omits_raw_by_default(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "name": "invoice",
                "version": "1",
                "fields": {
                    "document_id": {
                        "aliases": ["DOCUMENT_NUMBER"],
                    }
                },
                "providers": [
                    {
                        "name": "expense",
                        "type": "textract",
                        "mode": "expense",
                    }
                ],
            }
        )
    )
    response = {
        "ExpenseDocuments": [
            {
                "SummaryFields": [
                    {
                        "Type": {"Text": "DOCUMENT_NUMBER"},
                        "ValueDetection": {"Text": "INV-1"},
                    }
                ],
                "Blocks": [{"BlockType": "LINE", "Text": "full document"}],
            }
        ]
    }

    default_result = process_document.normalize_textract(
        response,
        profile_path=str(profile_path),
    )
    raw_result = process_document.normalize_textract(
        response,
        profile_path=str(profile_path),
        include_raw=True,
    )

    assert "raw_text" not in default_result
    assert raw_result["raw_text"] == "full document"


def test_public_bda_normalizer_omits_raw_by_default(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "name": "document",
                "version": "1",
                "fields": {"document_id": {}},
                "providers": [
                    {
                        "name": "bda",
                        "type": "bda",
                        "mode": "sync",
                    }
                ],
            }
        )
    )
    response = {
        "inference_result": {"document_id": "DOC-1"},
        "rawText": "full document",
    }

    default_result = process_document.normalize_bda(
        response,
        profile_path=str(profile_path),
    )
    raw_result = process_document.normalize_bda(
        response,
        profile_path=str(profile_path),
        include_raw=True,
    )

    assert "raw_text" not in default_result
    assert raw_result["raw_text"] == "full document"
