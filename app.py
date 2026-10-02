"""Second Look: Streamlit screen (owner: B). Run with `streamlit run app.py`."""

import streamlit as st

st.set_page_config(page_title="Second Look", layout="wide")
st.title("Second Look")
st.caption("Flags behavior change across a client's calls. Never diagnoses; a human approves every action.")

# TODO(B): client picker, client card, timeline + Plotly chart (yellow=4, red=8 lines),
# call detail with the 5 signals and quotes, people-mentioned panel, upload,
# next-steps panel with Approve buttons, "Ask about this client" box (guardrail demo).
