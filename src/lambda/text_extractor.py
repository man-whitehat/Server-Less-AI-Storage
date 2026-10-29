"""
Robust, zero-external-dependency text extraction utility for AWS Lambda.
Supports TXT, JSON, CSV, Markdown, Logs, and PDF documents.
Uses standard library (zlib, re) with optional fallback to pypdf if available.
"""

import io
import json
import logging
import os
import re
import sys
import zlib
from typing import Optional

logger = logging.getLogger(__name__)

# Ensure vendor directory is accessible
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
vendor_dir = os.path.join(parent_dir, "vendor")
if os.path.exists(vendor_dir) and vendor_dir not in sys.path:
    sys.path.insert(0, vendor_dir)


def is_clean_human_text(line: str) -> bool:
    """
    Filters out corrupt font glyph encoding artifacts and random symbol noise
    common in PDFs with unmapped character encodings.
    """
    line = line.strip()
    if len(line) < 3:
        return False
    # Drop lines with sequences of 4+ consecutive special characters (#$%&*, etc.)
    if re.search(r"[\#\$\%\&\'\*\+\/\<\=\>\?\@\[\\\]\^\_\`\{\|\}\~]{3,}", line):
        return False
    # Check ratio of alphanumeric characters and standard sentence punctuation
    valid_chars = sum(1 for c in line if c.isalnum() or c in " .,-:;()'\"")
    ratio = valid_chars / len(line)
    return ratio >= 0.72


def extract_text_from_pdf_stream(pdf_bytes: bytes) -> str:
    """
    Extracts text content from PDF bytes using pypdf if present,
    or a pure-Python regex + zlib decompressor fallback (zero-dependency).
    """
    # 1. Try pypdf if installed
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            clean_lines = [l.strip() for l in text.splitlines() if is_clean_human_text(l)]
            if clean_lines:
                pages_text.append(f"--- Page {i + 1} ---\n" + "\n".join(clean_lines))
        if pages_text:
            return "\n\n".join(pages_text)
    except ImportError:
        logger.info("pypdf not installed in environment; using pure-Python stream extractor.")
    except Exception as err:
        logger.warning(f"pypdf extraction failed, falling back to stream extractor: {err}")

    # 2. Pure-Python fallback using stream scanning and FlateDecode (zlib)
    # Extracts text from PDF stream objects: (string) Tj or [(string) ...] TJ
    extracted_chunks = []
    stream_pattern = re.compile(rb"stream[\r\n]+(.*?)[\r\n]+endstream", re.DOTALL)

    for match in stream_pattern.finditer(pdf_bytes):
        stream_data = match.group(1)
        decompressed_data = None

        # Try zlib decompression (FlateDecode)
        try:
            decompressed_data = zlib.decompress(stream_data)
        except Exception:
            # Might be raw uncompressed stream
            decompressed_data = stream_data

        if not decompressed_data:
            continue

        try:
            # Look for PDF text show operators: (text) Tj or [(text)] TJ
            # Decode using Latin-1 / UTF-8 safe mapping
            stream_str = decompressed_data.decode("latin-1", errors="ignore")

            # Extract literal strings inside parentheses: (Some text)
            # Match ( ... ) followed by Tj or inside TJ arrays
            tj_matches = re.findall(r"\((.*?)\)\s*T[jJ]", stream_str)
            for m in tj_matches:
                cleaned = m.replace(r"\)", ")").replace(r"\(", "(").replace(r"\n", " ").strip()
                if cleaned and is_clean_human_text(cleaned):
                    extracted_chunks.append(cleaned)

            # Also match array of strings: [ (Hello) -10 (World) ] TJ
            array_matches = re.findall(r"\[(.*?)\]\s*TJ", stream_str)
            for arr in array_matches:
                parts = re.findall(r"\((.*?)\)", arr)
                joined = "".join(parts).replace(r"\)", ")").replace(r"\(", "(").strip()
                if joined and is_clean_human_text(joined):
                    extracted_chunks.append(joined)
        except Exception as err:
            logger.debug(f"Stream parsing chunk skipped: {err}")

    if extracted_chunks:
        # Join extracted pieces into readable sentences/paragraphs
        full_text = " ".join(extracted_chunks)
        # Normalize repeated spaces
        return re.sub(r"\s+", " ", full_text).strip()

    return "[PDF content detected, but text could not be extracted directly from streams. May contain scanned bitmap images.]"


def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    """
    Extracts readable text from file bytes based on file extension and content sniffing.
    Supports .txt, .json, .csv, .md, .log, and .pdf.
    """
    lower_name = filename.lower()

    # 1. PDF files
    if lower_name.endswith(".pdf") or file_bytes.startswith(b"%PDF-"):
        return extract_text_from_pdf_stream(file_bytes)

    # 2. JSON files
    if lower_name.endswith(".json"):
        try:
            text = file_bytes.decode("utf-8")
            data = json.loads(text)
            # Format nicely for LLM comprehension
            return json.dumps(data, indent=2)
        except Exception:
            # Fall back to raw text decode
            pass

    # 3. Plain text / CSV / Markdown / Logs
    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
    for enc in encodings:
        try:
            return file_bytes.decode(enc)
        except UnicodeDecodeError:
            continue

    # Fallback with replacement characters
    return file_bytes.decode("utf-8", errors="replace")
