from __future__ import annotations

import json
import io

import pytest

from idp_starter import DocumentProfile, DocumentSource
from idp_starter.providers.bda import BdaProvider, _confidence


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, 0.0),
        (0.5, 0.5),
        (1, 1.0),
        (1.1, None),
        (50, None),
        (True, None),
    ],
)
def test_bda_confidence_uses_documented_unit_interval(raw, expected):
    assert _confidence(raw) == expected


class SyncClient:
    def __init__(self, response):
        self.response = response
        self.request = None

    def invoke_data_automation(self, **kwargs):
        self.request = kwargs
        return self.response


def test_sync_bda_request_and_normalization(profile):
    client = SyncClient(
        {
            "outputSegments": [
                {
                    "customOutputStatus": "MATCH",
                    "customOutput": json.dumps(
                        {
                            "document_id": {
                                "inference_result": "DOC-4",
                                "confidence": 0.91,
                                "visualGrounding": {"page": 1},
                            },
                            "total_amount": {
                                "value": "45.00",
                                "confidenceScore": 0.87,
                            },
                        }
                    ),
                    "standardOutput": json.dumps(
                        {"representation": "Document DOC-4"}
                    ),
                }
            ]
        }
    )
    provider = BdaProvider(
        "bda-primary",
        client=client,
        s3_client=None,
        options={
            "mode": "sync",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
        },
    )

    result = provider.extract(DocumentSource("bucket", "document.png"), profile)

    assert client.request["inputConfiguration"]["s3Uri"] == "s3://bucket/document.png"
    assert result.fields["document_id"].value == "DOC-4"
    assert result.fields["document_id"].confidence == 0.91
    assert result.fields["document_id"].provenance["visual_grounding"] == {
        "page": 1
    }
    assert result.raw_text == "Document DOC-4"
    assert result.metadata["segment_statuses"] == [
        {"customOutputStatus": "MATCH"}
    ]


def test_empty_sync_output_warns(profile):
    provider = BdaProvider(
        "bda-primary",
        client=SyncClient({"outputSegments": []}),
        s3_client=None,
        options={
            "mode": "sync",
            "data_automation_profile_arn": "profile-arn",
            "blueprints": [{"blueprintArn": "blueprint-arn"}],
        },
    )

    result = provider.extract(DocumentSource("bucket", "document.png"), profile)

    assert result.fields == {}
    assert result.warnings == ["No configured fields found in BDA output"]


def test_standard_output_does_not_create_field_candidates(profile):
    provider = BdaProvider(
        "bda-primary",
        client=SyncClient(
            {
                "outputSegments": [
                    {
                        "standardOutput": json.dumps(
                            {
                                "document_id": "metadata-not-a-field",
                                "representation": "raw text",
                            }
                        )
                    }
                ]
            }
        ),
        s3_client=None,
        options={
            "mode": "sync",
            "data_automation_profile_arn": "profile-arn",
            "blueprints": [{"blueprintArn": "blueprint-arn"}],
        },
    )

    result = provider.extract(DocumentSource("bucket", "document.png"), profile)

    assert result.fields == {}
    assert result.raw_text == "raw text"


def test_explainability_metadata_does_not_create_field_candidates():
    profile = DocumentProfile.from_dict(
        {
            "name": "metadata-collision",
            "version": "1",
            "fields": {"confidence": {}},
            "providers": [{"name": "bda", "type": "bda"}],
        }
    )
    provider = BdaProvider(
        "bda",
        client=None,
        s3_client=None,
        options={
            "mode": "sync",
            "data_automation_profile_arn": "profile-arn",
            "blueprints": [{"blueprintArn": "blueprint-arn"}],
        },
    )

    result = provider.normalize_payload(
        {
            "inference_result": {"document_id": "DOC-1"},
            "explainability_info": [
                {"document_id": {"confidence": 0.99}}
            ],
        },
        profile,
    )

    assert result.fields == {}


def test_camel_case_explainability_is_joined(profile):
    provider = BdaProvider(
        "bda-primary",
        client=None,
        s3_client=None,
        options={
            "mode": "sync",
            "data_automation_profile_arn": "profile-arn",
            "blueprints": [{"blueprintArn": "blueprint-arn"}],
        },
    )

    result = provider.normalize_payload(
        {
            "inferenceResult": {"document_id": "DOC-CAMEL"},
            "explainabilityInfo": [
                {
                    "document_id": {
                        "confidence": 0.93,
                        "geometry": [{"page": 2}],
                    }
                }
            ],
        },
        profile,
    )

    field = result.fields["document_id"]
    assert field.confidence == 0.93
    assert field.provenance["path"] == "inferenceResult.document_id"
    assert field.provenance["visual_grounding"] == [{"page": 2}]


