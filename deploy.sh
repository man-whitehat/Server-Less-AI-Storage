#!/usr/bin/env bash
# =============================================================================
# Local Deployment Script for AI Document Analyzer
# Executes locally using your configured AWS CLI profile credentials and AWS CDK.
# Zero Docker dependency required.
# =============================================================================

set -e

# Load environment variables if .env exists
if [ -f .env ]; then
  echo "--> Loading environment configuration from .env..."
  set -a
  source .env
  set +a
fi

AWS_PROFILE="${AWS_PROFILE:-default}"
AWS_REGION="${AWS_REGION:-us-east-1}"
BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-amazon.nova-micro-v1:0}"
BUDGET_LIMIT="${BUDGET_LIMIT:-10}"
ALERT_EMAIL="${ALERT_EMAIL:-admin@example.com}"

echo "================================================================="
echo "  AI Document Analyzer: Production Local Deployment Pipeline     "
echo "================================================================="
echo "AWS Profile:        $AWS_PROFILE"
echo "AWS Region:         $AWS_REGION"
echo "Bedrock Model:      $BEDROCK_MODEL_ID (Ultra-low cost)"
echo "Monthly Budget Cap: \$$BUDGET_LIMIT USD"
echo "Alert Email:        $ALERT_EMAIL"
echo "================================================================="

# 1. Verify Prerequisites
echo "--> Step 1/6: Checking local tool dependencies..."
command -v aws >/dev/null 2>&1 || { echo "ERROR: AWS CLI is not installed or not in PATH."; exit 1; }
command -v node >/dev/null 2>&1 || { echo "ERROR: Node.js is not installed or not in PATH."; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "ERROR: npm is not installed or not in PATH."; exit 1; }

# 2. Verify AWS CLI Credentials
echo "--> Step 2/6: Validating AWS credentials with STS..."
CALLER_IDENTITY=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --region "$AWS_REGION" --output json 2>&1) || {
  echo "ERROR: Failed to validate AWS credentials for profile '$AWS_PROFILE'."
  echo "Please run 'aws configure --profile $AWS_PROFILE' to set valid credentials."
  echo "$CALLER_IDENTITY"
  exit 1
}

ACCOUNT_ID=$(echo "$CALLER_IDENTITY" | grep -o '"Account": "[^"]*' | cut -d'"' -f4)
IAM_ARN=$(echo "$CALLER_IDENTITY" | grep -o '"Arn": "[^"]*' | cut -d'"' -f4)
echo "Authenticated successfully as:"
echo "  Account ID: $ACCOUNT_ID"
echo "  IAM ARN:    $IAM_ARN"

# 3. Bedrock Model Access Notice
echo "--> Step 3/6: Checking Amazon Bedrock configuration..."
echo "  Target Model: $BEDROCK_MODEL_ID"
echo "  NOTE: Ensure '$BEDROCK_MODEL_ID' is enabled in your AWS Bedrock Console"
echo "  (AWS Console -> Amazon Bedrock -> Model access -> Enable '$BEDROCK_MODEL_ID')"

# 4. Install Node Dependencies if needed
if [ ! -d "node_modules" ]; then
  echo "--> Step 4/6: Installing project dependencies..."
  npm install
else
  echo "--> Step 4/6: Node dependencies already present."
fi

# 5. CDK Synthesize
echo "--> Step 5/6: Synthesizing CloudFormation template (cdk synth)..."
export AWS_PROFILE="$AWS_PROFILE"
export AWS_REGION="$AWS_REGION"
npx cdk synth

# 6. CDK Bootstrap (if required) & Deploy
echo "--> Step 6/6: Deploying CDK stack (cdk deploy)..."
npx cdk deploy --require-approval never \
  --profile "$AWS_PROFILE" \
  --outputs-file cdk-outputs.json

echo ""
echo "================================================================="
echo "  Deployment Complete! Application is Live on AWS.              "
echo "================================================================="
if [ -f cdk-outputs.json ]; then
  cat cdk-outputs.json
fi
echo ""
echo "You can test the deployment using the cURL commands documented in README.md."
