import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3n from "aws-cdk-lib/aws-s3-notifications";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import { HttpLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";
import { Construct } from "constructs";
import { BudgetAlertConstruct } from "./constructs/budget-alert";

export interface AiDocumentAnalyzerStackProps extends cdk.StackProps {
  /**
   * Bedrock model ID for analysis.
   * Defaults to ultra-low-cost amazon.nova-micro-v1:0
   * or anthropic.claude-3-haiku-20240307-v1:0
   */
  readonly bedrockModelId?: string;

  /**
   * Monthly budget threshold in USD to prevent cost overruns.
   * Default: $10.00
   */
  readonly budgetLimit?: number;

  /**
   * Alert email for budget notifications.
   */
  readonly alertEmail?: string;
}

export class AiDocumentAnalyzerStack extends cdk.Stack {
  public readonly rawUploadsBucket: s3.Bucket;
  public readonly processedInsightsBucket: s3.Bucket;
  public readonly documentInsightsTable: dynamodb.Table;
  public readonly processorFunction: lambda.Function;
  public readonly apiFunction: lambda.Function;
  public readonly httpApi: apigwv2.HttpApi;

  constructor(scope: Construct, id: string, props: AiDocumentAnalyzerStackProps = {}) {
    super(scope, id, props);

    const bedrockModelId =
      props.bedrockModelId ||
      process.env.BEDROCK_MODEL_ID ||
      "us.amazon.nova-micro-v1:0"; // US cross-region inference profile (works on all new accounts)

    const budgetLimit = props.budgetLimit ?? (process.env.BUDGET_LIMIT ? parseFloat(process.env.BUDGET_LIMIT) : 10);
    const alertEmail = props.alertEmail || process.env.ALERT_EMAIL || "admin@example.com";

    // =========================================================================
    // 1. Storage Layer (S3)
    // =========================================================================

    // Raw uploads bucket: 1-day auto-delete lifecycle rule minimizes S3 storage costs
    this.rawUploadsBucket = new s3.Bucket(this, "RawUploadsBucket", {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      lifecycleRules: [
        {
          id: "AutoExpireRawUploads1Day",
          enabled: true,
          expiration: cdk.Duration.days(1),
          abortIncompleteMultipartUploadAfter: cdk.Duration.days(1),
        },
      ],
      cors: [
        {
          allowedMethods: [
            s3.HttpMethods.GET,
            s3.HttpMethods.PUT,
            s3.HttpMethods.POST,
            s3.HttpMethods.HEAD,
          ],
          allowedOrigins: ["*"],
          allowedHeaders: ["*"],
          maxAge: 3600,
        },
      ],
    });

    // Processed insights bucket: Stores structured JSON extraction results
    this.processedInsightsBucket = new s3.Bucket(this, "ProcessedInsightsBucket", {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // =========================================================================
    // 2. Database Layer (DynamoDB Pay-Per-Request)
    // =========================================================================

    // On-Demand (PAY_PER_REQUEST) guarantees zero idle cost
    this.documentInsightsTable = new dynamodb.Table(this, "DocumentInsightsTable", {
      partitionKey: {
        name: "document_id",
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: false,
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // =========================================================================
    // 3. Compute Layer (AWS Lambda Python 3.12 - Docker-free Asset Bundling)
    // =========================================================================

    const lambdaSourceCode = lambda.Code.fromAsset(path.join(__dirname, "../src"));

    // Processor Lambda: Triggered by S3 upload, calls Bedrock, saves to DB & S3, deletes raw file
    this.processorFunction = new lambda.Function(this, "DocumentProcessorFunction", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda/handler.lambda_handler",
      code: lambdaSourceCode,
      memorySize: 512,
      timeout: cdk.Duration.seconds(60),
      environment: {
        PROCESSED_BUCKET_NAME: this.processedInsightsBucket.bucketName,
        DYNAMODB_TABLE_NAME: this.documentInsightsTable.tableName,
        BEDROCK_MODEL_ID: bedrockModelId,
        LOG_LEVEL: "INFO",
      },
    });

    // API Lambda: Generates presigned URLs and serves insight queries
    this.apiFunction = new lambda.Function(this, "ApiGatewayFunction", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda/api_handler.lambda_handler",
      code: lambdaSourceCode,
      memorySize: 256,
      timeout: cdk.Duration.seconds(15),
      environment: {
        RAW_BUCKET_NAME: this.rawUploadsBucket.bucketName,
        DYNAMODB_TABLE_NAME: this.documentInsightsTable.tableName,
        BEDROCK_MODEL_ID: bedrockModelId,
        LOG_LEVEL: "INFO",
      },
    });

    // =========================================================================
    // 4. IAM Roles & Least-Privilege Security
    // =========================================================================

    // Processor permissions
    this.rawUploadsBucket.grantRead(this.processorFunction);
    this.rawUploadsBucket.grantDelete(this.processorFunction); // For immediate raw file cleanup
    this.processedInsightsBucket.grantWrite(this.processorFunction);
    this.documentInsightsTable.grantWriteData(this.processorFunction);

    // Bedrock Least-Privilege policy: grant access to low-cost models
    // Must include BOTH foundation-model ARNs AND inference-profile ARNs
    // New AWS accounts require cross-region inference profiles (us.amazon.nova-micro-v1:0)
    this.processorFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "BedrockFoundationModelAccess",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock:InvokeModel",
          "bedrock:Converse",
        ],
        resources: [
          // Direct foundation model ARNs in any region (required because cross-region profile routes to us-east-1, us-east-2, us-west-2)
          "arn:aws:bedrock:*::foundation-model/amazon.nova-micro-v1:0",
          "arn:aws:bedrock:*::foundation-model/amazon.nova-lite-v1:0",
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
        ],
      })
    );

    // Separate statement for inference profiles (different ARN format - account-scoped)
    this.processorFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "BedrockInferenceProfileAccess",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock:InvokeModel",
          "bedrock:Converse",
          "bedrock:InvokeModelWithResponseStream",
        ],
        resources: [
          // US cross-region inference profile ARNs (required for new accounts)
          `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/us.amazon.nova-micro-v1:0`,
          `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/us.amazon.nova-lite-v1:0`,
          `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/us.amazon.nova-pro-v1:0`,
          // Global inference profiles
          `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/global.amazon.nova-micro-v1:0`,
          `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/global.amazon.nova-lite-v1:0`,
        ],
      })
    );

    // API Lambda permissions
    this.rawUploadsBucket.grantPut(this.apiFunction);
    this.documentInsightsTable.grantReadData(this.apiFunction);

    // =========================================================================
    // 5. Event Triggers (S3 ObjectCreated -> Processor Lambda)
    // =========================================================================

    // Scoped strictly to "raw/" prefix where presigned URLs write
    this.rawUploadsBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(this.processorFunction),
      { prefix: "raw/" }
    );

    // =========================================================================
    // 6. API Gateway (HTTP API v2 for ultra-low cost and low latency)
    // =========================================================================

    const apiIntegration = new HttpLambdaIntegration(
      "DocumentAnalyzerIntegration",
      this.apiFunction
    );

    this.httpApi = new apigwv2.HttpApi(this, "DocumentAnalyzerHttpApi", {
      apiName: "ai-document-analyzer-api",
      description: "Low-cost AI Document Analyzer HTTP API",
      corsPreflight: {
        allowHeaders: ["*"],
        allowMethods: [
          apigwv2.CorsHttpMethod.GET,
          apigwv2.CorsHttpMethod.POST,
          apigwv2.CorsHttpMethod.OPTIONS,
        ],
        allowOrigins: ["*"],
        maxAge: cdk.Duration.days(1),
      },
    });

    // Add routes
    this.httpApi.addRoutes({
      path: "/health",
      methods: [apigwv2.HttpMethod.GET],
      integration: apiIntegration,
    });

    this.httpApi.addRoutes({
      path: "/upload-url",
      methods: [apigwv2.HttpMethod.POST],
      integration: apiIntegration,
    });

    this.httpApi.addRoutes({
      path: "/insights",
      methods: [apigwv2.HttpMethod.GET],
      integration: apiIntegration,
    });

    this.httpApi.addRoutes({
      path: "/insights/{id}",
      methods: [apigwv2.HttpMethod.GET],
      integration: apiIntegration,
    });

    // =========================================================================
    // 7. Budget Alert Construct ($10 threshold guardrail)
    // =========================================================================

    new BudgetAlertConstruct(this, "DocumentAnalyzerBudgetGuardrail", {
      limitAmount: budgetLimit,
      alertEmail: alertEmail,
    });

    // =========================================================================
    // 8. Stack CloudFormation Outputs
    // =========================================================================

    new cdk.CfnOutput(this, "ApiEndpoint", {
      value: this.httpApi.apiEndpoint,
      description: "Base HTTP URL for AI Document Analyzer API",
      exportName: "AiDocumentAnalyzerApiEndpoint",
    });

    new cdk.CfnOutput(this, "RawUploadsBucketName", {
      value: this.rawUploadsBucket.bucketName,
      description: "S3 Bucket for raw incoming files (auto-deletes after 1 day)",
    });

    new cdk.CfnOutput(this, "ProcessedInsightsBucketName", {
      value: this.processedInsightsBucket.bucketName,
      description: "S3 Bucket for persistent structured AI insights",
    });

    new cdk.CfnOutput(this, "DocumentInsightsTableName", {
      value: this.documentInsightsTable.tableName,
      description: "DynamoDB Table for indexed insights (Pay-Per-Request)",
    });

    new cdk.CfnOutput(this, "ActiveBedrockModel", {
      value: bedrockModelId,
      description: "Configured Amazon Bedrock foundation model ID",
    });
  }
}
