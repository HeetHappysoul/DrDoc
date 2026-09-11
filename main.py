from datetime import datetime
import io
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from google import genai
from google.genai import types
from pydantic import BaseModel
from pypdf import PdfReader

load_dotenv()

app = FastAPI(title="DrDoc Extraction Service")

# Initialize Gemini Client
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# --- Database Setup (SQLite) ---
DB_FILE = "invoices.db"


def init_db():
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS approved_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_name TEXT NOT NULL,
            bill_no TEXT,
            date TEXT,
            sold_by TEXT,
            bill_to TEXT,
            total_amount REAL,
            reviewer_status TEXT DEFAULT 'human_approved',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
  conn.commit()
  conn.close()


init_db()


# --- Pydantic Schemas ---
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


class ApprovalRequest(BaseModel):
  document_name: str
  verified_data: InvoiceData
  reviewer_status: str = "human_approved"


# --- Extraction Functions ---
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
      if "503" in str(e) and attempt < max_retries:
        time.sleep(2 * attempt)
        continue
      raise e


# --- API Routes ---
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
                reason=(
                    "No readable digital text found in PDF (scanned image or"
                    " empty file)."
                ),
            )
        ],
    )

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


@app.post("/approve")
def approve_invoice(payload: ApprovalRequest):
  """Stores human-verified invoice records into the audit database."""
  try:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT INTO approved_invoices 
            (document_name, bill_no, date, sold_by, bill_to, total_amount, reviewer_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.document_name,
            payload.verified_data.bill_no,
            payload.verified_data.date,
            payload.verified_data.sold_by,
            payload.verified_data.bill_to,
            payload.verified_data.total_amount,
            payload.reviewer_status,
        ),
    )
    record_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {
        "status": "stored",
        "record_id": record_id,
        "document_name": payload.document_name,
    }
  except Exception as e:
    raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.get("/invoices")
def list_invoices():
  """Fetches all human-approved invoice records for reporting and auditing."""
  conn = sqlite3.connect(DB_FILE)
  conn.row_factory = sqlite3.Row
  cursor = conn.cursor()
  cursor.execute(
      "SELECT * FROM approved_invoices ORDER BY created_at DESC"
  )  #
  rows = cursor.fetchall()
  conn.close()
  return [dict(row) for row in rows]