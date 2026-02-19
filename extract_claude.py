"""
Extract structured data from documents using Claude 4.5 via Bedrock Converse API.
"""
import boto3
import json

# Claude 4.5 models (cross-region inference profiles)
MODELS = {
    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "opus": "us.anthropic.claude-opus-4-5-20251101-v1:0",
}


def extract_document(
    bucket: str,
    key: str,
    prompt: str = None,
    model: str = "haiku",
    region: str = "us-east-1"
) -> dict:
    """
    Extract structured data from a PDF using Claude 4.5.

    Args:
        bucket: S3 bucket name
        key: S3 object key
        prompt: Extraction prompt (default: generic structured extraction)
        model: Claude model - haiku (fast/cheap), sonnet (balanced), opus (accurate)
        region: AWS region

    Returns:
        Extracted data as dictionary
    """
    s3 = boto3.client("s3", region_name=region)
    bedrock = boto3.client("bedrock-runtime", region_name=region)

    # Download PDF from S3
    obj = s3.get_object(Bucket=bucket, Key=key)
    pdf_bytes = obj["Body"].read()

    if prompt is None:
        prompt = """Extract all data from this document as structured JSON.
Include all text, tables, and form fields. Return ONLY valid JSON."""

    # Call Claude via Converse API
    response = bedrock.converse(
        modelId=MODELS.get(model, MODELS["haiku"]),
        messages=[{
            "role": "user",
            "content": [
                {
                    "document": {
                        "format": "pdf",
                        "name": "document",
                        "source": {"bytes": pdf_bytes}
                    }
                },
                {"text": prompt}
            ]
        }],
        inferenceConfig={"maxTokens": 4096, "temperature": 0}
    )

    output_text = response["output"]["message"]["content"][0]["text"]

    try:
        json_str = output_text.strip()
        if json_str.startswith("```"):
            json_str = json_str.split("```")[1]
            if json_str.startswith("json"):
                json_str = json_str[4:]
        return json.loads(json_str)
    except json.JSONDecodeError:
        return {"raw_text": output_text}
