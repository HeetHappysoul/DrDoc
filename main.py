import io
import json
import os
from typing import List, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from google import genai
from google.genai import types
from pydantic import BaseModel
from pypdf import PdfReader
import time

load_dotenv()

app = FastAPI(title="DrDoc Extraction Service")

# Initialize Gemini Client
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


class InvoiceData(BaseModel):
    bill_no: Optional[str] = None
    date: Optional[str] = None
    bill_to: Optional[str] = None
    sold_by: Optional[str] = None
    total_amount: Optional[float] = None


class ReviewItem(BaseModel):
    bill_no: Optional[str] = None
    document_name: str
    flagged_field: str
    extracted_value: Optional[str] = None
    reason: str


class ExtractionResponse(BaseModel):
    status: str
    data: Optional[InvoiceData] = None
    review_queue: List[ReviewItem] = []


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    pdf_file = io.BytesIO(pdf_bytes)
    reader = PdfReader(pdf_file)
    extracted_text = ""
    for page_num, page in enumerate(reader.pages):
        page_text = page.extract_text() or ""
        extracted_text += f"\n--- Page {page_num + 1} ---\n" + page_text
    return extracted_text.strip()


def call_llm_extractor(raw_text: str, max_retries: int = 3) -> dict:
    prompt = f"""You are an automated invoice parsing engine.
Extract the following fields from the invoice text below:
- bill_no (string or null): The invoice/bill number or reference ID.
- date (string or null): The invoice date.
- bill_to (string or null): The customer or recipient entity.
- sold_by (string or null): The seller or vendor entity.
- total_amount (number or null): The final total amount as a numeric float.

Strict rules:
1. Return ONLY a valid JSON object matching these keys.
2. If any field is missing, unclear, or ambiguous, return null for that field. Do not invent or hallucinate data.

Invoice Text:
{raw_text}
"""

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            # Retry on 503 capacity spikes with incremental backoff (2s, 4s)
            if "503" in str(e) and attempt < max_retries:
                time.sleep(2 * attempt)
                continue
            raise e

@app.post("/extract", response_model=ExtractionResponse)
async def extract_invoice(file: UploadFile = File(...)):
    file_bytes = await file.read()
    raw_text = extract_text_from_pdf(file_bytes)

    if not raw_text:
        return ExtractionResponse(
            status="error",
            data=None,
            review_queue=[
                ReviewItem(
                    document_name=file.filename,
                    flagged_field="document_body",
                    reason="No readable digital text found in PDF (scanned image or empty file).",
                )
            ],
        )

    # 1. Call LLM with error handling for malformed JSON
    try:
        extracted_dict = call_llm_extractor(raw_text)
    except json.JSONDecodeError:
        return ExtractionResponse(
            status="error",
            data=None,
            review_queue=[
                ReviewItem(
                    document_name=file.filename,
                    flagged_field="llm_output",
                    reason="LLM response was not valid JSON.",
                )
            ],
        )
    except Exception as e:
        return ExtractionResponse(
            status="error",
            data=None,
            review_queue=[
                ReviewItem(
                    document_name=file.filename,
                    flagged_field="llm_service",
                    reason=f"LLM extraction call failed: {str(e)}",
                )
            ],
        )

    # 2. Map data and populate review queue for missing fields
    review_queue: List[ReviewItem] = []
    bill_no = extracted_dict.get("bill_no")

    expected_fields = ["bill_no", "date", "bill_to", "sold_by", "total_amount"]
    for field in expected_fields:
        val = extracted_dict.get(field)
        if val is None:
            review_queue.append(
                ReviewItem(
                    bill_no=bill_no,
                    document_name=file.filename,
                    flagged_field=field,
                    extracted_value=None,
                    reason=f"Field '{field}' could not be extracted with certainty.",
                )
            )

    invoice_data = InvoiceData(
        bill_no=bill_no,
        date=extracted_dict.get("date"),
        bill_to=extracted_dict.get("bill_to"),
        sold_by=extracted_dict.get("sold_by"),
        total_amount=extracted_dict.get("total_amount"),
    )

    return ExtractionResponse(
        status="success",
        data=invoice_data,
        review_queue=review_queue,
    )