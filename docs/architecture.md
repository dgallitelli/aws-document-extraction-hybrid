# Standard AWS Intelligent Document Processing Architecture

## Decision

Use one stable result contract with a profile-driven provider policy.

The standard is not “always use Textract,” “always use Bedrock Data Automation,”
or “always use a multimodal model.” Those services solve different extraction
problems. The reusable part is the control plane around them:

1. A document profile defines the target fields, aliases, review policy, and
   ordered providers.
2. Provider adapters normalize service-specific responses into one field
   contract.
3. The pipeline preserves the first authoritative value, records alternative
   candidates, and uses later providers only to fill gaps or provide evidence.
4. Required-field, provider-specific confidence, and provenance checks decide
   whether the result is accepted or needs human review.

## Why this replaces the original prototype

| Concern | Original repository | Standardized approach |
|---|---|---|
| Document taxonomy | Hardcoded in Python | Defined per profile |
| Routing | LLM decides “standard” vs “variable” for every file | Ordered provider policy chosen from benchmark evidence |
| Textract parsing | One generic `Blocks` loop | Operation-specific Expense, ID, and Queries normalization |
| Bedrock models | Fixed model IDs | Configuration or environment |
| BDA | Fixed public project and fragile output prefix | Modality-specific projects/blueprints, manifest-safe async output, and isolated cleanup |
| Confidence | Missing from normalized results | Per-field confidence and review thresholds |
| Fallback | Replaces the whole route after errors | Fills missing fields and records conflicting alternatives |
| Provenance | One source string | Source and raw service key per field |
| Human review | Not represented | Explicit `needs_review` result with reasons |
| Tests | None | Pure response fixtures plus pipeline tests |

## Provider policy

Choose the profile after measuring a representative, approved document set.
Do not make an LLM classify layout variability when the workflow or source
system already knows the document family.

- **Textract Expense or ID** is a strong primary adapter when the specialized
  API matches the document family.
- **Textract Queries** is useful for stable fields on forms without writing a
  service-specific parser.
- **Bedrock Data Automation** is appropriate for schema-driven extraction,
  document splitting, and varied layouts. Synchronous invocation is restricted
  to image input and requires an `IMAGE` blueprint. Use a separate `DOCUMENT`
  blueprint and asynchronous project for PDFs and durable workflows.
- **Bedrock multimodal structured output** is an optional gap-filler or
  semantic extractor. It should not silently overwrite an existing
  authoritative field.

## Result contract

Every processed document returns:

- `profile` and `profile_version`: the selected versioned document profile;
- `status`: `accepted` or `needs_review`;
- `fields`: canonical field names with value, source, confidence, and raw key;
- `alternatives`: conflicting candidates retained for audit or review;
- `providers_attempted` and `timings_ms`;
- `warnings` and deterministic review reasons;
- provider-scoped raw text, tables, and metadata.

Profiles may also bind allowed Regions, S3 buckets and prefixes, and a total
processing deadline. Async BDA uses a caller request ID for idempotency and an
isolated output prefix. Successful async jobs return a `job_metadata.json`
manifest; the adapter follows only custom and standard output paths that remain
inside that invocation's requested prefix. Ephemeral cleanup runs after success,
service failure, polling failure, or timeout and removes current objects,
versions, and delete markers under the isolated prefix.

The contract deliberately separates extraction from downstream business logic.
Customer-specific validation can consume this envelope without changing AWS
adapters.

Confidence is not treated as a universal probability. Textract and BDA expose
different confidence dimensions, so thresholds are configured per field and
provider. An unscored model value can be made review-required without inventing
a numeric score. In particular, BDA blueprint-match confidence stays in
provider metadata and is never copied onto individual fields. Field confidence
and geometry are joined from BDA `explainability_info` onto the corresponding
`inference_result` value.

Adapters may perform conservative representation normalization before contract
validation. The Textract Expense adapter removes currency symbols and thousands
separators from unambiguous numeric values and converts unambiguous common date
formats to ISO 8601. Ambiguous dates remain unchanged and therefore go to
review.

## Production evolution

The repository is a local starter and contract reference. A production
implementation should add:

- S3 event or API intake with malware/type/size validation;
- Step Functions or event-driven asynchronous orchestration;
- idempotency and a durable job/result store;
- least-privilege IAM, customer-managed encryption where required, and
  retention/lifecycle controls;
- structured metrics without logging document contents;
- a customer-owned review queue and correction capture;
- an evaluation dataset with field-level accuracy, review rate, latency, and
  cost measurements;
- deployment-specific data-residency review, especially for cross-Region model
  inference.
