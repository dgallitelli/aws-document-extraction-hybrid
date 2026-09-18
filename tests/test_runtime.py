from __future__ import annotations

import json

import pytest

from idp_starter import DocumentProfile
from idp_starter.runtime import process_s3_document, validate_runtime_policy


class FakeSession:
    region_name = "us-east-1"


def test_runtime_rejects_disallowed_bucket_before_aws_clients(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "name": "restricted",
                "version": "1",
                "allowed_regions": ["us-east-1"],
                "allowed_source_buckets": ["allowed-bucket"],
                "allowed_source_prefixes": ["tenant-a/"],
                "fields": {"value": {}},
                "providers": [{"type": "textract"}],
            }
        )
    )

    with pytest.raises(ValueError, match="not allowed"):
        process_s3_document(
            "other-bucket",
            "tenant-a/document.pdf",
            profile_path,
            session=FakeSession(),
        )


def test_runtime_rejects_disallowed_prefix_before_aws_clients(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "name": "restricted",
                "version": "1",
                "allowed_regions": ["us-east-1"],
                "allowed_source_buckets": ["allowed-bucket"],
                "allowed_source_prefixes": ["tenant-a/"],
                "fields": {"value": {}},
                "providers": [{"type": "textract"}],
            }
        )
    )

    with pytest.raises(ValueError, match="allowed prefix"):
        process_s3_document(
            "allowed-bucket",
            "tenant-b/document.pdf",
            profile_path,
            session=FakeSession(),
        )


def test_shared_runtime_policy_rejects_disallowed_region():
    profile = DocumentProfile.from_dict(
        {
            "name": "restricted",
            "version": "1",
            "allowed_regions": ["ap-southeast-1"],
            "fields": {"value": {}},
            "providers": [{"type": "textract"}],
        }
    )

    with pytest.raises(ValueError, match="not allowed"):
        validate_runtime_policy(
            profile,
            "bucket",
            "document.pdf",
            "us-east-1",
        )
