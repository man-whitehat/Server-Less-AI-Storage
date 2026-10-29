"""
Unit tests for AI Document Analyzer prompts and text extraction logic.
Can be executed locally without AWS or Docker dependencies.
"""

import json
import os
import sys
import unittest

# Ensure src is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src/lambda")))

from prompts.extraction_prompt import (
    SYSTEM_EXTRACTION_PROMPT,
    build_user_prompt,
    parse_and_validate_extraction,
)
from text_extractor import extract_text_from_file


class TestExtractionPrompt(unittest.TestCase):
    def test_system_prompt_rules(self):
        """Ensure system prompt contains strict instructions and schema."""
        self.assertIn("summary", SYSTEM_EXTRACTION_PROMPT)
        self.assertIn("action_items", SYSTEM_EXTRACTION_PROMPT)
        self.assertIn("key_entities", SYSTEM_EXTRACTION_PROMPT)
        self.assertIn("classification", SYSTEM_EXTRACTION_PROMPT)
        self.assertIn("Return ONLY a single, valid JSON object", SYSTEM_EXTRACTION_PROMPT)

    def test_build_user_prompt(self):
        """Verify prompt construction with truncation safeguard."""
        sample_text = "This is a test invoice from Acme Corp for $500."
        prompt = build_user_prompt(sample_text, filename="invoice_001.txt")
        self.assertIn("invoice_001.txt", prompt)
        self.assertIn(sample_text, prompt)
        self.assertIn("=== DOCUMENT CONTENT START ===", prompt)

    def test_parse_valid_clean_json(self):
        """Verify parsing of clean JSON response."""
        sample_json = json.dumps({
            "summary": "Acme Corp signed an enterprise SLA agreement.",
            "action_items": ["Review security Annex B before Friday"],
            "key_entities": [{"name": "Acme Corp", "category": "ORGANIZATION", "context": "Client"}],
            "classification": "CONTRACT"
        })
        parsed = parse_and_validate_extraction(sample_json)
        self.assertEqual(parsed["classification"], "CONTRACT")
        self.assertEqual(len(parsed["action_items"]), 1)
        self.assertEqual(len(parsed["key_entities"]), 1)

    def test_parse_json_wrapped_in_markdown_fences(self):
        """Verify parser strips ```json code fences if returned by LLM."""
        wrapped = """```json
{
  "summary": "Quarterly earnings report showed a 14% revenue increase.",
  "action_items": [],
  "key_entities": [
    {"name": "Q3 2026", "category": "DATE", "context": "Reporting quarter"}
  ],
  "classification": "FINANCIAL_REPORT"
}
```"""
        parsed = parse_and_validate_extraction(wrapped)
        self.assertEqual(parsed["classification"], "FINANCIAL_REPORT")
        self.assertEqual(parsed["action_items"], [])

    def test_parse_invalid_json_raises_error(self):
        """Ensure malformed JSON raises ValueError."""
        invalid_text = "Here is your summary: { not valid json ..."
        with self.assertRaises(ValueError):
            parse_and_validate_extraction(invalid_text)

    def test_missing_mandatory_keys_raises_error(self):
        """Ensure JSON missing mandatory keys raises ValueError."""
        incomplete_json = json.dumps({"summary": "Incomplete data"})
        with self.assertRaises(ValueError):
            parse_and_validate_extraction(incomplete_json)


class TestTextExtractor(unittest.TestCase):
    def test_extract_plain_text(self):
        """Verify decoding of plain text."""
        raw_bytes = "Quarterly Business Review - October 2026".encode("utf-8")
        extracted = extract_text_from_file(raw_bytes, "qbr.txt")
        self.assertEqual(extracted, "Quarterly Business Review - October 2026")

    def test_extract_json_file(self):
        """Verify formatting of raw JSON file content."""
        raw_json_bytes = b'{"customer": "Globex Corp", "total_amount": 12500}'
        extracted = extract_text_from_file(raw_json_bytes, "order.json")
        self.assertIn("Globex Corp", extracted)
        self.assertIn("12500", extracted)

    def test_extract_pdf_file(self):
        """Verify extraction from real PDF document."""
        pdf_path = os.path.join(os.path.dirname(__file__), "../../test_samples/sample_quarterly_report.pdf")
        if os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
            extracted = extract_text_from_file(pdf_bytes, "sample_quarterly_report.pdf")
            self.assertIn("QUARTERLY FINANCIAL PERFORMANCE REPORT", extracted)
            self.assertIn("Global Logistics Enterprise Inc.", extracted)
            self.assertIn("Mandatory Action Items", extracted)


if __name__ == "__main__":
    unittest.main()
