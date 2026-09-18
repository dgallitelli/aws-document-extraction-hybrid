# Operations and Security Boundary

The library runs under the caller's AWS credentials. Application-level source
checks are defense in depth; IAM remains the authorization boundary.

## Input isolation

Production profiles should set:

```json
{
  "allowed_regions": ["us-east-1"],
  "allowed_source_buckets": ["tenant-idp-input"],
  "allowed_source_prefixes": ["tenant-a/incoming/"]
}
```

Bind each authenticated tenant to a server-selected profile and S3 prefix.
Never accept arbitrary profile paths, bucket names, or object keys directly
from an untrusted HTTP client.

The primary runtime and all retained top-level compatibility wrappers apply
the same Region, bucket, and prefix checks before constructing provider
clients.

The runtime role should allow only the required object prefix and provider
operations. A deployment-specific policy should use explicit bucket, KMS key,
BDA project/blueprint, model/inference profile, and output-prefix resources
instead of wildcard resources where the service supports resource scoping.
Async cleanup also requires `s3:GetBucketVersioning` so it can remove versions
and delete markers when the output bucket is versioned.

## Data handling

- The CLI omits raw text, tables, and provider metadata by default. Use
  `--include-raw` only in an approved environment. Library and compatibility
  serialization follows the same opt-in rule.
- Do not log document bodies, raw provider output, or extracted field values.
- Configure S3 encryption, Block Public Access, access logging where required,
  and lifecycle expiration for temporary BDA output.
- The async BDA adapter accepts a caller request ID, hashes it into a stable
  `clientToken`, and uses the same token as the output prefix. Reuse a request
  ID only for retries of the same logical input.
- Set `cleanup_output: true` for ephemeral validation when outputs do not need
  audit retention. Otherwise use an explicit lifecycle rule and deletion
  runbook. The adapter validates the status URI and every path referenced by
  the BDA job manifest. Cleanup refuses empty prefixes and deletes the complete
  isolated job prefix rather than only `job_metadata.json`. Cleanup also runs
  after service errors and timeouts and removes object versions and delete
  markers when bucket versioning is enabled or suspended.

## Size, type, and deadlines

The Bedrock adapter performs `HeadObject`, applies separate document and image
byte limits, verifies PDF and common image signatures, and checks image
dimensions before inference. Production intake should additionally scan for
malware, validate encrypted/malformed documents, and enforce page limits before
orchestration.

Profiles can set `deadline_seconds`. The pipeline passes the remaining budget
to provider SDK read timeouts and BDA polling, stops before starting another
provider when the budget is exhausted, and returns a review reason.

## Model safety and residency

Structured output constrains shape, not factual grounding. Bedrock-derived
fields are review-required by default. A profile may disable that only when an
independent validation or grounding policy justifies it.

Set `allowed_regions` and review every model or inference-profile identifier.
Cross-Region and global inference can process data outside the caller Region;
the repository cannot infer a customer's residency policy from a model ID.

The live harness defaults to the US Claude Haiku 4.5 inference profile because
it accepts PDF document blocks and Bedrock structured output. Do not substitute
a model based only on text/image support; verify PDF document support for the
exact inference-profile ID.

## Human review

`needs_review` is a service-neutral boundary. The application should send the
result, evidence, and document reference to a tenant-isolated review system.
Do not build new workflows around Amazon A2I availability assumptions; keep the
workforce and correction store behind an adapter.
