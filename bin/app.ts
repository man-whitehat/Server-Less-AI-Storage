#!/usr/bin/env node
import "dotenv/config";
import * as cdk from "aws-cdk-lib";
import { AiDocumentAnalyzerStack } from "../lib/ai-document-analyzer-stack";

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT || process.env.AWS_ACCOUNT_ID,
  region: process.env.CDK_DEFAULT_REGION || process.env.AWS_REGION || "us-east-1",
};

new AiDocumentAnalyzerStack(app, "AiDocumentAnalyzerStack", {
  env,
  bedrockModelId: process.env.BEDROCK_MODEL_ID || "amazon.nova-micro-v1:0",
  budgetLimit: process.env.BUDGET_LIMIT ? parseFloat(process.env.BUDGET_LIMIT) : 10,
  alertEmail: process.env.ALERT_EMAIL || "admin@example.com",
  description:
    "Production-Ready AI Document Analyzer Stack: Bedrock (Nova Micro/Haiku), S3 (1-day retention), DynamoDB On-Demand, Lambda, API Gateway v2, and $10 Budget Alert",
});

app.synth();
