import base64
import json
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="DrDoc - Invoice Review Queue", layout="wide")

st.title("📄 DrDoc Invoice Extractor & Verification Hub")

# Sidebar Configuration
BASE_URL = st.sidebar.text_input(
    "Backend Base URL",
    value="http://127.0.0.1:8000",
    help="Base URL of your API (e.g. http://127.0.0.1:8000 or your Render URL without /extract)",
)
# Strip trailing slashes for clean path joining
BASE_URL = BASE_URL.rstrip("/")
EXTRACT_URL = f"{BASE_URL}/extract"
APPROVE_URL = f"{BASE_URL}/approve"
INVOICES_URL = f"{BASE_URL}/invoices"

tab_review, tab_audit = st.tabs(
    ["🔍 Document Reviewer", "📊 Approved Audit Trail"]
)

# ----------------- TAB 1: REVIEW INTERFACE -----------------
with tab_review:
  uploaded_file = st.file_uploader("Upload an Invoice PDF", type=["pdf"])

  if uploaded_file is not None:
    if (
        "last_uploaded" not in st.session_state
        or st.session_state.last_uploaded != uploaded_file.name
    ):
      with st.spinner("Extracting fields via Gemini..."):
        try:
          files = {
              "file": (
                  uploaded_file.name,
                  uploaded_file.getvalue(),
                  "application/pdf",
              )
          }
          response = requests.post(EXTRACT_URL, files=files, timeout=120)
          response_json = response.json()
          st.session_state.extraction_result = response_json
          st.session_state.last_uploaded = uploaded_file.name
          st.session_state.pop("approved_payload", None)
        except Exception as e:
          st.error(f"Failed to connect to extraction service: {str(e)}")
          st.stop()

    result = st.session_state.get("extraction_result", {})
    data = result.get("data") or {}
    review_queue = result.get("review_queue", [])

    if review_queue:
      st.warning(
          f"⚠️ **Review Required:** {len(review_queue)} field(s) flagged for"
          " human verification."
      )
      for item in review_queue:
        st.info(
            f"**Flagged Field:** `{item.get('flagged_field')}` —"
            f" *{item.get('reason')}*"
        )
    else:
      st.success("✅ **Extraction Clean:** All fields extracted with high confidence.")

    col_pdf, col_form = st.columns([1, 1], gap="large")

    with col_pdf:
      st.subheader("Document Preview")
      base64_pdf = base64.b64encode(uploaded_file.getvalue()).decode("utf-8")
      pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="700" type="application/pdf"></iframe>'
      st.markdown(pdf_display, unsafe_allow_html=True)

    with col_form:
      st.subheader("Extracted Fields Verification")
      flagged_names = {item.get("flagged_field") for item in review_queue}

      with st.form(key="review_form"):
        bill_no = st.text_input(
            "Bill No" + (" ⚠️ [FLAGGED]" if "bill_no" in flagged_names else ""),
            value=data.get("bill_no") or "",
        )
        date = st.text_input(
            "Invoice Date"
            + (" ⚠️ [FLAGGED]" if "date" in flagged_names else ""),
            value=data.get("date") or "",
        )
        sold_by = st.text_input(
            "Sold By (Vendor)"
            + (" ⚠️ [FLAGGED]" if "sold_by" in flagged_names else ""),
            value=data.get("sold_by") or "",
        )
        bill_to = st.text_input(
            "Bill To (Customer)"
            + (" ⚠️ [FLAGGED]" if "bill_to" in flagged_names else ""),
            value=data.get("bill_to") or "",
        )
        total_amount = st.number_input(
            "Total Amount ($)"
            + (" ⚠️ [FLAGGED]" if "total_amount" in flagged_names else ""),
            value=float(data.get("total_amount") or 0.0),
            format="%.2f",
        )

        submitted = st.form_submit_button("Approve & Store to Database")

        if submitted:
          payload = {
              "document_name": uploaded_file.name,
              "verified_data": {
                  "bill_no": bill_no,
                  "date": date,
                  "sold_by": sold_by,
                  "bill_to": bill_to,
                  "total_amount": total_amount,
              },
              "reviewer_status": "human_approved",
          }
          try:
            res = requests.post(APPROVE_URL, json=payload, timeout=15)
            if res.status_code == 200:
              st.session_state.approved_payload = payload
              st.session_state.db_record_id = res.json().get("record_id")
            else:
              st.error(f"Approval failed: {res.text}")
          except Exception as err:
            st.error(f"Database connection error: {str(err)}")

      if "approved_payload" in st.session_state:
        st.success(
            f"Record #{st.session_state.get('db_record_id')} permanently saved"
            " to database!"
        )
        st.download_button(
            label="📥 Download JSON Backup",
            data=json.dumps(st.session_state.approved_payload, indent=2),
            file_name=f"verified_{uploaded_file.name}.json",
            mime="application/json",
        )

# ----------------- TAB 2: AUDIT TRAIL & LOGS -----------------
with tab_audit:
  st.subheader("Central Audit Trail")
  if st.button("🔄 Refresh Audit Logs"):
    st.rerun()

  try:
    resp = requests.get(INVOICES_URL, timeout=15)
    if resp.status_code == 200:
      records = resp.json()
      if records:
        df = pd.DataFrame(records)

        # Summary KPIs
        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("Total Invoices Approved", len(df))
        total_spend = (
            df["total_amount"].sum() if "total_amount" in df.columns else 0.0
        )
        kpi2.metric("Total Verified Spend", f"${total_spend:,.2f}")
        kpi3.metric("Reviewer Status", "100% Audited")

        # Display table
        st.dataframe(
            df[
                [
                    "id",
                    "document_name",
                    "bill_no",
                    "date",
                    "sold_by",
                    "bill_to",
                    "total_amount",
                    "created_at",
                ]
            ],
            use_container_width=True,
        )

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Export Full Audit Log (CSV)",
            data=csv,
            file_name="invoice_audit_trail.csv",
            mime="text/csv",
        )
      else:
        st.info(
            "No approved records found in database. Approve an invoice in the"
            " Reviewer tab to see it here."
        )
    else:
      st.error(f"Failed to fetch records: {resp.text}")
  except Exception as e:
    st.warning(f"Could not reach database endpoint: {str(e)}")