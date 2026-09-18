"""Convenience runtime for processing an S3 document from a JSON profile."""

from __future__ import annotations

from pathlib import Path

import boto3

from .config import DocumentProfile, load_profile
from .models import DocumentSource, ExtractionResult
from .pipeline import DocumentPipeline
from .providers import AwsProviderFactory


def validate_runtime_policy(
    profile: DocumentProfile,
    bucket: str,
    key: str,
    region: str | None,
) -> None:
    """Validate profile-scoped source and Region restrictions."""

    if profile.allowed_regions and region not in profile.allowed_regions:
        raise ValueError(
            f"AWS Region {region!r} is not allowed by profile "
            f"{profile.name!r}"
        )
    if (
        profile.allowed_source_buckets
        and bucket not in profile.allowed_source_buckets
    ):
        raise ValueError(f"S3 bucket {bucket!r} is not allowed by the profile")
    if profile.allowed_source_prefixes and not any(
        key.startswith(prefix) for prefix in profile.allowed_source_prefixes
    ):
        raise ValueError(f"S3 key {key!r} is not under an allowed prefix")


def process_s3_document(
    bucket: str,
    key: str,
    profile_path: str | Path,
    *,
    aws_profile: str | None = None,
    region: str | None = None,
    session: boto3.Session | None = None,
    request_id: str | None = None,
) -> ExtractionResult:
    profile = load_profile(profile_path)
    active_session = session or boto3.Session(
        profile_name=aws_profile,
        region_name=region,
    )
    active_region = region or active_session.region_name
    validate_runtime_policy(profile, bucket, key, active_region)
    pipeline = DocumentPipeline(
        profile=profile,
        provider_factory=AwsProviderFactory(active_session, region=region),
    )
    return pipeline.process(
        DocumentSource(bucket=bucket, key=key, request_id=request_id)
    )
