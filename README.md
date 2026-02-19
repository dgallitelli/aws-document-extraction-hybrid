# AWS Document Extraction - Hybrid Architecture

A cost-optimized hybrid approach combining Amazon Textract and Bedrock Data Automation (BDA).

**Blog post:** [Amazon Textract vs Bedrock Data Automation: A Cost-Optimized Hybrid Approach](https://dgallitelli95.medium.com/amazon-textract-vs-bedrock-data-automation-a-cost-optimized-hybrid-approach-66b730c41c94)

## The Hybrid Approach

Route documents to the optimal service based on their characteristics:

- **Standardized forms** → Textract (cheaper, deterministic)
- **Variable documents** → BDA (handles layout variability)

```
┌─────────────────────────────────┐
│     Document Classifier         │
│  (Claude 4.5 + Structured Out)  │
└───────────────┬─────────────────┘
                │
        ┌───────┴───────┐
        ▼               ▼
  ┌───────────┐   ┌───────────┐
  │ Textract  │   │    BDA    │
  │ (standard)│   │ (variable)│
  └───────────┘   └───────────┘
```

## Quick Start

```bash
pip install -r requirements.txt
aws sso login
```

```python
from process_document import process_document

result = process_document("my-bucket", "document.pdf")
# Automatically routes to Textract or BDA based on classification
```

## Files

| File | Description |
|------|-------------|
| `process_document.py` | Main routing logic with fallback |
| `classify_document.py` | Classification using Structured Outputs |
| `extract_bda.py` | BDA extraction (async with polling) |
| `extract_claude.py` | Claude extraction via Converse API |

## Key Implementation Notes

### BDA Requirements

```python
bda.invoke_data_automation_async(
    inputConfiguration={"s3Uri": f"s3://{bucket}/{key}"},
    outputConfiguration={"s3Uri": f"s3://{bucket}/output/"},
    dataAutomationConfiguration={
        "dataAutomationProjectArn": project_arn,  # NOT "dataAutomationArn"
        "stage": "LIVE"
    },
    dataAutomationProfileArn=profile_arn  # REQUIRED
)
```

- `dataAutomationProfileArn` is **required** (account-specific)
- BDA is **async-only** - must poll `get_data_automation_status()`
- Status value is `"Success"` (not `"COMPLETED"`)

### Classification with Structured Outputs

```python
response = bedrock.converse(
    modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    messages=[{
        "role": "user",
        "content": [
            {"document": {"format": "pdf", "source": {"bytes": pdf_bytes}}},
            {"text": "Classify this document."}
        ]
    }],
    outputConfig={
        "textFormat": {
            "type": "json_schema",
            "structure": {"jsonSchema": {"schema": SCHEMA, "name": "classification"}}
        }
    }
)
```

- Uses `outputConfig.textFormat` with JSON schema
- Enum constraints guarantee valid categories
- No string parsing needed

## Cost Comparison

| Scenario | Monthly Cost (100K docs) |
|----------|-------------------------|
| All BDA (Custom) | $4,000 |
| **Hybrid approach** | **$1,825** |
| Savings | **54%** |