def test_explainability_keeps_best_confidence(profile):
    provider = BdaProvider(
        "bda-primary",
        client=None,
        s3_client=None,
        options={
            "mode": "sync",
            "data_automation_profile_arn": "profile-arn",
            "blueprints": [{"blueprintArn": "blueprint-arn"}],
        },
    )

    result = provider.normalize_payload(
        {
            "inference_result": {"document_id": "DOC-BEST"},
            "explainability_info": [
                {
                    "document_id": {
                        "confidence": 0.92,
                        "geometry": [{"page": 1}],
                    }
                },
                {
                    "document_id": {
                        "confidence": 0.10,
                        "geometry": [{"page": 2}],
                    }
                },
            ],
        },
        profile,
    )

    field = result.fields["document_id"]
    assert field.confidence == 0.92
    assert field.provenance["visual_grounding"] == [{"page": 1}]


def test_scoreless_failed_explainability_requires_review(profile):
    provider = BdaProvider(
        "bda-primary",
        client=None,
        s3_client=None,
        options={
            "mode": "sync",
            "data_automation_profile_arn": "profile-arn",
            "blueprints": [{"blueprintArn": "blueprint-arn"}],
        },
    )

    result = provider.normalize_payload(
        {
            "inference_result": {"document_id": "DOC-FAILED"},
            "explainability_info": [
                {"document_id": {"success": False}}
            ],
        },
        profile,
    )

    assert result.fields["document_id"].provenance["requires_review"] is True


def test_conflicting_bda_alias_values_are_review_signaled(profile):
    provider = BdaProvider(
        "bda-primary",
        client=None,
        s3_client=None,
        options={
            "mode": "sync",
            "data_automation_profile_arn": "profile-arn",
            "blueprints": [{"blueprintArn": "blueprint-arn"}],
        },
    )

    result = provider.normalize_payload(
        {
            "inference_result": {
                "document_id": "DOC-A",
                "DOCUMENT_NUMBER": "DOC-B",
            },
            "explainability_info": [
                {
                    "document_id": {"confidence": 0.92},
                    "DOCUMENT_NUMBER": {"confidence": 0.80},
                }
            ],
        },
        profile,
    )

    field = result.fields["document_id"]
    assert field.value == "DOC-A"
    assert field.provenance["requires_review"] is True
    assert field.provenance["conflicting_value"] == "DOC-B"
    assert result.warnings == [
        "Conflicting BDA values for field document_id"
    ]


class AsyncClient:
    def __init__(self):
        self.requests = []

    def invoke_data_automation_async(self, **kwargs):
        self.requests.append(kwargs)
        return {"invocationArn": "invocation-arn"}

    def get_data_automation_status(self, **kwargs):
        return {
            "status": "Success",
            "outputConfiguration": {
                "s3Uri": self.requests[-1]["outputConfiguration"]["s3Uri"]
            },
            "jobDurationInSeconds": 1,
        }


class ManifestAsyncClient(AsyncClient):
    def get_data_automation_status(self, **kwargs):
        requested_uri = self.requests[-1]["outputConfiguration"]["s3Uri"]
        return {
            "status": "Success",
            "outputConfiguration": {
                "s3Uri": f"{requested_uri}invocation/job_metadata.json"
            },
            "jobDurationInSeconds": 1,
        }


class OnePagePaginator:
    def paginate(self, **kwargs):
        return [{"Contents": [{"Key": "prefix/result.json"}]}]


class AsyncS3:
    def get_bucket_versioning(self, **kwargs):
        return {}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return OnePagePaginator()

    def get_object(self, **kwargs):
        return {
            "Body": io.BytesIO(
                json.dumps(
                    {
                        "inference_result": {
                            "document_id": "DOC-9",
                            "total_amount": "9.00",
                        },
                        "explainability_info": [
                            {
                                "document_id": {
                                    "success": True,
                                    "confidence": 0.92,
                                    "geometry": [{"page": 1}],
                                },
                                "total_amount": {
                                    "success": True,
                                    "confidence": 0.88,
                                },
                            }
                        ],
                        "matched_blueprint": {
                            "name": "generic-document",
                            "confidence": 0.9,
                        },
                    }
                ).encode("utf-8")
            )
        }


