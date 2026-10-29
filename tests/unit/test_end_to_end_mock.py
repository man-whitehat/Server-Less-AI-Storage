"""
End-to-end local workflow simulation test using unittest.mock.
Simulates:
1. API Gateway generating presigned S3 upload URL
2. S3 ObjectCreated trigger invoking Processor Lambda
3. Bedrock Converse AI extraction with strict JSON parsing
4. DynamoDB and S3 persistent storage
5. Immediate deletion of raw source file
6. Querying insights via API Gateway

Runs completely locally with zero AWS credentials or network calls required.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure paths are added
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src/lambda")))

import handler
import api_handler


class TestEndToEndLocalPipeline(unittest.TestCase):
    def setUp(self):
        # Configure test environment variables
        os.environ["RAW_BUCKET_NAME"] = "test-raw-uploads-bucket"
        os.environ["PROCESSED_BUCKET_NAME"] = "test-processed-insights-bucket"
        os.environ["DYNAMODB_TABLE_NAME"] = "test-document-insights-table"
        os.environ["BEDROCK_MODEL_ID"] = "amazon.nova-micro-v1:0"

    @patch("api_handler.s3_client")
    def test_api_upload_url_generation(self, mock_s3):
        """Test API Gateway endpoint generating presigned upload URL."""
        mock_s3.generate_presigned_url.return_value = "https://s3.amazonaws.com/test-bucket/raw/123/sample.txt?sig=xyz"

        event = {
            "requestContext": {"http": {"method": "POST", "path": "/upload-url"}},
            "body": json.dumps({"filename": "invoice_october.txt", "content_type": "text/plain"}),
        }

        response = api_handler.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 200)

        body = json.loads(response["body"])
        self.assertIn("upload_url", body)
        self.assertIn("document_id", body)
        self.assertIn("s3_key", body)
        self.assertTrue(body["s3_key"].startswith("raw/"))
        self.assertTrue(body["s3_key"].endswith("invoice_october.txt"))

    @patch("handler.s3_client")
    @patch("handler.bedrock_client")
    @patch("handler.get_insights_table")
    def test_processor_full_pipeline(self, mock_get_table, mock_bedrock, mock_s3):
        """Test S3 event processor Lambda extracting text, calling Bedrock, saving data, and deleting raw file."""
        mock_dynamo_table = MagicMock()
        mock_get_table.return_value = mock_dynamo_table

        sample_doc_content = b"""ACME CORPORATION INVOICE
Invoice #: INV-2026-991
Total Due: $1,450.00 USD
Payment Due Date: November 15, 2026
Vendor: CloudSolutions Ltd
Note: Please remit payment to our Chase bank account."""

        # 1. Mock S3 get_object returning document bytes
        mock_s3_body = MagicMock()
        mock_s3_body.read.return_value = sample_doc_content
        mock_s3.get_object.return_value = {"Body": mock_s3_body}

        # 2. Mock Bedrock Converse API response with valid strict JSON
        mock_bedrock_json = {
            "summary": "Invoice INV-2026-991 from CloudSolutions Ltd for $1,450.00 USD due on November 15, 2026.",
            "action_items": ["Remit payment of $1,450.00 to CloudSolutions Ltd by November 15, 2026"],
            "key_entities": [
                {"name": "CloudSolutions Ltd", "category": "ORGANIZATION", "context": "Vendor"},
                {"name": "$1,450.00 USD", "category": "CURRENCY", "context": "Total Due"},
                {"name": "November 15, 2026", "category": "DATE", "context": "Due Date"}
            ],
            "classification": "INVOICE"
        }

        mock_bedrock.converse.return_value = {
            "output": {
                "message": {
                    "content": [{"text": json.dumps(mock_bedrock_json)}]
                }
            },
            "usage": {
                "inputTokens": 140,
                "outputTokens": 85
            }
        }

        # 3. Simulate S3 ObjectCreated event
        doc_id = "test-doc-uuid-999"
        raw_key = f"raw/{doc_id}/invoice.txt"
        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "test-raw-uploads-bucket"},
                        "object": {"key": raw_key, "size": len(sample_doc_content)},
                    }
                }
            ]
        }

        # Execute Lambda handler
        response = handler.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 200)

        # 4. Verify S3 get_object was called to read raw file
        mock_s3.get_object.assert_called_once_with(
            Bucket="test-raw-uploads-bucket",
            Key=raw_key
        )

        # 5. Verify Bedrock Converse was called with the document text
        mock_bedrock.converse.assert_called_once()
        call_kwargs = mock_bedrock.converse.call_args[1]
        self.assertEqual(call_kwargs["modelId"], "amazon.nova-micro-v1:0")
        prompt_content = call_kwargs["messages"][0]["content"][0]["text"]
        self.assertIn("INV-2026-991", prompt_content)

        # 6. Verify processed JSON was saved to processed S3 bucket
        mock_s3.put_object.assert_called_once()
        put_kwargs = mock_s3.put_object.call_args[1]
        self.assertEqual(put_kwargs["Bucket"], "test-processed-insights-bucket")
        self.assertEqual(put_kwargs["Key"], f"insights/{doc_id}.json")

        # 7. Verify DynamoDB put_item was called
        mock_dynamo_table.put_item.assert_called_once()
        db_item = mock_dynamo_table.put_item.call_args[1]["Item"]
        self.assertEqual(db_item["document_id"], doc_id)
        self.assertEqual(db_item["status"], "COMPLETED")
        self.assertEqual(db_item["classification"], "INVOICE")
        self.assertEqual(db_item["raw_file_deleted"], True)

        # 8. CRITICAL: Verify source raw file was immediately deleted from raw S3 bucket!
        mock_s3.delete_object.assert_called_once_with(
            Bucket="test-raw-uploads-bucket",
            Key=raw_key
        )

    @patch("api_handler.get_insights_table")
    def test_api_query_insights(self, mock_get_table):
        """Test API Gateway endpoint querying stored insights."""
        mock_dynamo_table = MagicMock()
        mock_get_table.return_value = mock_dynamo_table
        doc_id = "test-doc-uuid-999"
        mock_dynamo_table.get_item.return_value = {
            "Item": {
                "document_id": doc_id,
                "filename": "invoice.txt",
                "status": "COMPLETED",
                "summary": "Sample summary",
                "classification": "INVOICE",
                "action_items": ["Pay invoice"],
                "key_entities": []
            }
        }

        event = {
            "requestContext": {"http": {"method": "GET", "path": f"/insights/{doc_id}"}},
            "pathParameters": {"id": doc_id},
        }

        response = api_handler.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["document_id"], doc_id)
        self.assertEqual(body["classification"], "INVOICE")


if __name__ == "__main__":
    unittest.main()
