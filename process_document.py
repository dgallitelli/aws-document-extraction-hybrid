"""
Hybrid document processing with intelligent routing.

Routes standardized documents to Textract, variable documents to BDA.
"""
import boto3
from classify_document import classify
from extract_bda import extract_with_bda


# Routing map for standardized document types
TEXTRACT_ROUTES = {
    "invoice": "expense",
    "receipt": "expense",
    "drivers_license": "id",
    "passport": "id",
    "w2": "forms",
    "tax_form": "forms",
}


def process_document(bucket: str, key: str, region: str = "us-east-1") -> dict:
    """
    Process document with intelligent routing.

    Classifies the document, then routes to optimal service:
    - Standardized forms → Textract (cheaper, deterministic)
    - Variable documents → BDA (handles layout variability)

    Args:
        bucket: S3 bucket name
        key: S3 object key
        region: AWS region

    Returns:
        Normalized extraction result
    """
    textract = boto3.client("textract", region_name=region)

    # Classify document
    classification = classify(bucket, key, region)
    doc_type = classification["doc_type"]
    layout = classification["layout"]

    # Route standardized documents to Textract
    if layout == "standard" and doc_type in TEXTRACT_ROUTES:
        route = TEXTRACT_ROUTES[doc_type]

        if route == "expense":
            response = textract.analyze_expense(
                Document={"S3Object": {"Bucket": bucket, "Name": key}}
            )
            return normalize_textract(response)

        elif route == "id":
            response = textract.analyze_id(
                DocumentPages=[{"S3Object": {"Bucket": bucket, "Name": key}}]
            )
            return normalize_textract(response)

        elif route == "forms":
            response = textract.analyze_document(
                Document={"S3Object": {"Bucket": bucket, "Name": key}},
                FeatureTypes=["FORMS", "TABLES"]
            )
            return normalize_textract(response)

    # Variable documents go to BDA
    response = extract_with_bda(bucket, key, region)
    return normalize_bda(response)


def process_with_fallback(bucket: str, key: str, region: str = "us-east-1") -> dict:
    """
    Process document with automatic fallback to BDA on errors.
    """
    try:
        classification = classify(bucket, key, region)

        if classification["layout"] == "standard":
            result = process_document(bucket, key, region)
            # Fall back to BDA for low-confidence results
            if result.get("confidence", 1.0) < 0.7:
                return normalize_bda(extract_with_bda(bucket, key, region))
            return result

        return normalize_bda(extract_with_bda(bucket, key, region))

    except Exception:
        # BDA as universal fallback
        return normalize_bda(extract_with_bda(bucket, key, region))


def normalize_textract(response: dict) -> dict:
    """Convert Textract response to unified format."""
    fields = {}
    tables = []
    raw_text_lines = []

    for block in response.get("Blocks", []):
        if block["BlockType"] == "KEY_VALUE_SET":
            # Extract form fields (simplified)
            pass
        elif block["BlockType"] == "TABLE":
            tables.append(block)
        elif block["BlockType"] == "LINE":
            raw_text_lines.append(block.get("Text", ""))

    return {
        "source": "textract",
        "fields": fields,
        "tables": tables,
        "raw_text": "\n".join(raw_text_lines)
    }


def normalize_bda(response: dict) -> dict:
    """Convert BDA response to unified format."""
    return {
        "source": "bda",
        "fields": {},
        "tables": [],
        "raw_text": response.get("markdown", ""),
        "elements_count": response.get("elements_count", 0),
        "table_count": response.get("table_count", 0)
    }


if __name__ == "__main__":
    # Example usage
    result = process_document(bucket="my-bucket", key="document.pdf")
    print(f"Source: {result['source']}")
    print(f"Raw text preview: {result['raw_text'][:200]}...")
