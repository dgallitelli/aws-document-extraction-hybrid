"""
Classify documents using Claude 4.5 Haiku with Structured Outputs.

Uses Bedrock's toolConfig with toolChoice to guarantee valid classification.
"""
import boto3


# Valid document categories
DOC_TYPES = ["invoice", "tax-form", "government-form", "complex-layout", "other"]


def classify(bucket: str, key: str, region: str = "us-east-1") -> str:
    """
    Classify a document using Claude 4.5 Haiku with Structured Outputs.

    Uses toolConfig with enum constraint to guarantee valid response.

    Args:
        bucket: S3 bucket name
        key: S3 object key
        region: AWS region

    Returns:
        Document type: invoice, tax-form, government-form, complex-layout, or other
    """
    s3 = boto3.client("s3", region_name=region)
    bedrock = boto3.client("bedrock-runtime", region_name=region)

    pdf_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    response = bedrock.converse(
        modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        messages=[{
            "role": "user",
            "content": [
                {"document": {"format": "pdf", "source": {"bytes": pdf_bytes}}},
                {"text": "Classify this document."}
            ]
        }],
        toolConfig={
            "tools": [{
                "toolSpec": {
                    "name": "classify",
                    "description": "Classify the document type",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "doc_type": {
                                    "type": "string",
                                    "enum": DOC_TYPES
                                }
                            },
                            "required": ["doc_type"]
                        }
                    }
                }
            }],
            "toolChoice": {"tool": {"name": "classify"}}
        }
    )

    return response["output"]["message"]["content"][0]["toolUse"]["input"]["doc_type"]


if __name__ == "__main__":
    # Example usage
    doc_type = classify(bucket="my-bucket", key="document.pdf")
    print(f"Document type: {doc_type}")
