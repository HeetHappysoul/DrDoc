from datetime import datetime
import io
import os
import sqlite3
from typing import List, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from pypdf import PdfReader

load_dotenv()

app = FastAPI(title="DrDoc Extraction Service")

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
  bill_no: Optional[str] = Field(
      default=None,
      description="The invoice or bill number / reference identifier.",
  )
  date: Optional[str] = Field(
      default=None,
      description="The date of the invoice (e.g. YYYY-MM-DD or standard date string).",
  )
  bill_to: Optional[str] = Field(
      default=None,
      description="The customer, client, or recipient organization/person.",
  )
  sold_by: Optional[str] = Field(
      default=None,
      description="The vendor, seller, or billing entity name and address.",
  )
  total_amount: Optional[float] = Field(
      default=None,
      description="The final total invoice amount as a numeric float.",
  )


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


# --- LangChain LCEL Setup ---
# 1. Base Model
llm = ChatGoogleGenerativeAI(
    model="gemini-3.6-flash",
    google_api_key=os.getenv("GEMINI_API_KEY"),
    temperature=0.0,
)

# 2. Structured Output with Built-In Exponential Backoff Retries
structured_llm = llm.with_structured_output(InvoiceData).with_retry(
    stop_after_attempt=3
)

# 3. Prompt Template
extraction_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        (
            "You are an expert invoice parsing engine. Extract the required"
            " fields with absolute precision. If any field is ambiguous or"
            " absent, leave it as null. Do not invent details."
        ),
    ),
    ("human", "Invoice Document Text:\n\n{raw_text}"),
])

# 4. Declarative Chain: Text -> Prompt -> Model -> Validated InvoiceData Object
invoice_chain = extraction_prompt | structured_llm


# --- PDF Parser ---
def extract_text_from_pdf(pdf_bytes: bytes) -> str:
  pdf_file = io.BytesIO(pdf_bytes)
  reader = PdfReader(pdf_file)
  extracted_text = ""
  for page_num, page in enumerate(reader.pages):
    page_text = page.extract_text() or ""
    extracted_text += f"\n--- Page {page_num + 1} ---\n" + page_text
  return extracted_text.strip()


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
                reason="No readable text found in PDF (empty or scanned image).",
            )
        ],
    )

  try:
    # Invoking the LCEL chain directly yields a populated InvoiceData object
    invoice_data: InvoiceData = invoice_chain.invoke({"raw_text": raw_text})
  except Exception as e:
    return ExtractionResponse(
        status="error",
        data=None,
        review_queue=[
            ReviewItem(
                document_name=file.filename,
                flagged_field="llm_service",
                reason=f"LangChain extraction failed: {str(e)}",
            )
        ],
    )

  # Check for missing/null fields and route them to the human review queue
  review_queue: List[ReviewItem] = []
  for field_name, value in invoice_data.model_dump().items():
    if value is None:
      review_queue.append(
          ReviewItem(
              bill_no=invoice_data.bill_no,
              document_name=file.filename,
              flagged_field=field_name,
              extracted_value=None,
              reason=(
                  f"Field '{field_name}' could not be extracted with certainty."
              ),
          )
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
  cursor.execute("SELECT * FROM approved_invoices ORDER BY created_at DESC")
  rows = cursor.fetchall()
  conn.close()
  return [dict(row) for row in rows]