class ManifestS3:
    def __init__(self):
        self.requested_keys = []

    def get_object(self, **kwargs):
        key = kwargs["Key"]
        self.requested_keys.append(key)
        if key.endswith("job_metadata.json"):
            invocation_prefix = key.removesuffix("job_metadata.json")
            payload = {
                "output_metadata": [
                    {
                        "segment_metadata": [
                            {
                                "custom_output_path": (
                                    f"s3://output/{invocation_prefix}0/"
                                    "custom_output/0/result.json"
                                ),
                                "standard_output_path": (
                                    f"s3://output/{invocation_prefix}0/"
                                    "standard_output/0/result.json"
                                ),
                            }
                        ]
                    }
                ]
            }
        elif "/custom_output/" in key:
            payload = {
                "inference_result": {
                    "document_id": "DOC-MANIFEST",
                    "total_amount": "17.00",
                }
            }
        else:
            payload = {"representation": "manifest standard output"}
        return {"Body": io.BytesIO(json.dumps(payload).encode("utf-8"))}

    def get_bucket_versioning(self, **kwargs):
        return {}


def test_async_bda_follows_job_metadata_manifest(profile):
    client = ManifestAsyncClient()
    provider = BdaProvider(
        "bda-primary",
        client=client,
        s3_client=ManifestS3(),
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base/token",
        },
    )

    result = provider.extract(
        DocumentSource("bucket", "document.pdf", request_id="manifest"),
        profile,
    )

    assert result.fields["document_id"].value == "DOC-MANIFEST"
    assert result.raw_text == "manifest standard output"
    assert result.metadata["payload_count"] == 1
    assert result.metadata["text_payload_count"] == 1


def test_async_bda_rejects_manifest_reference_outside_job_prefix(profile):
    class EscapingManifestS3(ManifestS3):
        def get_object(self, **kwargs):
            if kwargs["Key"].endswith("job_metadata.json"):
                payload = {
                    "output_metadata": [
                        {
                            "segment_metadata": [
                                {
                                    "custom_output_path": (
                                        "s3://output/another-job/result.json"
                                    )
                                }
                            ]
                        }
                    ]
                }
                return {
                    "Body": io.BytesIO(
                        json.dumps(payload).encode("utf-8")
                    )
                }
            return super().get_object(**kwargs)

    provider = BdaProvider(
        "bda-primary",
        client=ManifestAsyncClient(),
        s3_client=EscapingManifestS3(),
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base/token",
        },
    )

    with pytest.raises(RuntimeError, match="manifest referenced output outside"):
        provider.extract(
            DocumentSource("bucket", "document.pdf", request_id="escape"),
            profile,
        )


def test_async_bda_uses_stable_hashed_client_token(profile):
    client = AsyncClient()
    provider = BdaProvider(
        "bda-primary",
        client=client,
        s3_client=AsyncS3(),
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base",
        },
    )
    source = DocumentSource("bucket", "document.pdf", request_id="request-1")

    first = provider.extract(source, profile)
    second = provider.extract(source, profile)

    assert first.fields["document_id"].value == "DOC-9"
    assert first.fields["document_id"].confidence == 0.92
    assert first.fields["document_id"].provenance["visual_grounding"] == [
        {"page": 1}
    ]
    assert first.metadata["matched_blueprints"] == [
        {"name": "generic-document", "confidence": 0.9}
    ]
    assert client.requests[0]["clientToken"] == client.requests[1]["clientToken"]
    assert len(client.requests[0]["clientToken"]) == 64
    assert client.requests[0]["outputConfiguration"] == client.requests[1][
        "outputConfiguration"
    ]


def test_sync_bda_rejects_non_image_input(profile):
    provider = BdaProvider(
        "bda-primary",
        client=SyncClient({"outputSegments": []}),
        s3_client=None,
        options={
            "mode": "sync",
            "data_automation_profile_arn": "profile-arn",
            "blueprints": [{"blueprintArn": "blueprint-arn"}],
        },
    )

    with pytest.raises(ValueError, match="image inputs only"):
        provider.extract(DocumentSource("bucket", "document.pdf"), profile)


