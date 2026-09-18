#!/usr/bin/env python3
"""Run sanitized live integration checks against a disposable CDK stack."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
import tempfile
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from PIL import Image, ImageDraw

from idp_starter.config import DocumentProfile, load_profile
from idp_starter.models import DocumentSource
from idp_starter.pipeline import DocumentPipeline
from idp_starter.providers import AwsProviderFactory
from idp_starter.providers.bda import BdaProvider
from idp_starter.providers.bedrock import BedrockProvider
from idp_starter.providers.textract import TextractProvider


STACK_NAME = "AwsIdpStarterLiveValidation"
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SUPPORTED_REGIONS = {"us-east-1", "us-west-2"}
SYNTHETIC_EXPECTED: dict[str, str] = {
    "currency": "SGD",
    "customer_name": "Sample Customer",
    "due_date": "2026-09-30",
    "invoice_date": "2026-09-01",
    "invoice_number": "TEST-2026-0918",
    "supplier_name": "Example Utilities Pte Ltd",
    "total_amount": "123.45",
}
CORE_EXPECTED_FIELDS = {
    "invoice_date",
    "invoice_number",
    "supplier_name",
    "total_amount",
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region")
    parser.add_argument("--stack-name", default=STACK_NAME)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("live-validation-report.json"),
    )
    return parser.parse_args()


def _stack_outputs(
    cloudformation: Any, stack_name: str
) -> dict[str, str]:
    response = cloudformation.describe_stacks(StackName=stack_name)
    outputs = response["Stacks"][0].get("Outputs", [])
    return {
        item["OutputKey"]: item["OutputValue"]
        for item in outputs
        if "OutputKey" in item and "OutputValue" in item
    }


def _synthetic_invoice(image_path: Path, document_path: Path) -> None:
    image = Image.new("RGB", (1600, 1100), "white")
    draw = ImageDraw.Draw(image)
    lines = [
        "SYNTHETIC INVOICE - TEST DATA ONLY",
        "Supplier: Example Utilities Pte Ltd",
        "Customer: Sample Customer",
        "Invoice Number: TEST-2026-0918",
        "Invoice Date: 2026-09-01",
        "Due Date: 2026-09-30",
        "Currency: SGD",
        "Total Amount Due: 123.45",
    ]
    for index, line in enumerate(lines):
        draw.text((90, 100 + index * 105), line, fill="black")
    image.save(image_path, format="PNG")
    image.save(document_path, format="PDF", resolution=150)


def _timed(callable_: Any) -> tuple[Any, float]:
    started = time.monotonic()
    result = callable_()
    return result, round(time.monotonic() - started, 3)


def _provider_summary(result: Any, duration: float) -> dict[str, Any]:
    return {
        "duration_seconds": duration,
        "field_count": len(result.fields),
        "field_names": sorted(result.fields),
        "provider": result.provider,
        "raw_text_present": bool(result.raw_text),
    }


def _pipeline_profile(model_id: str) -> DocumentProfile:
    return DocumentProfile.from_dict(
        {
            "name": "live-fallback-check",
            "version": "1.0",
            "deadline_seconds": 240,
            "fallback_on_low_confidence": True,
            "fields": {
                "supplier_name": {
                    "description": "Supplier or issuer name",
                    "required": True,
                    "aliases": ["VENDOR_NAME"],
                    "require_confidence": True,
                    "confidence_thresholds": {"textract-expense": 1.0},
                },
                "total_amount": {
                    "description": "Final total amount due",
                    "required": True,
                    "aliases": ["TOTAL", "AMOUNT_DUE"],
                    "require_confidence": True,
                    "value_type": "number",
                    "confidence_thresholds": {"textract-expense": 1.0},
                },
            },
            "providers": [
                {
                    "name": "textract-expense",
                    "type": "textract",
                    "mode": "expense",
                },
                {
                    "name": "bedrock-fallback",
                    "type": "bedrock",
                    "model_id": model_id,
                    "max_tokens": 500,
                    "require_review": True,
                },
            ],
        }
    )


def _assert_expected_fields(
    result: Any,
    names: set[str],
    label: str,
) -> None:
    missing = names - result.fields.keys()
    if missing:
        raise AssertionError(
            f"{label} missed expected fields: {sorted(missing)}"
        )
    for name in names:
        actual = result.fields[name].value
        expected = SYNTHETIC_EXPECTED[name]
        if name == "total_amount":
            try:
                matches = Decimal(str(actual)) == Decimal(expected)
            except InvalidOperation:
                matches = False
        else:
            matches = str(actual).strip() == expected
        if not matches:
            raise AssertionError(
                f"{label} returned an incorrect value for {name}"
            )


def _list_keys(s3: Any, bucket: str, prefix: str) -> list[str]:
    paginator = s3.get_paginator("list_objects_v2")
    return [
        item["Key"]
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
        for item in page.get("Contents", [])
    ]


def _has_tls_only_policy(policy: dict[str, Any]) -> bool:
    statements = policy.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    return any(
        statement.get("Effect") == "Deny"
        and statement.get("Action") == "s3:*"
        and statement.get("Condition", {})
        .get("Bool", {})
        .get("aws:SecureTransport")
        in {"false", False}
        for statement in statements
        if isinstance(statement, dict)
    )


def main() -> int:
    args = _arguments()
    session = boto3.Session(
        profile_name=args.profile,
        region_name=args.region,
    )
    region = args.region or session.region_name
    if not region:
        raise RuntimeError("AWS Region is not configured")
    if region not in SUPPORTED_REGIONS:
        raise RuntimeError(
            "The disposable BDA validation stack supports us-east-1 or "
            "us-west-2 only"
        )

    account = session.client("sts").get_caller_identity()["Account"]
    client_config = Config(
        connect_timeout=10,
        read_timeout=360,
        retries={"mode": "standard", "total_max_attempts": 3},
    )
    cloudformation = session.client(
        "cloudformation", config=client_config
    )
    outputs = _stack_outputs(cloudformation, args.stack_name)
    required_outputs = {
        "ValidationBucketName",
        "BdaSyncProjectArn",
        "BdaSyncProjectStage",
        "BdaAsyncProjectArn",
        "BdaAsyncProjectStage",
    }
    missing_outputs = required_outputs - outputs.keys()
    if missing_outputs:
        raise RuntimeError(
            f"Stack is missing outputs: {sorted(missing_outputs)}"
        )

    bucket = outputs["ValidationBucketName"]
    s3 = session.client("s3", config=client_config)
    textract = session.client("textract", config=client_config)
    bedrock = session.client("bedrock-runtime", config=client_config)
    bda_runtime = session.client(
        "bedrock-data-automation-runtime",
        config=client_config,
    )
    invoice_profile = load_profile("profiles/invoice.json")
    profile_arn = (
        f"arn:aws:bedrock:{region}:{account}:"
        "data-automation-profile/us.data-automation-v1"
    )
    run_id = str(time.time_ns())
    document_key = "input/synthetic-invoice.pdf"
    image_key = "input/synthetic-invoice.png"
    uploaded_keys: list[str] = []

    try:
        with tempfile.TemporaryDirectory(
            prefix="aws-idp-live-"
        ) as directory:
            image_path = Path(directory) / "synthetic-invoice.png"
            document_path = Path(directory) / "synthetic-invoice.pdf"
            _synthetic_invoice(image_path, document_path)
            for path, key in (
                (document_path, document_key),
                (image_path, image_key),
            ):
                s3.upload_file(str(path), bucket, key)
                uploaded_keys.append(key)

            document_source = DocumentSource(
                bucket=bucket,
                key=document_key,
                request_id=f"live-validation-pdf-{run_id}",
            )
            image_source = DocumentSource(
                bucket=bucket,
                key=image_key,
                request_id=f"live-validation-image-{run_id}",
            )

            textract_result, textract_seconds = _timed(
                lambda: TextractProvider(
                    "textract-expense",
                    textract,
                    {"mode": "expense"},
                ).extract(document_source, invoice_profile)
            )
            if not textract_result.raw_text:
                raise AssertionError("Textract returned no document text")
            _assert_expected_fields(
                textract_result,
                CORE_EXPECTED_FIELDS,
                "Textract",
            )

            bedrock_result, bedrock_seconds = _timed(
                lambda: BedrockProvider(
                    "bedrock-structured-output",
                    bedrock,
                    s3,
                    {
                        "model_id": args.model_id,
                        "max_tokens": 1200,
                        "require_review": True,
                    },
                ).extract(document_source, invoice_profile)
            )
            _assert_expected_fields(
                bedrock_result,
                set(SYNTHETIC_EXPECTED),
                "Bedrock",
            )

            sync_result, sync_seconds = _timed(
                lambda: BdaProvider(
                    "bda-sync",
                    bda_runtime,
                    s3,
                    {
                        "mode": "sync",
                        "data_automation_profile_arn": profile_arn,
                        "data_automation_project_arn": outputs[
                            "BdaSyncProjectArn"
                        ],
                        "stage": outputs["BdaSyncProjectStage"],
                        "timeout_seconds": 300,
                    },
                ).extract(image_source, invoice_profile)
            )
            _assert_expected_fields(
                sync_result,
                set(SYNTHETIC_EXPECTED),
                "Synchronous BDA",
            )

            async_result, async_seconds = _timed(
                lambda: BdaProvider(
                    "bda-async",
                    bda_runtime,
                    s3,
                    {
                        "mode": "async",
                        "data_automation_profile_arn": profile_arn,
                        "data_automation_project_arn": outputs[
                            "BdaAsyncProjectArn"
                        ],
                        "stage": outputs["BdaAsyncProjectStage"],
                        "output_s3_uri": f"s3://{bucket}/output",
                        "timeout_seconds": 600,
                        "poll_seconds": 5,
                        "cleanup_output": True,
                    },
                ).extract(document_source, invoice_profile)
            )
            _assert_expected_fields(
                async_result,
                CORE_EXPECTED_FIELDS,
                "Asynchronous BDA",
            )
            if async_result.metadata.get("output_cleaned_up") is not True:
                raise AssertionError(
                    "Asynchronous BDA output was not cleaned up"
                )
            if _list_keys(s3, bucket, "output/"):
                raise AssertionError(
                    "Asynchronous BDA output prefix is not empty"
                )

            pipeline_profile = _pipeline_profile(args.model_id)
            pipeline_result, pipeline_seconds = _timed(
                lambda: DocumentPipeline(
                    profile=pipeline_profile,
                    provider_factory=AwsProviderFactory(
                        session,
                        region=region,
                    ),
                ).process(
                    DocumentSource(
                        bucket=bucket,
                        key=document_key,
                        request_id=f"live-validation-fallback-{run_id}",
                    )
                )
            )
            if pipeline_result.providers_attempted != [
                "textract-expense",
                "bedrock-fallback",
            ]:
                raise AssertionError(
                    "Pipeline did not execute the deterministic fallback path"
                )
            _assert_expected_fields(
                pipeline_result,
                {"supplier_name", "total_amount"},
                "Pipeline",
            )
    finally:
        if uploaded_keys:
            response = s3.delete_objects(
                Bucket=bucket,
                Delete={
                    "Objects": [{"Key": key} for key in uploaded_keys],
                    "Quiet": True,
                },
            )
            if response.get("Errors"):
                raise RuntimeError(
                    "Live-validation input cleanup was incomplete"
                )

    if any(
        key in _list_keys(s3, bucket, "input/")
        for key in uploaded_keys
    ):
        raise AssertionError("Uploaded live-validation inputs remain in S3")

    encryption = s3.get_bucket_encryption(Bucket=bucket)
    encryption_algorithm = encryption[
        "ServerSideEncryptionConfiguration"
    ]["Rules"][0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
    if encryption_algorithm != "AES256":
        raise AssertionError("Validation bucket is not using SSE-S3")

    public_access = s3.get_public_access_block(Bucket=bucket)[
        "PublicAccessBlockConfiguration"
    ]
    public_access_keys = {
        "BlockPublicAcls",
        "BlockPublicPolicy",
        "IgnorePublicAcls",
        "RestrictPublicBuckets",
    }
    if not all(
        public_access.get(key) is True for key in public_access_keys
    ):
        raise AssertionError("Validation bucket does not block all public access")

    bucket_policy = json.loads(
        s3.get_bucket_policy(Bucket=bucket)["Policy"]
    )
    if not _has_tls_only_policy(bucket_policy):
        raise AssertionError("Validation bucket does not enforce TLS-only access")

    report = {
        "model_id": args.model_id,
        "profile": args.profile,
        "region": region,
        "stack_name": args.stack_name,
        "storage": {
            "bda_output_empty": True,
            "encryption_algorithm": encryption_algorithm,
            "input_objects_cleaned_up": True,
            "public_access_block": public_access,
            "tls_only_policy": True,
        },
        "tests": {
            "bda_async": _provider_summary(
                async_result, async_seconds
            ),
            "bda_sync": _provider_summary(sync_result, sync_seconds),
            "bedrock": _provider_summary(
                bedrock_result, bedrock_seconds
            ),
            "pipeline_fallback": {
                "duration_seconds": pipeline_seconds,
                "field_count": len(pipeline_result.fields),
                "providers_attempted": pipeline_result.providers_attempted,
                "status": pipeline_result.status,
                "validated_field_names": [
                    "supplier_name",
                    "total_amount",
                ],
            },
            "textract": _provider_summary(
                textract_result, textract_seconds
            ),
        },
        "validated_values": {
            "fixture": "generated synthetic invoice",
            "field_names": sorted(SYNTHETIC_EXPECTED),
            "values_omitted": True,
        },
    }
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(args.report),
                "services_validated": sorted(report["tests"]),
                "success": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
