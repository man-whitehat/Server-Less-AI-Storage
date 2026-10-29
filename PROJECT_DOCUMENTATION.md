# AI Document Analyzer - Complete Project Documentation

## 1. What this project is

AI Document Analyzer (the frontend calls it **DocParser**) is a serverless AWS application that turns uploaded documents into structured intelligence.

The application:

1. Accepts a document through a browser, CLI, cURL, or another HTTP client.
2. Requests a short-lived Amazon S3 presigned upload URL.
3. Uploads the file directly to S3, avoiding API Gateway and Lambda payload limits.
4. Detects the upload with an S3 `ObjectCreated` event.
5. Extracts text from TXT, JSON, CSV, Markdown, log, and PDF files.
6. Sends the text to Amazon Bedrock through the Converse API.
7. Validates the model response against a strict JSON shape.
8. Stores searchable fields in DynamoDB and the full structured result in S3.
9. Deletes the raw source file after successful processing.
10. Exposes the result through HTTP API endpoints and the web dashboard.

The design favors low idle cost, short-lived raw data, direct-to-S3 uploads, and a small set of AWS managed services.

## 2. Current implementation at a glance

| Area | Implementation |
|---|---|
| Infrastructure as code | AWS CDK v2 in TypeScript |
| Runtime | Python 3.12 for Lambda; Node.js/TypeScript for CDK |
| AI | Amazon Bedrock Converse API |
| Default model | `amazon.nova-micro-v1:0` in the application defaults; deployed output may use a `us.` inference profile |
| Alternative model | Anthropic Claude 3 Haiku |
| HTTP layer | API Gateway HTTP API v2 |
| File storage | Two private S3 buckets |
| Indexed results | DynamoDB on-demand table |
| Processing | S3 event -> processor Lambda |
| Browser UI | Static HTML, JavaScript, CSS; no frontend build step |
| Local frontend server | Python standard-library HTTP server |
| Tests | Python `unittest`, including mocked end-to-end tests |
| Cost control | AWS Budget with forecasted and actual email alerts |

## 3. Architecture and data flow

```text
Browser / CLI / cURL
        |
        | GET /health
        | POST /upload-url
        v
API Gateway HTTP API
        |
        v
API Lambda ----------------------> DynamoDB (read insights/list)
        |
        | presigned PUT URL
        v
Client --------------------------> Raw S3 bucket
                                      |
                                      | ObjectCreated for raw/*
                                      v
                               Processor Lambda
                                      |
                         +------------+-------------+
                         |                          |
                         v                          v
                   Text extraction            Bedrock Converse
                         |                          |
                         +------------+-------------+
                                      |
                         +------------+-------------+
                         |                          |
                         v                          v
                Processed S3 JSON             DynamoDB record
                                      |
                                      v
                              Delete raw S3 object
```

### Normal successful request

1. The client calls `POST /upload-url` with a filename and content type.
2. The API Lambda creates a UUID and key:
   `raw/{document_id}/{filename}`.
3. The API Lambda returns a presigned S3 `PUT` URL valid for 900 seconds.
4. The client uploads the bytes with HTTP `PUT` and the same content type.
5. S3 invokes the processor Lambda for keys under the `raw/` prefix.
6. The processor parses the document ID and filename from the S3 key.
7. The processor downloads the object and extracts readable text.
8. The processor creates a bounded Bedrock prompt. Input is capped at 32,000 characters.
9. Bedrock returns a JSON extraction, which the parser cleans and validates.
10. The full result is written to `insights/{document_id}.json` in the processed bucket.
11. A flattened query record is written to DynamoDB.
12. The raw S3 object is deleted.
13. The client polls `GET /insights/{document_id}` until the record is `COMPLETED` or `FAILED`.

### Processing failure

If extraction, Bedrock, parsing, or persistence fails:

- The processor logs the exception.
- A `FAILED` record is attempted in DynamoDB with the error message truncated to 1,000 characters.
- The raw object is not deleted by the failure path, so the one-day raw bucket lifecycle rule can clean it up.
- The API returns a normal query response once the failure record is available.

## 4. AWS resources created by the CDK stack

The main stack is [lib/ai-document-analyzer-stack.ts](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/lib/ai-document-analyzer-stack.ts).

### 4.1 Raw uploads S3 bucket

Logical resource: `RawUploadsBucket`

