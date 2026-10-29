"""
HTTP API Handler for API Gateway.
Provides endpoints for:
1. POST /upload-url: Generates S3 presigned PUT URL for direct client upload
2. GET /insights/{id}: Fetches extracted insights for a specific document
3. GET /insights: Lists recent document insights
4. GET /health: Healthcheck endpoint
"""

import json
import logging
import os
import uuid
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

RAW_BUCKET_NAME = os.environ.get("RAW_BUCKET_NAME")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")

# Configure S3 client with s3v4 signature for secure presigned URLs
REGION_NAME = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
s3_client = boto3.client("s3", region_name=REGION_NAME, config=Config(signature_version="s3v4"))
dynamodb_resource = boto3.resource("dynamodb", region_name=REGION_NAME)

def get_raw_bucket() -> str:
    return os.environ.get("RAW_BUCKET_NAME", "")

def get_bedrock_model_id() -> str:
    return os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")

def get_insights_table():
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    return dynamodb_resource.Table(table_name) if table_name else None


class DecimalEncoder(json.JSONEncoder):
    """Helper to convert DynamoDB Decimal types to standard float/int for JSON."""
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super(DecimalEncoder, self).default(o)


def build_response(status_code: int, body: dict) -> dict:
    """Builds standard HTTP response with CORS headers."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
        },
        "body": json.dumps(body, cls=DecimalEncoder),
    }


def handle_get_health():
    """Health check endpoint."""
    return build_response(200, {
        "status": "healthy",
        "service": "ai-document-analyzer",
        "model_id": get_bedrock_model_id(),
        "raw_bucket": get_raw_bucket(),
        "table": os.environ.get("DYNAMODB_TABLE_NAME", ""),
    })


def handle_generate_upload_url(body: dict):
    """
    Generates a presigned S3 PUT URL allowing client direct upload to raw-uploads-bucket.
    Prevents large payloads from traversing API Gateway / Lambda memory.
    """
    raw_bucket = get_raw_bucket()
    if not raw_bucket:
        return build_response(500, {"error": "RAW_BUCKET_NAME not configured"})

    raw_filename = body.get("filename", "document.txt")
    # Sanitize filename
    clean_filename = os.path.basename(raw_filename).replace(" ", "_")
    if not clean_filename:
        clean_filename = "document.txt"

    content_type = body.get("content_type", "application/octet-stream")
    doc_id = str(uuid.uuid4())
    s3_key = f"raw/{doc_id}/{clean_filename}"

    try:
        presigned_url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": raw_bucket,
                "Key": s3_key,
                "ContentType": content_type,
            },
            ExpiresIn=900,  # 15 minutes
        )

        return build_response(200, {
            "document_id": doc_id,
            "filename": clean_filename,
            "s3_key": s3_key,
            "upload_url": presigned_url,
            "expires_in_seconds": 900,
            "method": "PUT",
            "curl_example": f'curl -X PUT -T "{clean_filename}" -H "Content-Type: {content_type}" "{presigned_url}"',
        })
    except ClientError as err:
        logger.exception(f"Failed to generate presigned URL: {err}")
        return build_response(500, {"error": "Failed to generate presigned upload URL", "details": str(err)})


def handle_get_insight(doc_id: str):
    """Fetches insights for a single document ID from DynamoDB."""
    insights_table = get_insights_table()
    if not insights_table:
        return build_response(500, {"error": "DynamoDB table not configured"})

    try:
        response = insights_table.get_item(Key={"document_id": doc_id})
        item = response.get("Item")
        if not item:
            return build_response(404, {
                "error": "Document insights not found",
                "document_id": doc_id,
                "note": "Document may still be processing or ID is invalid.",
            })

        return build_response(200, item)
    except ClientError as err:
        logger.exception(f"Error querying DynamoDB for {doc_id}: {err}")
        return build_response(500, {"error": "Database query failed", "details": str(err)})


def handle_list_insights(query_params: dict):
    """Lists recent document insights from DynamoDB."""
    insights_table = get_insights_table()
    if not insights_table:
        return build_response(500, {"error": "DynamoDB table not configured"})

    limit = 20
    if query_params and "limit" in query_params:
        try:
            limit = min(max(1, int(query_params["limit"])), 50)
        except ValueError:
            pass

    try:
        # Scan with limit for lightweight listing
        response = insights_table.scan(Limit=limit)
        items = response.get("Items", [])
        # Sort descending by created_at if present
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return build_response(200, {
            "count": len(items),
            "documents": items,
        })
    except ClientError as err:
        logger.exception(f"Error scanning DynamoDB: {err}")
        return build_response(500, {"error": "Failed to list insights", "details": str(err)})


def lambda_handler(event, context):
    """
    Main API Gateway HTTP API router.
    Supports HTTP API (v2) and REST API formats.
    """
    logger.info(f"API Request: {event.get('requestContext', {}).get('http', {})}")

    # Determine HTTP method and path
    http_ctx = event.get("requestContext", {}).get("http", {})
    method = http_ctx.get("method") or event.get("httpMethod", "GET")
    raw_path = http_ctx.get("path") or event.get("path", "/")

    # Normalize path (remove trailing slash except root)
    path = raw_path.rstrip("/") if raw_path != "/" else "/"

    # Handle CORS Preflight
    if method == "OPTIONS":
        return build_response(200, {"message": "OK"})

    # Health check
    if path in ["/health", ""]:
        return handle_get_health()

    # Route: POST /upload-url
    if path == "/upload-url" and method == "POST":
        body = {}
        if event.get("body"):
            try:
                body = json.loads(event["body"])
            except json.JSONDecodeError:
                body = {}
        return handle_generate_upload_url(body)

    # Route: GET /insights
    if path == "/insights" and method == "GET":
        query_params = event.get("queryStringParameters") or {}
        return handle_list_insights(query_params)

    # Route: GET /insights/{id}
    path_parameters = event.get("pathParameters") or {}
    if "id" in path_parameters and method == "GET":
        return handle_get_insight(path_parameters["id"])

    # Fallback path parameter matching if pathParameters is empty
    if path.startswith("/insights/") and method == "GET":
        doc_id = path.split("/insights/")[1]
        return handle_get_insight(doc_id)

    return build_response(404, {"error": f"Endpoint not found: {method} {raw_path}"})
