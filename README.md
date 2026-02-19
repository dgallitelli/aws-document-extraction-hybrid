# AWS Document Extraction - Hybrid Architecture

Compare and combine AWS document extraction services for optimal cost and accuracy.

## Services Compared

| Service | Best For | Cost/1000 docs | Notes |
|---------|----------|----------------|-------|
| **Claude 4.5 Haiku** | All documents | ~$5 | Best value, structured JSON output |
| **Claude 4.5 Sonnet** | Complex layouts | ~$25 | Higher accuracy for difficult docs |
| **Bedrock Data Automation** | Layout preservation | ~$50 | Markdown output with structure |
| **Amazon Textract** | Standardized forms | ~$95 | Forms and tables extraction |

## Quick Start

```bash
pip install -r requirements.txt
aws sso login
```

```python
from extract_claude import extract_document
from extract_bda import extract_with_bda

# Extract with Claude (recommended)
result = extract_document("bucket", "document.pdf", model="haiku")

# Or use BDA for markdown output
result = extract_with_bda("bucket", "document.pdf")
```

## Files

- `extract_claude.py` - Claude 4.5 extraction via Converse API
- `extract_bda.py` - Bedrock Data Automation (async API)
- `classify_document.py` - Document classification with Structured Outputs
- `requirements.txt` - Dependencies

## Key Implementation Notes

### BDA Requirements
- `dataAutomationProfileArn` is **required** (account-specific)
- Parameter is `dataAutomationProjectArn` (not `dataAutomationArn`)
- BDA is **async-only** - must poll for completion
- Status value is `"Success"` (not `"COMPLETED"`)

### Claude via Converse API
- Pass document bytes directly (cannot read S3 via text prompt)
- Use cross-region inference profile IDs (`us.anthropic.claude-*`)
- Temperature 0 for consistent extraction

### Structured Outputs for Classification
- Use `toolConfig` with `toolChoice` to guarantee valid responses
- Enum constraints prevent hallucinated categories
- No string parsing needed