- S3-managed encryption.
- Public access completely blocked.
- SSL enforced.
- CORS allows browser `PUT` uploads and related read/head operations.
- Raw objects use the `raw/` prefix.
- Objects expire after one day.
- Incomplete multipart uploads are aborted after one day.
- CDK removal policy is `DESTROY` with auto-delete enabled.
- The API Lambda can write to it.
- The processor Lambda can read and delete from it.

The immediate delete after successful processing is the primary cleanup mechanism. The lifecycle rule is the backstop for failed, abandoned, or orphaned uploads.

### 4.2 Processed insights S3 bucket

Logical resource: `ProcessedInsightsBucket`

- S3-managed encryption.
- Public access completely blocked.
- SSL enforced.
- Contains persistent JSON results at `insights/{document_id}.json`.
- CDK removal policy is `DESTROY` with auto-delete enabled.
- Only the processor Lambda is granted write access.

### 4.3 DynamoDB table

Logical resource: `DocumentInsightsTable`

- Partition key: `document_id` (string).
- Billing: `PAY_PER_REQUEST`.
- Point-in-time recovery is disabled in the current stack.
- CDK removal policy is `DESTROY`.
- The processor writes records.
- The API Lambda reads individual records and scans recent records.

The `/insights` implementation currently uses a DynamoDB `Scan`, limited to 50 items, then sorts the returned page by `created_at` in memory. It is suitable for a small project but should be replaced with a time-based index/query strategy for large datasets.

### 4.4 Processor Lambda

Logical resource: `DocumentProcessorFunction`

- Runtime: Python 3.12.
- Handler: `lambda/handler.lambda_handler`.
- Source asset: `src/`.
- Memory: 512 MB.
- Timeout: 60 seconds.
- Environment variables:
  - `PROCESSED_BUCKET_NAME`
  - `DYNAMODB_TABLE_NAME`
  - `BEDROCK_MODEL_ID`
  - `LOG_LEVEL`
- Permissions:
  - Read and delete raw S3 objects.
  - Write processed S3 objects.
  - Write DynamoDB records.
  - Invoke the configured Bedrock model/inference profiles.

### 4.5 API Lambda

Logical resource: `ApiGatewayFunction`

- Runtime: Python 3.12.
- Handler: `lambda/api_handler.lambda_handler`.
- Source asset: `src/`.
- Memory: 256 MB.
- Timeout: 15 seconds.
- Environment variables:
  - `RAW_BUCKET_NAME`
  - `DYNAMODB_TABLE_NAME`
  - `BEDROCK_MODEL_ID`
  - `LOG_LEVEL`
- Permissions:
  - Put objects into the raw S3 bucket.
  - Read from DynamoDB.

### 4.6 S3 event notification

The raw bucket sends `OBJECT_CREATED` notifications to the processor Lambda, scoped to the `raw/` prefix. Folder markers and zero-byte objects are skipped by the processor.

### 4.7 API Gateway HTTP API

Logical resource: `DocumentAnalyzerHttpApi`

- API type: HTTP API v2.
- CORS allows all origins, headers, and the configured GET/POST/OPTIONS methods.
- All four routes use the API Lambda integration.

### 4.8 Budget alert

The [budget-alert construct](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/lib/constructs/budget-alert.ts) creates a monthly AWS cost budget:

- Default limit: USD 10.
- Forecasted alert: 80 percent of the configured limit.
- Actual-spend alert: 100 percent of the configured limit.
- Delivery: email through AWS Budgets.

This is an alert, not a hard service shutdown. It does not prevent AWS charges or automatically disable the application.

## 5. HTTP API reference

The API base URL is emitted as the CDK output `ApiEndpoint`.

### `GET /health`

Returns configuration and connectivity information from the API Lambda.

Example response:

```json
{
  "status": "healthy",
  "service": "ai-document-analyzer",
  "model_id": "amazon.nova-micro-v1:0",
  "raw_bucket": "raw-bucket-name",
  "table": "table-name"
}
```

This endpoint confirms that the API Lambda is running. It does not invoke Bedrock or verify every downstream permission.

### `POST /upload-url`

Request:

```json
{
  "filename": "invoice.pdf",
  "content_type": "application/pdf"
}
```

Behavior:

- Uses the basename of the filename, replacing spaces with underscores.
- Generates a UUID document ID.
- Creates an S3 key under `raw/{document_id}/`.
- Generates a 15-minute presigned `PUT` URL.

Response fields:

