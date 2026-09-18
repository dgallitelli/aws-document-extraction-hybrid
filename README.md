# AWS Intelligent Document Processing Starter

A customer-agnostic starter for policy-driven document extraction with Amazon
Textract, Amazon Bedrock Data Automation (BDA), and multimodal models on Amazon
Bedrock.

The repository standardizes the reusable part of an IDP engagement:

- versioned document profiles;
- operation-specific AWS adapters;
- deterministic fallback and merge rules;
- per-field confidence and provenance;
- schema and runtime validation;
- a service-neutral human-review signal.

It does not assume one AWS service is always best. Provider order is selected
from evaluation evidence for each document family.

## Architecture

```text
S3 document + profile
          |
          v
Ordered provider policy
  | Textract Expense / ID / Queries
  | BDA project or blueprints
  | Bedrock structured-output fallback
          |
          v
Canonical result envelope
  fields + confidence + provenance
  alternatives + timings + warnings
          |
          v
accepted | needs_review
```

See [docs/architecture.md](docs/architecture.md) for the decision record and
production evolution path. See
[docs/operations-security.md](docs/operations-security.md) before deployment.
The disposable live-test procedure is in
[docs/live-validation.md](docs/live-validation.md).

## Quick start

Create the environment outside the repository:

```bash
python3 -m venv /tmp/aws-idp-starter/.venv
source /tmp/aws-idp-starter/.venv/bin/activate
python -m pip install -e '.[dev]'
```

Authenticate and choose a Region:

```bash
aws sso login --profile my-profile
export AWS_PROFILE=my-profile
export AWS_REGION=us-east-1
```

Process an invoice already stored in S3:

```bash
python process_document.py my-bucket invoices/example.pdf \
  --profile profiles/invoice.json \
  --request-id engagement-run-001
```

Or call the library:

```python
from idp_starter import process_s3_document

result = process_s3_document(
    bucket="my-bucket",
    key="invoices/example.pdf",
    profile_path="profiles/invoice.json",
)

if result.status == "needs_review":
    print([reason.code for reason in result.review_reasons])
```

The invoice profile uses Textract Expense first. It resolves
`${BEDROCK_MODEL_ID}` only if the fallback is actually needed.

## Profiles

Profiles are JSON and own all engagement-specific behavior:

- canonical fields and aliases;
- required fields and value validation;
- provider-specific confidence thresholds;
- ordered AWS providers and their options;
- sync or async BDA execution mode.

Included examples:

- `profiles/invoice.json`: Textract Expense with Bedrock fallback;
- `profiles/generic-bda.json`: asynchronous BDA with Bedrock fallback.

For the BDA example, configure:

```bash
export BDA_PROFILE_ARN='arn:aws:bedrock:...:data-automation-profile/...'
export BDA_PROJECT_ARN='arn:aws:bedrock:...:data-automation-project/...'
export BDA_OUTPUT_S3_URI='s3://my-idp-output/bda'
export BEDROCK_MODEL_ID='your-cross-region-inference-profile-id'
```

## Result contract

The result contains:

- profile name and version;
- `accepted` or `needs_review`, with review reasons shaped as
  `{code, message, field, provider, details}`;
- canonical fields with provider, native confidence, raw key, page/geometry
  provenance, and model/project metadata where available;
- conflicting alternatives without overwriting the first authoritative value;
- providers attempted, provider timings, warnings, raw text, and tables.

Textract and BDA confidence values are not treated as interchangeable. Set
thresholds by field and provider name. BDA blueprint-match confidence is
provider metadata, not field confidence. BDA field confidence and geometry are
joined from `explainability_info` when present; otherwise
`require_confidence` correctly sends an unscored field to review. The Textract
Expense adapter conservatively normalizes currency-symbol amounts and
unambiguous common date formats before profile validation.

The CLI and `to_dict()` methods print canonical fields and review information
by default. Raw text, tables, and provider metadata require the explicit
`--include-raw` flag or `include_raw=True`.

## Legacy imports

The original top-level modules remain as compatibility wrappers:

- `process_document.py`
- `classify_document.py`
- `extract_bda.py`
- `extract_claude.py`

New code should import `idp_starter`. Legacy calls now require a profile through
`profile_path` or `IDP_PROFILE`; fixed model IDs, Regions, and project ARNs were
removed. The optional classifier retains an overridable generic taxonomy for
import compatibility. The wrappers return the new normalized provider/result
contracts rather than reproducing the prototype's inconsistent dictionaries.

## Validation

```bash
python -m pytest
```

Unit tests use captured response shapes and fake clients. They do not create
AWS resources or send document contents to AWS.

The repository also includes a disposable CDK integration stack and sanitized
live harness. The harness validates:

- Textract `AnalyzeExpense`;
- Bedrock Converse structured output with a PDF-capable inference profile;
- BDA synchronous image extraction and asynchronous PDF extraction;
- deterministic Textract-to-Bedrock fallback;
- encrypted, TLS-only, non-public temporary storage;
- BDA manifest traversal and complete per-job output cleanup.

The harness generates a synthetic invoice with known values, then destroys its
uploaded inputs. Run it and tear down the stack:

```bash
export AWS_PROFILE=default
export AWS_REGION="$(aws configure get region --profile "$AWS_PROFILE")"
export AWS_DEFAULT_REGION="$AWS_REGION"

cd infra
npm ci
npm test
npx cdk diff AwsIdpStarterLiveValidation --profile "$AWS_PROFILE" --strict
npx cdk deploy AwsIdpStarterLiveValidation \
  --profile "$AWS_PROFILE" \
  --require-approval never
cd ..

python scripts/live_validation.py \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

cd infra
npx cdk destroy AwsIdpStarterLiveValidation \
  --profile "$AWS_PROFILE" \
  --force
```

See [docs/live-validation.md](docs/live-validation.md) for the expected report,
teardown audit, and the latest verified result.

The latest verified run used the `default` profile in `us-east-1` on
2026-09-18. All live checks passed, CDK reported no post-deploy differences,
and the disposable stack and its resources were removed afterward.

## Production boundary

This repository is the extraction core, not a complete production platform.
Production engagements should add durable asynchronous orchestration,
idempotency, malware and content validation, authentication, tenant isolation,
encrypted result storage, lifecycle cleanup, metrics, review workflow, and a
representative evaluation dataset.

Do not log document bodies or extracted PII. Review data residency before using
cross-Region inference. Remove test buckets, BDA output, and other engagement
resources after validation.
