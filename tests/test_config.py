from __future__ import annotations

import json

import pytest

from idp_starter.config import ConfigError, DocumentProfile, load_profile


def test_load_profile_and_alias_lookup(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "name": "invoice",
                "version": "2",
                "fields": {
                    "invoice_number": {
                        "aliases": ["INVOICE_RECEIPT_ID"],
                        "required": True,
                    }
                },
                "providers": [{"name": "expense", "type": "textract"}],
            }
        )
    )

    profile = load_profile(path)

    assert profile.version == "2"
    assert profile.canonical_name("Invoice Receipt Id") == "invoice_number"


def test_rejects_alias_collision():
    with pytest.raises(ConfigError, match="shared"):
        DocumentProfile.from_dict(
            {
                "name": "bad",
                "version": "1",
                "fields": {
                    "first": {"aliases": ["shared"]},
                    "second": {"aliases": ["shared"]},
                },
                "providers": [{"type": "textract"}],
            }
        )


@pytest.mark.parametrize("threshold", [-0.1, 1.1, "high", True])
def test_rejects_invalid_confidence_threshold(threshold):
    with pytest.raises(ConfigError, match="confidence threshold"):
        DocumentProfile.from_dict(
            {
                "name": "bad",
                "version": "1",
                "fields": {
                    "value": {
                        "confidence_thresholds": {"provider": threshold}
                    }
                },
                "providers": [{"type": "textract"}],
            }
        )


def test_rejects_invalid_pattern():
    with pytest.raises(ConfigError, match="pattern is invalid"):
        DocumentProfile.from_dict(
            {
                "name": "bad",
                "version": "1",
                "fields": {"value": {"pattern": "["}},
                "providers": [{"type": "textract"}],
            }
        )


def test_profile_security_and_deadline_settings():
    profile = DocumentProfile.from_dict(
        {
            "name": "bounded",
            "version": "1",
            "deadline_seconds": 30,
            "allowed_regions": ["us-east-1"],
            "allowed_source_buckets": ["input-bucket"],
            "allowed_source_prefixes": ["tenant-a/"],
            "fields": {"value": {}},
            "providers": [{"type": "textract"}],
        }
    )

    assert profile.deadline_seconds == 30
    assert profile.allowed_regions == ("us-east-1",)
    assert profile.allowed_source_buckets == ("input-bucket",)
    assert profile.allowed_source_prefixes == ("tenant-a/",)


def test_rejects_boolean_deadline():
    with pytest.raises(ConfigError, match="positive number"):
        DocumentProfile.from_dict(
            {
                "name": "bad-deadline",
                "version": "1",
                "deadline_seconds": True,
                "fields": {"value": {}},
                "providers": [{"type": "textract"}],
            }
        )


def test_requires_explicit_version():
    with pytest.raises(ConfigError, match="explicit version"):
        DocumentProfile.from_dict(
            {
                "name": "unversioned",
                "fields": {"value": {}},
                "providers": [{"type": "textract"}],
            }
        )


def test_rejects_string_boolean():
    with pytest.raises(ConfigError, match="JSON boolean"):
        DocumentProfile.from_dict(
            {
                "name": "bad-boolean",
                "version": "1",
                "fallback_on_low_confidence": "false",
                "fields": {"value": {}},
                "providers": [{"type": "textract"}],
            }
        )


def test_rejects_threshold_for_unknown_provider():
    with pytest.raises(ConfigError, match="unknown confidence providers"):
        DocumentProfile.from_dict(
            {
                "name": "bad-provider-reference",
                "version": "1",
                "fields": {
                    "value": {
                        "confidence_thresholds": {"misspelled": 0.8}
                    }
                },
                "providers": [{"name": "actual", "type": "textract"}],
            }
        )


def test_rejects_invalid_textract_query_alias():
    with pytest.raises(ConfigError, match="alias"):
        DocumentProfile.from_dict(
            {
                "name": "invalid-query-alias",
                "version": "1",
                "fields": {"field🙂": {}},
                "providers": [
                    {
                        "name": "queries",
                        "type": "textract",
                        "mode": "queries",
                    }
                ],
            }
        )


def test_rejects_overlong_textract_query():
    with pytest.raises(ConfigError, match="query"):
        DocumentProfile.from_dict(
            {
                "name": "invalid-query",
                "version": "1",
                "fields": {"value": {"query": "x" * 201}},
                "providers": [
                    {
                        "name": "queries",
                        "type": "textract",
                        "mode": "queries",
                    }
                ],
            }
        )
