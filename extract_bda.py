"""Compatibility wrapper for a BDA provider configured in an IDP profile."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import boto3

from idp_starter import DocumentSource, load_profile, validate_runtime_policy
from idp_starter.config import ProviderConfig
from idp_starter.providers import AwsProviderFactory


def extract_with_bda(
    bucket: str,
    key: str,
    region: str | None = None,
    timeout: int = 300,
    profile_path: str | Path | None = None,
    aws_profile: str | None = None,
    request_id: str | None = None,
    include_raw: bool = False,
) -> dict[str, Any]:
    path = profile_path or os.environ.get("IDP_PROFILE")
    if not path:
        raise ValueError("Pass profile_path or set IDP_PROFILE")
    profile = load_profile(path)
    provider_config = next(
        (provider for provider in profile.providers if provider.type == "bda"),
        None,
    )
    if provider_config is None:
        raise ValueError("Profile has no BDA provider")
    provider_config = ProviderConfig(
        name=provider_config.name,
        type=provider_config.type,
        options={**provider_config.options, "timeout_seconds": timeout},
    )
    session = boto3.Session(profile_name=aws_profile, region_name=region)
    validate_runtime_policy(
        profile,
        bucket,
        key,
        region or session.region_name,
    )
    provider = AwsProviderFactory(session, region=region)(provider_config)
    return provider.extract(
        DocumentSource(bucket, key, request_id=request_id),
        profile,
    ).to_dict(
        include_raw=include_raw
    )