```json
{
  "document_id": "uuid",
  "filename": "invoice.pdf",
  "s3_key": "raw/uuid/invoice.pdf",
  "upload_url": "https://...",
  "expires_in_seconds": 900,
  "method": "PUT",
  "curl_example": "curl -X PUT ..."
}
```

The client must upload using `PUT` and the returned `Content-Type`.

### `GET /insights/{id}`

Returns one DynamoDB record by `document_id`.

Possible outcomes:

- `200`: record exists, normally `COMPLETED` or `FAILED`.
- `404`: the document is still processing, the ID is invalid, or the record was not written.
- `500`: table configuration or database failure.

Successful result fields commonly include:

```json
{
  "document_id": "uuid",
  "filename": "invoice.pdf",
  "status": "COMPLETED",
  "summary": "Concise factual summary.",
  "classification": "INVOICE",
  "action_items": ["Pay the invoice by the due date."],
  "key_entities": [
    {
      "name": "Example Corp",
      "category": "ORGANIZATION",
      "context": "Invoice vendor"
    }
  ],
  "processed_s3_uri": "s3://processed-bucket/insights/uuid.json",
  "input_tokens": 400,
  "output_tokens": 120,
  "model_id": "amazon.nova-micro-v1:0",
  "created_at": "2026-01-01T00:00:00+00:00",
  "updated_at": "2026-01-01T00:00:00+00:00",
  "raw_file_deleted": true
}
```

### `GET /insights`

Returns recent records:

```json
{
  "count": 1,
  "documents": []
}
```

The optional `limit` query parameter is clamped from 1 through 50. Invalid values retain the default of 20.

### CORS and unsupported routes

`OPTIONS` requests return `200`. Unknown routes return `404` with an error message. API responses use JSON and include permissive CORS headers.

## 6. AI extraction contract

The prompt and parser live in [src/prompts/extraction_prompt.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/src/prompts/extraction_prompt.py).

The model is instructed to return only this shape:

```json
{
  "summary": "A concise, factual 2 to 4 sentence summary.",
  "action_items": [
    "An actionable task, deadline, or requirement."
  ],
  "key_entities": [
    {
      "name": "Exact name from the document",
      "category": "ORGANIZATION",
      "context": "Short relevance explanation"
    }
  ],
  "classification": "INVOICE"
}
```

Allowed classification examples include `INVOICE`, `CONTRACT`, `TECHNICAL_SPEC`, `MEETING_NOTES`, `FINANCIAL_REPORT`, `RESUME`, `POLICY`, `EMAIL`, `MEMO`, and `OTHER`.

Allowed entity categories include `ORGANIZATION`, `PERSON`, `DATE`, `CURRENCY`, `LOCATION`, `PRODUCT`, `REGULATION`, `METRIC`, and `OTHER`.

The parser:

1. Removes surrounding whitespace.
2. Removes accidental Markdown code fences.
3. Finds the outermost JSON object.
4. Parses JSON.
5. Requires `summary`, `action_items`, `key_entities`, and `classification`.
6. Normalizes incorrect top-level types where possible.
7. Raises `ValueError` for malformed JSON or missing required keys.

The user prompt includes the filename and source length. Document content is capped at 32,000 characters; longer content is truncated and marked in the prompt.

## 7. Text extraction behavior

The implementation is in [src/lambda/text_extractor.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/src/lambda/text_extractor.py).

Supported input behavior:

- PDF: uses vendored `pypdf` when available.
- PDF fallback: scans PDF streams, attempts zlib decompression, and extracts common `Tj`/`TJ` text operators.
- JSON: decodes and pretty-prints valid JSON.
- TXT, CSV, Markdown, and logs: tries UTF-8, UTF-8 with BOM, Latin-1, and CP1252.
- Unknown extensions: uses the same text decoding fallback.
- Undecodable bytes: UTF-8 replacement characters are used.

PDF lines are filtered for likely font-encoding noise. Scanned image-only PDFs may produce a message indicating that text could not be extracted; OCR is not implemented.

The project vendors `pypdf` under `src/vendor/` so the Lambda asset does not need a Docker build or a network install during deployment.

## 8. Bedrock invocation and fallback

The processor uses `bedrock-runtime.converse` with:

- Temperature: `0.1`.
- Maximum output tokens: `1200`.
- One user message containing the document prompt.
- One system message containing the strict extraction rules.

The processor records input tokens, output tokens, and elapsed latency in the result.

