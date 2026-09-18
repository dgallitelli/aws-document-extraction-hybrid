#!/usr/bin/env node

import { App } from "aws-cdk-lib";
import { LiveValidationStack } from "../lib/live-validation-stack.js";

const account = process.env.CDK_DEFAULT_ACCOUNT;
const region = process.env.CDK_DEFAULT_REGION;

if (!account || !region) {
  throw new Error(
    "CDK_DEFAULT_ACCOUNT and CDK_DEFAULT_REGION are required; run CDK with an authenticated profile",
  );
}

const app = new App();

new LiveValidationStack(app, "AwsIdpStarterLiveValidation", {
  env: { account, region },
  description:
    "Disposable live-validation resources for the AWS IDP starter",
});
