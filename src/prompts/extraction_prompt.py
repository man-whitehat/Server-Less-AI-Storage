"""
Strict document extraction prompts and JSON parsing utilities for Amazon Bedrock.
Compatible with Amazon Nova Micro (amazon.nova-micro-v1:0) and
Anthropic Claude 3 Haiku (anthropic.claude-3-haiku-20240307-v1:0).
"""

import json
import re
from typing import Any, Dict

SYSTEM_EXTRACTION_PROMPT = """You are an automated, high-precision document intelligence extraction engine.
Your sole purpose is to analyze the provided document content and extract ONLY key information into a strict, validated JSON structure.

Output Rules:
1. Return ONLY a single, valid JSON object.
2. Do NOT include markdown code fences (no ```json or ```).
3. Do NOT include any conversational preface, greetings, notes, explanations, or post-analysis commentary.
4. The output MUST begin with the opening curly brace '{' and terminate with the closing curly brace '}'.
5. Adhere strictly to the required schema below.
6. If a specific section has no identifiable items in the document (such as action items), return an empty array [] - do not invent data.

Required JSON Schema:
{
  "summary": "A concise, factual 2 to 4 sentence executive summary of the document's core message, findings, or intent.",
  "action_items": [
    "Clear, discrete actionable task, next step, deadline, or requirement identified in the text"
  ],
  "key_entities": [
    {
      "name": "Exact entity name as found in text",
      "category": "ORGANIZATION | PERSON | DATE | CURRENCY | LOCATION | PRODUCT | REGULATION | METRIC | OTHER",
      "context": "Short 5-10 word context explaining its relevance in this document"
    }
  ],
  "classification": "INVOICE | CONTRACT | TECHNICAL_SPEC | MEETING_NOTES | FINANCIAL_REPORT | RESUME | POLICY | EMAIL | MEMO | OTHER"
}"""


def build_user_prompt(document_text: str, filename: str = "document") -> str:
    """
    Constructs the user message payload containing the document to analyze.
    Truncates or safeguards against exceeding token limits while maximizing context.
    """
    # Max ~20,000 characters is safely within Nova Micro and Claude Haiku context windows
    # while ensuring rapid inference and minimal cost.
    max_char_limit = 32000
    safe_text = document_text[:max_char_limit]
    if len(document_text) > max_char_limit:
        safe_text += "\n\n[NOTE: Document truncated for AI extraction due to length limit]"

    return f"""Analyze the following document and extract the required structured JSON according to your system instructions.

Document Metadata:
- Filename: {filename}
- Length: {len(document_text)} characters

=== DOCUMENT CONTENT START ===
{safe_text}
=== DOCUMENT CONTENT END ===

Extract key information now and output strictly the required JSON object:"""


def parse_and_validate_extraction(raw_response_text: str) -> Dict[str, Any]:
    """
    Cleans and parses the LLM output into a validated dictionary.
    Handles potential markdown code wrapping if the model inadvertently added backticks.
    Raises ValueError if JSON is malformed or missing mandatory keys.
    """
    cleaned = raw_response_text.strip()

    # Strip markdown fences if present
    if cleaned.startswith("```"):
        # Match ```json ... ``` or ``` ... ```
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        else:
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned).strip()

    # Find the outermost JSON object braces { ... }
    json_start = cleaned.find("{")
    json_end = cleaned.rfind("}")

    if json_start == -1 or json_end == -1 or json_end <= json_start:
        raise ValueError(f"No valid JSON object boundaries found in Bedrock response: {raw_response_text[:200]}...")

    json_str = cleaned[json_start : json_end + 1]

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as err:
        raise ValueError(f"Failed to parse LLM response as JSON: {err}. Raw text: {json_str[:200]}") from err

    # Validate required schema keys
    required_keys = ["summary", "action_items", "key_entities", "classification"]
    missing_keys = [k for k in required_keys if k not in data]
    if missing_keys:
        raise ValueError(f"Parsed JSON is missing required fields: {missing_keys}")

    # Ensure types
    if not isinstance(data.get("summary"), str):
        data["summary"] = str(data.get("summary", ""))

    if not isinstance(data.get("action_items"), list):
        data["action_items"] = [str(data.get("action_items"))] if data.get("action_items") else []

    if not isinstance(data.get("key_entities"), list):
        data["key_entities"] = []

    if not isinstance(data.get("classification"), str):
        data["classification"] = str(data.get("classification", "OTHER"))

    return data
