"""
Generates a pure-Python, zero-dependency sample PDF file for testing
PDF text extraction and Amazon Bedrock document intelligence.
"""

import os


def generate_test_pdf(output_path: str):
    """Creates a valid PDF-1.4 file containing structured business text."""
    stream_content = """BT
/F1 16 Tf
50 720 Td
(QUARTERLY FINANCIAL PERFORMANCE REPORT - Q3 2026) Tj
/F1 12 Tf
0 -30 Td
(Organization: Global Logistics Enterprise Inc.) Tj
0 -20 Td
(Report Date: October 18, 2026) Tj
0 -30 Td
(Executive Summary:) Tj
0 -18 Td
(Operating revenue surged 18.5 percent year-over-year to 82.4 million USD.) Tj
0 -18 Td
(Operating margin expanded by 240 basis points to 14.2 percent.) Tj
0 -30 Td
(Mandatory Action Items:) Tj
0 -18 Td
(1. Audit committee must approve revised FY2027 capital budget by November 12.) Tj
0 -18 Td
(2. Finalize European supply chain risk assessment report before November 25.) Tj
0 -18 Td
(3. Submit tax reconciliation filings to Ernst and Young by December 05.) Tj
0 -30 Td
(Authorized Signer: Michael Chang, Chief Financial Officer) Tj
ET"""

    stream_bytes = stream_content.encode("latin-1")
    stream_len = len(stream_bytes)

    objects = [
        # Obj 1: Catalog
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        # Obj 2: Pages
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        # Obj 3: Page
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        # Obj 4: Stream Contents
        f"4 0 obj\n<< /Length {stream_len} >>\nstream\n".encode("latin-1")
        + stream_bytes
        + b"\nendstream\nendobj\n",
        # Obj 5: Font
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]

    header = b"%PDF-1.4\n"
    offsets = [0]
    body = bytearray(header)

    for obj in objects:
        offsets.append(len(body))
        body.extend(obj)

    xref_offset = len(body)
    xref = f"xref\n0 {len(offsets)}\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        xref += f"{offset:010d} 00000 n \n"

    trailer = (
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    )

    body.extend(xref.encode("latin-1"))
    body.extend(trailer.encode("latin-1"))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(body)

    print(f"[+] Successfully generated sample PDF: {output_path} ({len(body)} bytes)")


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "sample_quarterly_report.pdf")
    generate_test_pdf(out)
