"""Compatibility wrapper for a Bedrock provider configured in an IDP profile."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import boto3

from idp_starter import DocumentSource, load_profile, validate_runtime_policy
from idp_starter.config import ProviderConfig
from idp_starter.providers import AwsProviderFactory

# Aliases are configured through environment variables to avoid stale model IDs.
MODEL_ALIASES = {
    "haiku": "BEDROCK_HAIKU_MODEL_ID",
    "sonnet": "BEDROCK_SONNET_MODEL_ID",
    "opus": "BEDROCK_OPUS_MODEL_ID",
}


def extract_document(
    bucket: str,
    key: str,
    prompt: str | None = None,
    model: str | None = None,
    region: str | None = None,
    profile_path: str | Path | None = None,
    aws_profile: str | None = None,
    include_raw: bool = False,
) -> dict[str, Any]:
    path = profile_path or os.environ.get("IDP_PROFILE")
    if not path:
        raise ValueError("Pass profile_path or set IDP_PROFILE")
    profile = load_profile(path)
    provider_config = next(
        (provider for provider in profile.providers if provider.type == "bedrock"),
        None,
    )
    if provider_config is None:
        raise ValueError("Profile has no Bedrock provider")

    model_id = model
    if model in MODEL_ALIASES:
        variable = MODEL_ALIASES[model]
        model_id = os.environ.get(variable)
        if not model_id:
            raise ValueError(f"Set {variable} to use the {model!r} alias")
    options = dict(provider_config.options)
    if model_id:
        options["model_id"] = model_id
    if prompt:
        options["instruction"] = prompt
    provider_config = ProviderConfig(
        name=provider_config.name,
        type=provider_config.type,
        options=options,
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
        DocumentSource(bucket, key),
        profile,
    ).to_dict(include_raw=include_raw)