If Bedrock returns the specific validation error indicating model access is not enabled, the application uses `extract_heuristic_insights()` instead. The fallback derives a summary, action items, classification, dates, academic years, programs, departments, course codes, emails, and capitalized phrases from the actual document text. It is a temporary content-aware fallback, not equivalent to a general-purpose language model.

Other Bedrock errors are re-raised and recorded as processing failures.

## 9. Repository map

### Infrastructure and configuration

- [bin/app.ts](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/bin/app.ts): CDK application entrypoint; loads dotenv, selects account/region, and passes model/budget/email settings to the stack.
- [lib/ai-document-analyzer-stack.ts](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/lib/ai-document-analyzer-stack.ts): all AWS resources, permissions, triggers, routes, and outputs.
- [lib/constructs/budget-alert.ts](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/lib/constructs/budget-alert.ts): reusable AWS Budgets construct.
- [cdk.json](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/cdk.json): CDK app command and feature flags.
- [package.json](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/package.json): Node dependencies and CDK scripts.
- [tsconfig.json](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/tsconfig.json): strict TypeScript compiler settings.

### Lambda and Python logic

- [src/lambda/api_handler.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/src/lambda/api_handler.py): API Gateway routing, presigned URL generation, health response, and DynamoDB reads.
- [src/lambda/handler.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/src/lambda/handler.py): S3-triggered processing orchestration, Bedrock calls, persistence, cleanup, and failure records.
- [src/lambda/text_extractor.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/src/lambda/text_extractor.py): file-to-text conversion.
- [src/prompts/extraction_prompt.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/src/prompts/extraction_prompt.py): system prompt, user prompt construction, and response validation.
- [src/lambda/requirements.txt](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/src/lambda/requirements.txt): Python dependency reference for Lambda-related code.

### Frontend and tools

- [frontend/index.html](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/frontend/index.html): single-page DocParser layout, controls, dialogs, and content regions.
- [frontend/app.js](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/frontend/app.js): upload flow, polling, history, API settings, sample documents, visual/raw result views, and health polling.
- [frontend/styles.css](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/frontend/styles.css): application-specific visual styling layered over Tailwind CDN classes.
- [run_frontend.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/run_frontend.py): local static server on the first free port from 3000 through 3049; reads the deployed endpoint from `cdk-outputs.json`.
- [test_client.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/test_client.py): dependency-free command-line API client.

### Samples and tests

- [test_samples/sample_contract.txt](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/test_samples/sample_contract.txt): contract sample.
- [test_samples/sample_meeting_notes.txt](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/test_samples/sample_meeting_notes.txt): meeting notes sample.
- [test_samples/sample_quarterly_report.pdf](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/test_samples/sample_quarterly_report.pdf): PDF extraction sample.
- [test_samples/generate_sample_pdf.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/test_samples/generate_sample_pdf.py): sample PDF generator.
- [tests/unit/test_prompt_and_extractor.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/tests/unit/test_prompt_and_extractor.py): prompt/parser and TXT/JSON/PDF tests.
- [tests/unit/test_end_to_end_mock.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/tests/unit/test_end_to_end_mock.py): mocked full pipeline test with no AWS calls.

### Generated and local-only files

- `.env.example`: safe configuration template.
- `.env`: local configuration; do not commit credentials or secrets.
- `cdk-outputs.json`: deployment outputs consumed by the frontend launcher and test client.
- `cdk.out/`: synthesized CDK assembly.
- `node_modules/`, Python caches, and other build/cache directories: generated dependencies or local runtime artifacts.

## 10. Environment configuration

