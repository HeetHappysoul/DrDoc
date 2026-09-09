# DrDoc — Invoice Extraction & Verification Service

An automated API service that extracts structured data from commercial invoices and routes missing or ambiguous fields to a human review queue.

## Tech Stack
- **Framework:** FastAPI
- **Model:** Google Gemini (Structured JSON)
- **PDF Extraction:** pypdf
- **Validation:** Pydantic

## API Endpoints
- `POST /extract` — Accepts an uploaded PDF invoice and returns structured JSON with an audit review queue.
- `GET /docs` — Interactive Swagger documentation.