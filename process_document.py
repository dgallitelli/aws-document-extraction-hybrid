"""Compatibility entry point for the profile-driven IDP pipeline."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from idp_starter import load_profile, process_s3_document
from idp_starter.providers.bda import BdaProvider
from idp_starter.providers.textract import TextractProvider

# Retained for callers that imported the original constant. New routing belongs
# in versioned profiles.
TEXTRACT_ROUTES = {
    "invoice": "expense",
    "receipt": "expense",
    "drivers_license": "id",
    "passport": "id",
    "w2": "queries",
    "tax_form": "queries",
}


def _profile_path(value: str | None) -> str:
    path = value or os.environ.get("IDP_PROFILE")
    if not path:
        raise ValueError("Pass profile_path or set IDP_PROFILE")
    return path


def process_document(
    bucket: str,
    key: str,
    region: str | None = None,
    profile_path: str | None = None,
    aws_profile: str | None = None,
    request_id: str | None = None,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Process one S3 document and return the canonical result envelope."""

    return process_s3_document(
        bucket=bucket,
        key=key,
        profile_path=_profile_path(profile_path),
        aws_profile=aws_profile,
        region=region,
        request_id=request_id,
    ).to_dict(include_raw=include_raw)


def process_with_fallback(
    bucket: str,
    key: str,
    region: str | None = None,
    profile_path: str | None = None,
    aws_profile: str | None = None,
    request_id: str | None = None,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Legacy alias; fallback behavior is now part of the profile."""

    return process_document(
        bucket=bucket,
        key=key,
        region=region,
        profile_path=profile_path,
        aws_profile=aws_profile,
        request_id=request_id,
        include_raw=include_raw,
    )


def normalize_textract(
    response: dict[str, Any],
    profile_path: str | None = None,
    mode: str | None = None,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Normalize a captured Textract response for compatibility and testing."""

    profile = load_profile(_profile_path(profile_path))
    inferred_mode = mode
    if inferred_mode is None:
        if "ExpenseDocuments" in response:
            inferred_mode = "expense"
        elif "IdentityDocuments" in response:
            inferred_mode = "id"
        else:
            inferred_mode = "queries"
    provider = TextractProvider(
        "textract",
        client=None,
        options={"mode": inferred_mode},
    )
    return provider.normalize_response(response, profile).to_dict(
        include_raw=include_raw
    )


def normalize_bda(
    response: dict[str, Any],
    profile_path: str | None = None,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Normalize a captured BDA payload for compatibility and testing."""

    profile = load_profile(_profile_path(profile_path))
    provider = BdaProvider(
        "bda",
        client=None,
        s3_client=None,
        options={
            "mode": "sync",
            "data_automation_profile_arn": "compatibility",
            "blueprints": [{"blueprintArn": "compatibility"}],
        },
    )
    if "outputSegments" in response:
        result = provider.normalize_response(response, profile)
    else:
        result = provider.normalize_payload(response, profile)
    return result.to_dict(include_raw=include_raw)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bucket")
    parser.add_argument("key")
    parser.add_argument("--profile", required=True, dest="profile_path")
    parser.add_argument("--aws-profile")
    parser.add_argument("--region")
    parser.add_argument("--request-id")
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include raw text, tables, and provider metadata in stdout",
    )
    args = parser.parse_args()
    result = process_document(
        bucket=args.bucket,
        key=args.key,
        profile_path=args.profile_path,
        aws_profile=args.aws_profile,
        region=args.region,
        request_id=args.request_id,
        include_raw=args.include_raw,
    )
    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
