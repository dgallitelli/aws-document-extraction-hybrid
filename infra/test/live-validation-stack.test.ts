import test from "node:test";
import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { LiveValidationStack } from "../lib/live-validation-stack.js";

function synthesizedTemplate(): Template {
  const app = new App();
  const stack = new LiveValidationStack(app, "TestStack", {
    env: { account: "111122223333", region: "us-east-1" },
  });
  return Template.fromStack(stack);
}

test("validation bucket is private, encrypted, TLS-only, and disposable", () => {
  const template = synthesizedTemplate();

  template.hasResourceProperties("AWS::S3::Bucket", {
    BucketEncryption: {
      ServerSideEncryptionConfiguration: [
        {
          ServerSideEncryptionByDefault: {
            SSEAlgorithm: "AES256",
          },
        },
      ],
    },
    PublicAccessBlockConfiguration: {
      BlockPublicAcls: true,
      BlockPublicPolicy: true,
      IgnorePublicAcls: true,
      RestrictPublicBuckets: true,
    },
  });

  template.hasResourceProperties("AWS::S3::BucketPolicy", {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: "s3:*",
          Condition: {
            Bool: {
              "aws:SecureTransport": "false",
            },
          },
          Effect: "Deny",
        }),
      ]),
    },
  });
  template.resourceCountIs("AWS::Lambda::Function", 0);
  template.resourceCountIs("AWS::IAM::Role", 0);
  template.resourceCountIs("AWS::Logs::LogGroup", 0);
});

test("stack owns modality-specific blueprints and sync plus async projects", () => {
  const template = synthesizedTemplate();

  template.resourceCountIs("AWS::Bedrock::Blueprint", 2);
  template.hasResourceProperties("AWS::Bedrock::Blueprint", {
    BlueprintName: "aws-idp-starter-live-document-invoice",
    Type: "DOCUMENT",
  });
  template.hasResourceProperties("AWS::Bedrock::Blueprint", {
    BlueprintName: "aws-idp-starter-live-image-invoice",
    Type: "IMAGE",
  });
  template.resourceCountIs("AWS::Bedrock::DataAutomationProject", 2);
  template.hasResourceProperties("AWS::Bedrock::DataAutomationProject", {
    OverrideConfiguration: {
      ModalityRouting: {
        jpeg: "IMAGE",
        png: "IMAGE",
      },
    },
    ProjectType: "SYNC",
    StandardOutputConfiguration: {
      Image: Match.anyValue(),
    },
  });
  template.hasResourceProperties("AWS::Bedrock::DataAutomationProject", {
    ProjectType: "ASYNC",
    StandardOutputConfiguration: {
      Document: Match.anyValue(),
    },
  });
});
