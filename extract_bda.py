"""
Extract documents using Bedrock Data Automation (BDA).

IMPORTANT: BDA is async-only and requires polling for completion.
"""
import boto3
import json
import time


def extract_with_bda(
    bucket: str,
    key: str,
    region: str = "us-east-1",
    timeout: int = 120
) -> dict:
    """
    Extract document using Bedrock Data Automation.

    Returns markdown output with preserved layout.

    Args:
        bucket: S3 bucket name
        key: S3 object key
        region: AWS region
        timeout: Max seconds to wait

    Returns:
        Dict with markdown, elements_count, table_count

    CRITICAL:
    - dataAutomationProfileArn is REQUIRED
    - Parameter is dataAutomationProjectArn (NOT dataAutomationArn)
    - Async-only: poll get_data_automation_status
    - Status is "Success" (NOT "COMPLETED")
    """
    sts = boto3.client("sts", region_name=region)
    bda = boto3.client("bedrock-data-automation-runtime", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    account_id = sts.get_caller_identity()["Account"]

    # Required ARNs
    profile_arn = f"arn:aws:bedrock:{region}:{account_id}:data-automation-profile/us.data-automation-v1"
    project_arn = f"arn:aws:bedrock:{region}:aws:data-automation-project/public-default"

    output_prefix = f"bda-output/{key.replace('.pdf', '').replace('/', '-')}/"

    response = bda.invoke_data_automation_async(
        inputConfiguration={"s3Uri": f"s3://{bucket}/{key}"},
        outputConfiguration={"s3Uri": f"s3://{bucket}/{output_prefix}"},
        dataAutomationConfiguration={
            "dataAutomationProjectArn": project_arn,
            "stage": "LIVE"
        },
        dataAutomationProfileArn=profile_arn
    )

    invocation_arn = response["invocationArn"]
    start_time = time.time()

    while time.time() - start_time < timeout:
        status_response = bda.get_data_automation_status(invocationArn=invocation_arn)
        status = status_response.get("status")

        if status == "Success":
            return _parse_output(s3, bucket, output_prefix)
        elif status in ["ClientError", "ServiceError", "FAILED"]:
            raise Exception(f"BDA failed: {status_response.get('errorMessage', status)}")

        time.sleep(5)

    raise TimeoutError(f"BDA timeout after {timeout}s")


def _parse_output(s3, bucket: str, prefix: str) -> dict:
    """Parse BDA output from S3."""
    response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)

    result = {"markdown": None, "elements_count": 0, "table_count": 0}

    for obj in response.get("Contents", []):
        if obj["Key"].endswith("result.json"):
            data = s3.get_object(Bucket=bucket, Key=obj["Key"])
            content = json.loads(data["Body"].read().decode("utf-8"))

            pages = content.get("pages", [])
            if pages:
                result["markdown"] = pages[0].get("representation", {}).get("markdown", "")

            stats = content.get("document", {}).get("statistics", {})
            result["elements_count"] = stats.get("element_count", 0)
            result["table_count"] = stats.get("table_count", 0)

    return result
