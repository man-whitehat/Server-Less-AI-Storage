<#
.SYNOPSIS
    Local PowerShell Deployment Script for AI Document Analyzer.
    Synthesizes and deploys AWS CDK stack using local AWS credentials and Bedrock.
    Zero Docker dependency required.
#>

$ErrorActionPreference = "Stop"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  AI Document Analyzer: Local PowerShell Deployment Pipeline     " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# 1. Load .env if present
if (Test-Path ".env") {
    Write-Host "--> Loading environment configuration from .env..." -ForegroundColor Yellow
    Get-Content ".env" | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $name, $value = $line.Split("=", 2)
            if ($name -and $value) {
                [System.Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
            }
        }
    }
}

$awsProfile = if ($env:AWS_PROFILE) { $env:AWS_PROFILE } else { "default" }
$awsRegion = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$modelId = if ($env:BEDROCK_MODEL_ID) { $env:BEDROCK_MODEL_ID } else { "us.amazon.nova-micro-v1:0" }
$budgetLimit = if ($env:BUDGET_LIMIT) { $env:BUDGET_LIMIT } else { "10" }

Write-Host "AWS Profile:        $awsProfile"
Write-Host "AWS Region:         $awsRegion"
Write-Host "Bedrock Model:      $modelId (Ultra-low cost)"
Write-Host "Monthly Budget Cap: `$$budgetLimit USD"
Write-Host "================================================================="

# 2. Check local tools
Write-Host "--> Step 1/5: Verifying local tool dependencies..." -ForegroundColor Yellow
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Write-Error "AWS CLI is not installed or not in PATH. Please install AWS CLI v2."
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error "Node.js is not installed or not in PATH."
}

# 3. Verify AWS credentials
Write-Host "--> Step 2/5: Validating AWS credentials with STS..." -ForegroundColor Yellow
try {
    $identityJson = aws sts get-caller-identity --profile $awsProfile --region $awsRegion --output json
    $identity = $identityJson | ConvertFrom-Json
    Write-Host "Authenticated successfully as:" -ForegroundColor Green
    Write-Host "  Account ID: $($identity.Account)"
    Write-Host "  IAM ARN:    $($identity.Arn)"
} catch {
    Write-Error "Failed to validate AWS credentials for profile '$awsProfile'. Run 'aws configure' first."
}

# 4. Check dependencies
if (-not (Test-Path "node_modules")) {
    Write-Host "--> Step 3/5: Installing npm dependencies..." -ForegroundColor Yellow
    & npm.cmd install
} else {
    Write-Host "--> Step 3/5: Node dependencies already present." -ForegroundColor Green
}

# 5. CDK Synth
Write-Host "--> Step 4/5: Synthesizing CloudFormation templates..." -ForegroundColor Yellow
$env:AWS_PROFILE = $awsProfile
$env:AWS_REGION = $awsRegion
& npx.cmd cdk synth

# 6. CDK Deploy
Write-Host "--> Step 5/5: Deploying stack to AWS..." -ForegroundColor Yellow
& npx.cmd cdk deploy --require-approval never --profile $awsProfile --outputs-file cdk-outputs.json

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host "  Deployment Succeeded! Application is Live on AWS.              " -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
if (Test-Path "cdk-outputs.json") {
    Get-Content "cdk-outputs.json"
}
