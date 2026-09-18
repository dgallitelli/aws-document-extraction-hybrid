import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
  Tags,
  aws_bedrock as bedrock,
  aws_s3 as s3,
} from "aws-cdk-lib";
import { Construct } from "constructs";
import {
  documentInvoiceBlueprintSchema,
  imageInvoiceBlueprintSchema,
} from "./invoice-blueprint-schema.js";

export class LiveValidationStack extends Stack {
  constructor(scope: Construct, id: string, props: StackProps) {
    super(scope, id, props);

    const validationBucket = new s3.Bucket(this, "ValidationBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      lifecycleRules: [
        {
          abortIncompleteMultipartUploadAfter: Duration.days(1),
          expiration: Duration.days(1),
        },
      ],
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const documentBlueprint = new bedrock.CfnBlueprint(
      this,
      "DocumentInvoiceBlueprint",
      {
        blueprintName: "aws-idp-starter-live-document-invoice",
        schema: documentInvoiceBlueprintSchema,
        type: "DOCUMENT",
      },
    );
    const imageBlueprint = new bedrock.CfnBlueprint(
      this,
      "ImageInvoiceBlueprint",
      {
        blueprintName: "aws-idp-starter-live-image-invoice",
        schema: imageInvoiceBlueprintSchema,
        type: "IMAGE",
      },
    );

    const documentBlueprintConfiguration = {
      blueprints: [
        {
          blueprintArn: documentBlueprint.attrBlueprintArn,
          blueprintStage: documentBlueprint.attrBlueprintStage,
        },
      ],
    };
    const imageBlueprintConfiguration = {
      blueprints: [
        {
          blueprintArn: imageBlueprint.attrBlueprintArn,
          blueprintStage: imageBlueprint.attrBlueprintStage,
        },
      ],
    };
    const documentStandardOutputConfiguration = {
      document: {
        extraction: {
          boundingBox: { state: "ENABLED" },
          granularity: { types: ["DOCUMENT"] },
        },
        generativeField: { state: "ENABLED" },
        outputFormat: {
          additionalFileFormat: { state: "DISABLED" },
          textFormat: { types: ["MARKDOWN"] },
        },
      },
    };
    const imageStandardOutputConfiguration = {
      image: {
        extraction: {
          boundingBox: { state: "ENABLED" },
          category: {
            state: "ENABLED",
            types: ["CONTENT_MODERATION"],
          },
        },
        generativeField: {
          state: "ENABLED",
          types: ["IMAGE_SUMMARY"],
        },
      },
    };

    const syncProject = new bedrock.CfnDataAutomationProject(
      this,
      "SyncInvoiceProject",
      {
        customOutputConfiguration: imageBlueprintConfiguration,
        projectDescription:
          "Disposable synchronous project for AWS IDP starter validation",
        projectName: "aws-idp-starter-live-sync",
        projectType: "SYNC",
        overrideConfiguration: {
          modalityRouting: {
            jpeg: "IMAGE",
            png: "IMAGE",
          },
        },
        standardOutputConfiguration: imageStandardOutputConfiguration,
      },
    );

    const asyncProject = new bedrock.CfnDataAutomationProject(
      this,
      "AsyncInvoiceProject",
      {
        customOutputConfiguration: documentBlueprintConfiguration,
        projectDescription:
          "Disposable asynchronous project for AWS IDP starter validation",
        projectName: "aws-idp-starter-live-async",
        projectType: "ASYNC",
        standardOutputConfiguration: documentStandardOutputConfiguration,
      },
    );

    Tags.of(this).add("Application", "aws-idp-starter");
    Tags.of(this).add("Environment", "live-validation");
    Tags.of(this).add("ManagedBy", "aws-cdk");

    new CfnOutput(this, "ValidationBucketName", {
      value: validationBucket.bucketName,
    });
    new CfnOutput(this, "BdaDocumentBlueprintArn", {
      value: documentBlueprint.attrBlueprintArn,
    });
    new CfnOutput(this, "BdaImageBlueprintArn", {
      value: imageBlueprint.attrBlueprintArn,
    });
    new CfnOutput(this, "BdaSyncProjectArn", {
      value: syncProject.attrProjectArn,
    });
    new CfnOutput(this, "BdaSyncProjectStage", {
      value: syncProject.attrProjectStage,
    });
    new CfnOutput(this, "BdaAsyncProjectArn", {
      value: asyncProject.attrProjectArn,
    });
    new CfnOutput(this, "BdaAsyncProjectStage", {
      value: asyncProject.attrProjectStage,
    });
  }
}
