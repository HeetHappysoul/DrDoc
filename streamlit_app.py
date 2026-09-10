import base64
import json
import requests
import streamlit as st

st.set_page_config(page_title="DrDoc - Invoice Review Queue", layout="wide")

st.title("📄 DrDoc Invoice Extractor & Review Queue")

# API Configuration
API_URL = st.sidebar.text_input(
    "Backend API URL",
    value="https://your-service-url.onrender.com/extract",
    help="Paste your live Render URL ending with /extract",
)

uploaded_file = st.file_uploader("Upload an Invoice PDF", type=["pdf"])

if uploaded_file is not None:
    # Trigger extraction if not already run for this file
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
                response = requests.post(API_URL, files=files, timeout=45)
                response_json = response.json()
                st.session_state.extraction_result = response_json
                st.session_state.last_uploaded = uploaded_file.name
                # Reset previous approval payload on new upload
                st.session_state.pop("approved_payload", None)
            except Exception as e:
                st.error(f"Failed to connect to extraction service: {str(e)}")
                st.stop()

    result = st.session_state.get("extraction_result", {})
    status = result.get("status", "error")
    data = result.get("data") or {}
    review_queue = result.get("review_queue", [])

    # Visual alert for review queue status
    if review_queue:
        st.warning(
            f"⚠️ **Review Required:** {len(review_queue)} field(s) flagged for human verification."
        )
        for item in review_queue:
            st.info(
                f"**Flagged Field:** `{item.get('flagged_field')}` — *{item.get('reason')}*"
            )
    else:
        st.success(
            "✅ **Extraction Complete:** All fields extracted without validation warnings."
        )

    # Define the two columns explicitly
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
                "Bill No"
                + (" ⚠️ [FLAGGED]" if "bill_no" in flagged_names else ""),
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

            submitted = st.form_submit_button("Approve & Export Verified Record")

            if submitted:
                st.session_state.approved_payload = {
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

        # Placed outside st.form so download_button executes cleanly
        if "approved_payload" in st.session_state:
            st.success("Record approved successfully!")
            st.json(st.session_state.approved_payload)
            st.download_button(
                label="📥 Download Verified JSON",
                data=json.dumps(st.session_state.approved_payload, indent=2),
                file_name=f"verified_{uploaded_file.name}.json",
                mime="application/json",
            )