def test_async_standard_output_cannot_create_fields(profile):
    class StandardOutputS3(AsyncS3):
        def get_object(self, **kwargs):
            return {
                "Body": io.BytesIO(
                    json.dumps(
                        {
                            "document_id": "metadata-not-a-field",
                            "documentText": "raw document text",
                        }
                    ).encode("utf-8")
                )
            }

    provider = BdaProvider(
        "bda-primary",
        client=AsyncClient(),
        s3_client=StandardOutputS3(),
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base",
        },
    )

    result = provider.extract(
        DocumentSource("bucket", "document.pdf", request_id="request-2"),
        profile,
    )

    assert result.fields == {}
    assert result.raw_text == "raw document text"


def test_cleanup_error_is_not_reported_as_success(profile):
    class CleanupS3(AsyncS3):
        def delete_objects(self, **kwargs):
            return {
                "Errors": [
                    {
                        "Key": "prefix/result.json",
                        "Code": "AccessDenied",
                    }
                ]
            }

    provider = BdaProvider(
        "bda-primary",
        client=AsyncClient(),
        s3_client=CleanupS3(),
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base",
            "cleanup_output": True,
        },
    )

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        provider.extract(
            DocumentSource("bucket", "document.pdf", request_id="request-3"),
            profile,
        )


def test_cleanup_uses_requested_job_prefix_not_manifest_key(profile):
    class CleanupManifestS3(ManifestS3):
        def __init__(self):
            super().__init__()
            self.list_prefixes = []
            self.deleted_keys = []

        def get_paginator(self, name):
            assert name == "list_objects_v2"
            owner = self

            class Paginator:
                def paginate(self, **kwargs):
                    owner.list_prefixes.append(kwargs["Prefix"])
                    return [
                        {
                            "Contents": [
                                {
                                    "Key": (
                                        f"{kwargs['Prefix']}"
                                        "invocation/job_metadata.json"
                                    )
                                },
                                {
                                    "Key": (
                                        f"{kwargs['Prefix']}invocation/0/"
                                        "custom_output/0/result.json"
                                    )
                                },
                            ]
                        }
                    ]

            return Paginator()

        def delete_objects(self, **kwargs):
            self.deleted_keys.extend(
                item["Key"] for item in kwargs["Delete"]["Objects"]
            )
            return {}

    client = ManifestAsyncClient()
    s3 = CleanupManifestS3()
    provider = BdaProvider(
        "bda-primary",
        client=client,
        s3_client=s3,
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base/token",
            "cleanup_output": True,
        },
    )

    result = provider.extract(
        DocumentSource("bucket", "document.pdf", request_id="cleanup-manifest"),
        profile,
    )

    requested_prefix = client.requests[0]["outputConfiguration"]["s3Uri"]
    assert s3.list_prefixes == [requested_prefix.removeprefix("s3://output/")]
    assert len(s3.deleted_keys) == 2
    assert result.metadata["output_cleaned_up"] is True


def test_cleanup_removes_versions_and_delete_markers(profile):
    class VersionPaginator:
        def paginate(self, **kwargs):
            return [
                {
                    "Versions": [
                        {"Key": "prefix/result.json", "VersionId": "v2"},
                        {"Key": "prefix/result.json", "VersionId": "v1"},
                    ],
                    "DeleteMarkers": [
                        {"Key": "prefix/result.json", "VersionId": "d1"}
                    ],
                }
            ]

    class VersionedS3(AsyncS3):
        def __init__(self):
            self.deleted = []

        def get_bucket_versioning(self, **kwargs):
            return {"Status": "Enabled"}

        def get_paginator(self, name):
            assert name == "list_object_versions"
            return VersionPaginator()

        def delete_objects(self, **kwargs):
            self.deleted.extend(kwargs["Delete"]["Objects"])
            return {}

    s3 = VersionedS3()
    provider = BdaProvider(
        "bda-primary",
        client=AsyncClient(),
        s3_client=s3,
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base",
        },
    )

    provider._delete_prefix("s3://output/prefix/")

    assert s3.deleted == [
        {"Key": "prefix/result.json", "VersionId": "v2"},
        {"Key": "prefix/result.json", "VersionId": "v1"},
        {"Key": "prefix/result.json", "VersionId": "d1"},
    ]