The template is [.env.example](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `AWS_PROFILE` | `default` | AWS CLI profile used by deployment scripts |
| `AWS_REGION` | `us-east-1` | Deployment and runtime region |
| `AWS_ACCOUNT_ID` | unset | Optional account override if CDK cannot infer it |
| `BEDROCK_MODEL_ID` | `amazon.nova-micro-v1:0` | Bedrock model or inference profile |
| `BUDGET_LIMIT` | `10` | Monthly AWS Budget amount in USD |
| `ALERT_EMAIL` | placeholder | Budget alert destination |
| `LOG_LEVEL` | `INFO` | Lambda logging level |

Copy the template before deploying:

```powershell
Copy-Item .env.example .env
```

Do not place AWS access keys in source files or commit `.env`.

## 11. Prerequisites

- Node.js 18 or newer.
- npm.
- Python 3.12 or newer.
- AWS CLI v2.
- An AWS profile with permission to deploy the CDK stack.
- CDK bootstrap completed in the target account and region when required.
- Bedrock model access enabled for the selected model/inference profile.

The deployment scripts use `aws`, `node`, `npm`, and the CDK CLI. Docker is not required by the current asset layout.

## 12. Installation, build, and deployment

### Install dependencies

```powershell
npm install
```

### Compile TypeScript

```powershell
npm run build
```

### Synthesize CloudFormation

```powershell
npx cdk synth
```

### First deployment in an account/region

```powershell
npx cdk bootstrap
```

### Deploy

The repository includes both [deploy.ps1](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/deploy.ps1) for PowerShell and [deploy.sh](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/deploy.sh) for Bash/Git Bash.

On Windows:

```powershell
.\deploy.ps1
```

The script loads `.env`, validates AWS STS credentials, checks the configured model notice, installs dependencies when needed, synthesizes the stack, and deploys with `--require-approval never`. It writes outputs to `cdk-outputs.json`.

Manual deployment:

```powershell
$env:AWS_PROFILE = "default"
$env:AWS_REGION = "us-east-1"
npx cdk deploy --require-approval never --profile $env:AWS_PROFILE --outputs-file cdk-outputs.json
```

### Destroy

This deletes the CDK-managed resources and their data:

```powershell
npx cdk destroy --force
```

Because both buckets and the DynamoDB table use `DESTROY`, this operation is destructive. Export or back up important processed results first.

## 13. Running the web dashboard

After deployment:

```powershell
python run_frontend.py
```

or:

```powershell
npm run frontend
```

The launcher:

- Serves the `frontend/` directory from localhost.
- Selects an available port starting at 3000.
- Reads `ApiEndpoint` from `cdk-outputs.json`.
- Opens the dashboard in the default browser.

The dashboard supports drag-and-drop or file-picker uploads, sample loaders, pipeline status, history search, result inspection, raw JSON display, copy buttons, health polling, and an API endpoint settings dialog. It stores a manually configured endpoint in browser `localStorage` under `docparser_api_url`.

The frontend loads Tailwind CSS, Lucide icons, and fonts from CDNs. An offline or restricted network may therefore affect the visual appearance even though the local server still runs.

## 14. CLI usage

[test_client.py](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/test_client.py) uses only Python's standard library:

```powershell
python test_client.py health
python test_client.py upload test_samples\sample_contract.txt
python test_client.py upload test_samples\sample_quarterly_report.pdf
python test_client.py get <document-id>
python test_client.py list --limit 10
```

The client discovers the API URL in this order:

1. `API_URL` environment variable.
2. `cdk-outputs.json`.
3. `API_URL` in `.env`.

The upload command requests a presigned URL, performs the S3 `PUT`, then polls for up to 45 seconds at three-second intervals.

## 15. Testing

The tests are intentionally runnable without AWS credentials and without network calls:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

The suite covers:

- Required extraction prompt fields and strict-output instructions.
- User prompt construction and document truncation behavior.
- Clean JSON parsing.
- Markdown-fenced JSON parsing.
- Invalid JSON and missing-key errors.
- Plain-text extraction.
- JSON formatting.
- Real sample PDF extraction.
- Presigned URL API behavior.
- S3 event processing with mocked S3, Bedrock, and DynamoDB.
- Processed S3 writes, DynamoDB writes, raw-file deletion, and API queries.

TypeScript validation:

```powershell
npm run build
```

The existing repository also contains generated caches and `node_modules`; these are not required to understand the source behavior.

## 16. Security and privacy behavior

Positive controls already implemented:

- S3 public access is blocked.
- S3 requires SSL.
- S3 buckets use server-side S3-managed encryption.
- Browser uploads use short-lived presigned URLs rather than public bucket access.
- The processor has separate read/delete/write permissions rather than broad S3 administration.
- Bedrock permissions are limited to listed foundation-model and inference-profile ARNs.
- Raw documents are deleted after successful processing and expire after one day as a backstop.
- API and S3 traffic is not routed through a custom credential-bearing frontend.

Important current limitations:

- API Gateway routes are public and have no Cognito/JWT authorizer.
- CORS is intentionally permissive (`*`).
- There is no per-user tenant isolation or ownership check.
- The health response exposes bucket and table names.
- DynamoDB records contain extracted document content and may contain sensitive entities.
- AWS-managed S3 keys are used; customer-managed KMS keys are not configured.
- The budget is an alert only.
- Model output is validated structurally, not fact-checked against the source.
- The fallback heuristic may infer content and should be treated as lower-confidence output.

For production use with sensitive or multi-user data, add authentication and authorization, restrictive CORS, request/file validation, rate limiting, malware scanning, KMS key policies, tenant-aware keys, audit logging, retention policy review, and stronger output validation.

## 17. Cost behavior

The low-cost design comes from:

- Nova Micro by default.
- DynamoDB on-demand billing.
- HTTP API rather than a higher-cost API type.
- Direct S3 uploads.
- Small Lambda memory/time settings.
- Immediate raw-file deletion.
- A one-day raw-file lifecycle rule.

Actual cost depends on region, document size, model pricing, request volume, retries, storage, and AWS account conditions. The cost estimates in the original README are estimates, not a billing guarantee. Monitor AWS Cost Explorer and the configured budget.

## 18. Troubleshooting

### `cdk synth` cannot determine account or region

Configure an AWS profile and region, then verify:

```powershell
aws sts get-caller-identity --profile default
$env:AWS_REGION = "us-east-1"
```

### Bedrock says operation is not allowed

Enable the selected model in the Bedrock console for the target region. The processor has a content-aware heuristic fallback for one specific model-access validation error, but production operation should use an explicitly approved model.

### Upload succeeds but insight remains 404

Check CloudWatch logs for the processor Lambda. Common causes are:

- S3 event delivery or prefix mismatch.
- Missing Bedrock access.
- Incorrect Lambda IAM permissions.
- Text extraction failure.
- DynamoDB write failure.
- A still-running asynchronous invocation.

### Upload URL works but browser upload fails

Ensure the browser sends the same `Content-Type` used when requesting the URL. Check S3 CORS and that the URL has not expired.

### PDF result is empty or low quality

The extractor handles text PDFs but does not perform OCR on scanned image PDFs. Try a text-based PDF or add an OCR stage such as Amazon Textract.

### Frontend shows the wrong API

Open Settings and save the correct API Gateway URL, or remove/reset the `docparser_api_url` browser local-storage value. The launcher normally reads the latest `cdk-outputs.json`.

### `GET /insights` returns unexpected ordering

The current implementation scans a limited DynamoDB page and sorts only that page. It is not a globally ordered query. Add a GSI and query by a designed time/tenant key for production-scale history.

## 19. Known design limitations and recommended next steps

1. Add Cognito or another JWT authorizer before exposing the API to untrusted users.
2. Restrict CORS to the deployed frontend origin.
3. Validate file size, extension, MIME type, and content before processing.
4. Add idempotency protection for duplicate S3 notifications.
5. Use DynamoDB GSIs instead of `Scan` for history.
6. Add a queue or workflow for retries and longer documents.
7. Add OCR for scanned PDFs and image documents.
8. Store a processing status record before or immediately after upload if clients need explicit `PROCESSING` state.
9. Add structured CloudWatch metrics and alarms for failures, latency, throttling, and budget.
10. Use customer-managed KMS keys where regulatory requirements demand them.
11. Replace permissive public API behavior with authenticated, tenant-scoped access.
12. Add integration tests against a controlled AWS test account.
13. Review the model ARN policy whenever changing model IDs or inference profiles.
14. Consider retaining only the minimum extracted data required by the business.

## 20. Operational checklist

Before deployment:

- Confirm the AWS profile and region.
- Confirm Bedrock model access.
- Review `.env` values and ensure no secrets are committed.
- Run the Python tests.
- Run `npm run build`.
- Run `npx cdk synth`.

After deployment:

- Save `cdk-outputs.json`.
- Call `/health`.
- Upload a small TXT sample.
- Verify the processor result in `/insights/{id}`.
- Verify the raw S3 object is deleted after success.
- Confirm a processed JSON object exists.
- Confirm the budget email subscription is active if AWS requires confirmation.
- Review CloudWatch logs.

Before teardown:

- Export any required processed JSON.
- Confirm the stack and buckets belong to this project.
- Run `npx cdk destroy --force`.
- Verify that no unrelated resources remain and review Cost Explorer for residual charges.

## 21. Source of truth

This document describes the source currently present in the workspace. When behavior changes, update this file together with the affected code and the shorter [README.md](C:/Users/Manas%20Mishra/OneDrive/Desktop/AWS%20Project/README.md).
