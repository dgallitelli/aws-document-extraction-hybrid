# Disposable Live AWS Validation

This procedure validates the starter against real AWS services while keeping
test infrastructure isolated and removable. It is not a production deployment.

## What the stack creates

`AwsIdpStarterLiveValidation` contains:

- one generated-name S3 bucket with SSE-S3, Block Public Access, TLS-only
  access, one-day expiration, incomplete multipart cleanup, and a destroy
  removal policy;
- one `DOCUMENT` BDA invoice blueprint and asynchronous project for PDFs;
- one `IMAGE` BDA invoice blueprint and synchronous project for images.

BDA currently requires a non-null standard-output configuration even when a
project uses custom output. Synchronous projects do not accept document
blueprints, so the two modalities must remain separate.

The S3 bucket deliberately omits versioning, Object Lock, replication, and
access logging because it is disposable validation storage. Production
engagements must make their own retention, audit, and recovery decisions.

## Prerequisites

- An authenticated AWS CLI profile with permissions for CloudFormation, CDK
  bootstrap assets, S3, Textract, Bedrock, and Bedrock Data Automation.
- A bootstrapped target account and Region.
- Python dependencies installed in a virtual environment outside the
  repository.

Pin the Region explicitly. An ambient `AWS_REGION` overrides the profile's
configured Region. This disposable BDA stack supports `us-east-1` and
`us-west-2`:

```bash
export AWS_PROFILE=default
export AWS_REGION="$(aws configure get region --profile "$AWS_PROFILE")"
export AWS_DEFAULT_REGION="$AWS_REGION"
```

## Deploy

```bash
cd infra
npm ci
npm test
npx cdk synth AwsIdpStarterLiveValidation \
  --profile "$AWS_PROFILE" \
  --quiet
npx cdk diff AwsIdpStarterLiveValidation \
  --profile "$AWS_PROFILE" \
  --strict
npx cdk deploy AwsIdpStarterLiveValidation \
  --profile "$AWS_PROFILE" \
  --require-approval never
cd ..
```

Inspect the diff before deployment. The expected resources are one bucket, two
blueprints, and two projects. The stack intentionally has no Lambda function,
IAM role, or log group.

## Run

```bash
python scripts/live_validation.py \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

The default model is
`us.anthropic.claude-haiku-4-5-20251001-v1:0`. Override it only with a
Converse model that supports both PDF document blocks and structured output:

```bash
python scripts/live_validation.py \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --model-id your-inference-profile-id
```

`live-validation-report.json` is gitignored. It contains service names, field
names, counts, timings, exact-value validation status, pipeline status, and S3
security settings. It omits document text and field values. The harness creates
both a PNG and PDF from a built-in synthetic invoice, validates known values,
and removes both inputs even when a check fails.

## Verified result

On 2026-09-18, the stack was deployed with the `default` profile in
`us-east-1` and tested with the generated synthetic invoice:

| Check | Result |
|---|---|
| Textract Expense | Passed; 6 canonical fields and 4 known core values |
| Bedrock structured output | Passed; 7 canonical fields and all 7 known values |
| BDA synchronous image | Passed; 7 canonical fields and all 7 known values |
| BDA asynchronous PDF | Passed; 7 canonical fields and 4 known core values |
| Textract to Bedrock fallback | Passed; both providers attempted, 2 known values, result review-required |
| Temporary storage | SSE-S3 and all four Block Public Access settings enabled |
| TLS-only bucket policy | Passed; insecure transport is explicitly denied |
| BDA async cleanup | Passed; no per-job output objects remained |
| Input cleanup | Passed; uploaded PNG and PDF were removed |
| Post-deploy CDK diff | No differences |

This proves the live adapters can recover known values from a deterministic
fixture. It is not representative-dataset accuracy evidence. Production
readiness still requires approved ground truth and precision, recall,
review-rate, latency, and residency evaluation.

After validation, the stack was destroyed and audited. CloudFormation reported
the stack absent; the validation bucket, both BDA projects, and both blueprints
were absent; and no stack-named Lambda function, IAM role, or CloudWatch log
group remained. The shared `CDKToolkit` bootstrap stack was left intact.

The live test exposed and fixed two contract details that mocked tests had
missed:

1. async BDA status returns a `job_metadata.json` manifest, not an output
   directory;
2. cleanup must delete the requested job prefix, including manifest, custom
   output, standard output, and any object versions or delete markers.

## Destroy and audit

```bash
bucket="$(aws cloudformation describe-stacks \
  --stack-name AwsIdpStarterLiveValidation \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ValidationBucketName'].OutputValue" \
  --output text)"
aws s3 rm "s3://$bucket" \
  --recursive \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

cd infra
npx cdk destroy AwsIdpStarterLiveValidation \
  --profile "$AWS_PROFILE" \
  --force
cd ..

aws cloudformation describe-stacks \
  --stack-name AwsIdpStarterLiveValidation \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

aws bedrock-data-automation list-data-automation-projects \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

aws bedrock-data-automation list-blueprints \
  --blueprint-stage-filter ALL \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

The stack lookup must return not found. The BDA lists must not contain either
`aws-idp-starter-live-*` project or blueprint. Do not delete the shared
`CDKToolkit` stack as part of this teardown.

The bucket is emptied explicitly before destroy. This avoids a CDK
bucket-auto-delete Lambda, its IAM role, and an orphanable Lambda log group.