def test_cleanup_runs_after_bda_service_error(profile):
    class ErrorClient(AsyncClient):
        def get_data_automation_status(self, **kwargs):
            return {"status": "ServiceError"}

    class CleanupS3(AsyncS3):
        def __init__(self):
            self.list_prefixes = []

        def get_paginator(self, name):
            assert name == "list_objects_v2"
            owner = self

            class Paginator:
                def paginate(self, **kwargs):
                    owner.list_prefixes.append(kwargs["Prefix"])
                    return [{"Contents": []}]

            return Paginator()

    client = ErrorClient()
    s3 = CleanupS3()
    provider = BdaProvider(
        "bda-primary",
        client=client,
        s3_client=s3,
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base",
            "cleanup_output": True,
        },
    )

    with pytest.raises(RuntimeError, match="ServiceError"):
        provider.extract(
            DocumentSource("bucket", "document.pdf", request_id="error-cleanup"),
            profile,
        )

    requested_uri = client.requests[0]["outputConfiguration"]["s3Uri"]
    assert s3.list_prefixes == [requested_uri.removeprefix("s3://output/")]


def test_cleanup_runs_after_bda_timeout(profile):
    class PendingClient(AsyncClient):
        def get_data_automation_status(self, **kwargs):
            return {"status": "InProgress"}

    class CleanupS3(AsyncS3):
        def __init__(self):
            self.list_prefixes = []

        def get_paginator(self, name):
            assert name == "list_objects_v2"
            owner = self

            class Paginator:
                def paginate(self, **kwargs):
                    owner.list_prefixes.append(kwargs["Prefix"])
                    return [{"Contents": []}]

            return Paginator()

    client = PendingClient()
    s3 = CleanupS3()
    provider = BdaProvider(
        "bda-primary",
        client=client,
        s3_client=s3,
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base",
            "cleanup_output": True,
            "timeout_seconds": 0,
        },
    )

    with pytest.raises(TimeoutError, match="exceeded"):
        provider.extract(
            DocumentSource(
                "bucket",
                "document.pdf",
                request_id="timeout-cleanup",
            ),
            profile,
        )

    requested_uri = client.requests[0]["outputConfiguration"]["s3Uri"]
    assert s3.list_prefixes == [requested_uri.removeprefix("s3://output/")]


def test_cleanup_refuses_output_outside_requested_prefix(profile):
    class MismatchedOutputClient(AsyncClient):
        def get_data_automation_status(self, **kwargs):
            return {
                "status": "Success",
                "outputConfiguration": {"s3Uri": "s3://output/other-tenant/"},
            }

    class RecordingS3(AsyncS3):
        def __init__(self):
            self.delete_calls = []
            self.list_prefixes = []

        def get_paginator(self, name):
            assert name == "list_objects_v2"
            owner = self

            class Paginator:
                def paginate(self, **kwargs):
                    owner.list_prefixes.append(kwargs["Prefix"])
                    return [{"Contents": []}]

            return Paginator()

        def delete_objects(self, **kwargs):
            self.delete_calls.append(kwargs)
            return {}

    s3 = RecordingS3()
    provider = BdaProvider(
        "bda-primary",
        client=MismatchedOutputClient(),
        s3_client=s3,
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base",
            "cleanup_output": True,
        },
    )

    with pytest.raises(RuntimeError, match="outside the requested"):
        provider.extract(
            DocumentSource("bucket", "document.pdf", request_id="request-4"),
            profile,
        )

    assert s3.delete_calls == []
    requested_uri = provider.client.requests[0]["outputConfiguration"]["s3Uri"]
    assert s3.list_prefixes == [
        requested_uri.removeprefix("s3://output/")
    ]


def test_bda_service_error_includes_available_detail(profile):
    class ErrorClient(AsyncClient):
        def get_data_automation_status(self, **kwargs):
            return {
                "status": "ServiceError",
                "errorType": "InternalFailure",
                "errorMessage": "temporary failure",
            }

    provider = BdaProvider(
        "bda-primary",
        client=ErrorClient(),
        s3_client=AsyncS3(),
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base",
        },
    )

    with pytest.raises(RuntimeError, match="InternalFailure: temporary failure"):
        provider.extract(
            DocumentSource("bucket", "document.pdf", request_id="request-5"),
            profile,
        )


def test_delete_prefix_refuses_empty_prefix(profile):
    provider = BdaProvider(
        "bda-primary",
        client=AsyncClient(),
        s3_client=AsyncS3(),
        options={
            "mode": "async",
            "data_automation_profile_arn": "profile-arn",
            "data_automation_project_arn": "project-arn",
            "output_s3_uri": "s3://output/base",
        },
    )

    with pytest.raises(ValueError, match="empty"):
        provider._delete_prefix("s3://output/")
