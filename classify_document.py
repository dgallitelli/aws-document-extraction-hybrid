"""
Classify documents using Claude 4.5 Haiku with Bedrock Structured Outputs.

Uses outputConfig.textFormat to guarantee valid JSON responses.
"""
import boto3
import json


# Valid document categories
DOC_TYPES = ["invoice", "receipt", "drivers_license", "passport", "w2", "tax_form", "contract", "other"]
LAYOUTS = ["standard", "variable"]

# JSON Schema for classification
CLASSIFICATION_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "doc_type": {
            "type": "string",
            "enum": DOC_TYPES,
            "description": "The document type"
        },
        "layout": {
            "type": "string",
            "enum": LAYOUTS,
            "description": "Whether the document has a standard template or variable layout"
        }
    },
    "required": ["doc_type", "layout"]
})


def classify(bucket: str, key: str, region: str = "us-east-1") -> dict:
    """
    Classify a document using Claude 4.5 Haiku with Structured Outputs.

    Uses Bedrock's outputConfig.textFormat to guarantee valid JSON responses
    that conform to the classification schema.

    Args:
        bucket: S3 bucket name
        key: S3 object key
        region: AWS region

    Returns:
        Dict with doc_type and layout fields
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
                {"text": "Classify this document by type and layout consistency."}
            ]
        }],
        outputConfig={
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": {
                        "schema": CLASSIFICATION_SCHEMA,
                        "name": "classification",
                        "description": "Document classification result"
                    }
                }
            }
        }
    )

    return json.loads(response["output"]["message"]["content"][0]["text"])


if __name__ == "__main__":
    # Example usage
    result = classify(bucket="my-bucket", key="document.pdf")
    print(f"Type: {result['doc_type']}, Layout: {result['layout']}")
