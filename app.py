"""Second Look: Streamlit screen (owner: B). Run with `streamlit run app.py`.

Runs on made-up results from fake_data.py for now. To switch to real scoring, change
get_client_results() and analyze_upload(). Nothing else on the screen should need to change.

Look and feel: palette and fonts live in .streamlit/config.toml. The CSS below adds the
evidence "marker" highlight (heavier marker = stronger signal) and the call cards.
"""

import datetime
import html
import time

import plotly.graph_objects as go
import streamlit as st

import fake_data
from schema import SIGNALS

st.set_page_config(page_title="Second Look", page_icon=":material/manage_search:", layout="wide")

INK, GRAPHITE, RULE, LOUPE = "#1B2433", "#5E6B7E", "#D5DBE4", "#2D4FD6"
FONT = "Schibsted Grotesk, system-ui, sans-serif"

LEVEL_STYLE = {
    "green": {"color": "#2F7D6D", "label": "Steady"},
    "yellow": {"color": "#B9800F", "label": "Watch"},
    "red": {"color": "#B23A2E", "label": "Review now"},
    "unavailable": {"color": "#7A8494", "label": "Review manually"},
}

SIGNAL_LABELS = {
    "repetition": "Repetition",
    "memory_gaps": "Memory gaps",
    "new_influencer": "New influencer",
    "out_of_character": "Out of character",
    "urgency_secrecy": "Urgency or secrecy",
}

# Lighter versions of the level colors, readable on the dark sidebar.
SIDEBAR_LEVEL_COLOR = {"green": "#6CC4AE", "yellow": "#E8B84A", "red": "#F08A7E", "unavailable": "#A9B3C1"}

YELLOW_LINE, RED_LINE, MAX_SCORE = 4, 8, 15

# Placeholder until the real Bedrock guardrail is connected.
DIAGNOSIS_TERMS = ("dementia", "alzheimer", "cognitive", "diagnos", "senile", "mental illness", "impair")
GUARDRAIL_REFUSAL = (
    "Second Look can't assess medical or cognitive conditions. It only flags changes in "
    "behavior that may be worth a conversation. Review the quoted evidence and follow your "
    "firm's senior investor procedures."
)

st.markdown(
    f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:ital,wght@1,400;1,500&display=swap');
      :root {{ --ink:{INK}; --graphite:{GRAPHITE}; --rule:{RULE}; --loupe:{LOUPE}; }}
      .block-container {{ padding-top:2.5rem; max-width:1180px; }}

      .sl-name {{ font-size:2.5rem; font-weight:800; letter-spacing:-0.02em; line-height:1.05;
                  color:var(--ink); margin-bottom:0.35rem; }}
      .sl-standing {{ font-size:1.05rem; color:var(--ink); }}
      .sl-standing b {{ color:var(--lvl); }}
      .sl-meta {{ color:var(--graphite); margin-top:0.35rem; max-width:62rem; }}
      .sl-h {{ font-size:1.2rem; font-weight:700; color:var(--ink); margin:2.2rem 0 0.6rem; }}
      .sl-h small {{ font-size:0.92rem; font-weight:400; color:var(--graphite); margin-left:0.5rem; }}
      .sl-quiet {{ color:var(--graphite); }}

      /* The signature: quoted words get a marker stroke whose height grows with the score. */
      .sl-mark {{ font-style:italic; color:var(--ink); padding:0 0.1em;
                  box-decoration-break:clone; -webkit-box-decoration-break:clone; }}
      .sl-mark.s1 {{ background:linear-gradient(transparent 72%, rgba(45,79,214,0.22) 72%); }}
      .sl-mark.s2 {{ background:linear-gradient(transparent 42%, rgba(45,79,214,0.24) 42%); }}
      .sl-mark.s3 {{ background:linear-gradient(rgba(45,79,214,0.30), rgba(45,79,214,0.30)); }}

      /* Call cards: Streamlit containers keyed call-<client>-<n>. */
      [class*="st-key-call-"] {{ background:#fff; min-height:13.5rem; justify-content:space-between; }}
      .sl-call-when {{ color:var(--graphite); font-size:0.88rem; }}
      .sl-call-level {{ font-weight:700; color:var(--lvl); margin:0.15rem 0 0.55rem; }}
      .sl-call-level span {{ font-weight:400; color:var(--graphite); }}
      .sl-call-quote {{ line-height:1.5; }}

      .sl-summary {{ font-size:1.05rem; line-height:1.6; max-width:40rem; margin-bottom:0.6rem; }}
      .sl-signal {{ display:grid; grid-template-columns:9.5rem 2.6rem 1fr; column-gap:1rem;
                    align-items:baseline; padding:0.7rem 0; border-top:1px solid var(--rule); }}
      .sl-signal-name {{ font-weight:600; }}
      .sl-pips i {{ display:inline-block; width:0.5rem; height:0.85rem; margin-right:3px;
                    background:var(--rule); border-radius:1px; vertical-align:-1px; }}
      .sl-pips i.on {{ background:var(--loupe); }}
      .sl-signal-quote {{ line-height:1.55; }}
      @media (max-width:640px) {{
        .sl-signal {{ grid-template-columns:1fr auto; row-gap:0.3rem; }}
        .sl-signal-quote {{ grid-column:1 / -1; }}
        .sl-name {{ font-size:2rem; }}
      }}

      .sl-people {{ border-collapse:collapse; width:100%; }}
      .sl-people th {{ font-weight:500; font-size:0.88rem; color:var(--graphite); }}
      .sl-people th, .sl-people td {{ border-bottom:1px solid var(--rule); padding:0.55rem 0.4rem;
                                      text-align:center; }}
      .sl-people th:first-child, .sl-people td:first-child {{ text-align:left; }}
      .sl-people .dot {{ display:inline-block; width:0.45rem; height:0.45rem; border-radius:50%;
                         background:var(--ink); }}
      .sl-people .gap {{ color:#AEB7C4; }}
      .sl-new {{ font-weight:700; color:var(--loupe); }}
      .sl-people-note {{ color:var(--graphite); margin-top:0.6rem; font-size:0.92rem; }}

      .sl-draft {{ font-style:italic; line-height:1.6; max-width:44rem; margin:0.1rem 0 0.4rem; }}
      .sl-plain {{ line-height:1.6; max-width:44rem; margin:0.1rem 0 0.4rem; }}

      [data-testid="stSidebar"] .sl-wordmark {{ font-weight:800; font-size:1.3rem; letter-spacing:-0.01em; }}
      [data-testid="stSidebar"] [class*="st-key-pick-"] button {{ justify-content:flex-start;
          padding:0.35rem 0.6rem; border-radius:0.35rem; min-height:2.4rem; }}
      [data-testid="stSidebar"] [class*="st-key-pick-"] button > div {{ justify-content:flex-start; width:100%; }}
      [data-testid="stSidebar"] [class*="st-key-pick-"] button p {{ text-align:left; }}
      [data-testid="stSidebar"] .sl-side-status {{ font-size:0.85rem; font-weight:600; text-align:right;
          line-height:2.4rem; }}
      [data-testid="stSidebar"] [data-testid="stMarkdownContainer"]:has(.sl-side-status) {{ margin-bottom:0; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------- Data access (swap these for real scoring later) ----------

def get_clients():
    return fake_data.CLIENTS


def get_client_results(client_id):
    """All analyzed calls for a client, oldest first."""
    return fake_data.RESULTS[client_id] + st.session_state.uploaded.get(client_id, [])


def analyze_upload(client_id, transcript):
    """Score a newly delivered call. For now, returns the made-up result for Walter's Call 4.

    Pasted transcripts can't be scored until scoring.py is connected, so they come back as
    "unavailable". The screen must fail safe: never show a fake green.
    """
    pending = fake_data.PENDING_UPLOADS.get(client_id)
    if pending and transcript == fake_data.PENDING_TRANSCRIPTS.get(client_id):
        return pending
    next_number = len(get_client_results(client_id)) + 1
    return {
        "client_id": client_id,
        "call_number": next_number,
        "date": time.strftime("%Y-%m-%d"),
        "signals": {s: {"score": 0, "quote": ""} for s in SIGNALS},
        "people_mentioned": [],
        "new_people": [],
        "total": 0,
        "level": "unavailable",
        "summary": "Analysis unavailable, review manually.",
        "next_steps": None,
    }


# ---------- Session state ----------

st.session_state.setdefault("uploaded", {})   # client_id -> list of results added this session
st.session_state.setdefault("selected", {})   # client_id -> selected call_number
st.session_state.setdefault("decisions", {})  # (client_id, call_number, item) -> "approved" | "dismissed"
st.session_state.setdefault("answers", {})    # client_id -> last (question, answer, blocked)


# ---------- Small render helpers ----------

def style_for(level):
    return LEVEL_STYLE.get(level, LEVEL_STYLE["unavailable"])


def nice_date(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def mark(quote, score):
    return f'<span class="sl-mark s{max(1, min(score, 3))}">“{html.escape(quote)}”</span>'


def pips(score):
    return (f'<span class="sl-pips" title="{score} of 3">' + '<i class="on"></i>' * score
            + "<i></i>" * (3 - score) + "</span>")


def strongest(result):
    flagged = [s for s in result["signals"].values() if s["score"] > 0 and s["quote"]]
    return max(flagged, key=lambda s: s["score"]) if flagged else None


def current_level(results):
    return results[-1]["level"] if results else "unavailable"


def first_name(c):
    return c["name"].split()[0]


def heading(text, aside=""):
    aside_html = f"<small>{aside}</small>" if aside else ""
    st.markdown(f'<div class="sl-h">{text}{aside_html}</div>', unsafe_allow_html=True)


# ---------- Sidebar ----------

clients = get_clients()
st.session_state.setdefault("client_id", next(iter(clients)))

with st.sidebar:
    st.markdown('<div class="sl-wordmark">Second Look</div>', unsafe_allow_html=True)
    st.caption("Behavior change across client calls")
    st.write("")
    for cid in clients:
        is_open = cid == st.session_state.client_id
        level = current_level(get_client_results(cid))
        name_col, status_col = st.columns([3, 2], vertical_alignment="center", gap="small")
        if name_col.button(clients[cid]["name"], key=f"pick-{cid}",
                           type="secondary" if is_open else "tertiary", width="stretch"):
            st.session_state.client_id = cid
            st.rerun()
        status_col.markdown(f'<div class="sl-side-status" style="color:{SIDEBAR_LEVEL_COLOR.get(level, SIDEBAR_LEVEL_COLOR["unavailable"])}">'
                            f'{style_for(level)["label"]}</div>', unsafe_allow_html=True)
    st.divider()
    if st.button("Reset demo", width="stretch", help="Removes calls added this session and clears approvals."):
        for key in ("uploaded", "selected", "decisions", "answers"):
            st.session_state[key] = {}
        st.rerun()
    st.caption("All clients and conversations are made up.")

client_id = st.session_state.client_id
client = clients[client_id]
results = get_client_results(client_id)
selected_number = st.session_state.selected.get(client_id, results[-1]["call_number"])
selected = next((r for r in results if r["call_number"] == selected_number), results[-1])
latest = results[-1]


# ---------- Client header ----------

latest_style = style_for(latest["level"])
if latest["level"] == "unavailable":
    standing = f'<b>{latest_style["label"]}</b> · Call {latest["call_number"]} on {nice_date(latest["date"])} wasn\'t scored'
else:
    standing = (f'<b>{latest_style["label"]}</b> · Call {latest["call_number"]} on {nice_date(latest["date"])} '
                f'scored {latest["total"]} of {MAX_SCORE}')
st.markdown(
    f'<div class="sl-name">{html.escape(client["name"])}</div>'
    f'<div class="sl-standing" style="--lvl:{latest_style["color"]}">{standing}</div>'
    f'<div class="sl-meta">Advisor {html.escape(client["advisor"])} · Trusted contact '
    f'{html.escape(client["trusted_contact"])} · Age {client["age"]}<br>{html.escape(client["notes"])}</div>',
    unsafe_allow_html=True,
)


# ---------- New call from the note-taker ----------

def run_analysis(transcript):
    with st.status("New transcript received from the note-taker", expanded=True) as box:
        st.write("Saved to the private S3 bucket")
        time.sleep(0.8)
        st.write("Found the people mentioned")
        time.sleep(0.8)
        st.write(f"Compared with {first_name(client)}'s past calls")
        time.sleep(1.5)
        result = analyze_upload(client_id, transcript)
        if result["level"] in ("yellow", "red"):
            st.write("Drafted next steps")
            time.sleep(0.8)
        box.update(label="Analysis complete", state="complete")
    st.session_state.uploaded.setdefault(client_id, []).append(result)
    st.session_state.selected[client_id] = result["call_number"]
    st.rerun()


pending_transcript = fake_data.PENDING_TRANSCRIPTS.get(client_id)
already_uploaded = any(r["call_number"] == fake_data.PENDING_UPLOADS[client_id]["call_number"]
                       for r in st.session_state.uploaded.get(client_id, [])) if pending_transcript else True

if pending_transcript and not already_uploaded:
    next_call = fake_data.PENDING_UPLOADS[client_id]["call_number"]
    st.write("")
    with st.container(border=True):
        msg_col, btn_col = st.columns([3, 1], vertical_alignment="center")
        msg_col.markdown(f"**{client['advisor']} just finished Call {next_call} with {first_name(client)}.** "
                         "The note-taker has the transcript ready.")
        if btn_col.button(f"Receive Call {next_call} transcript", type="primary", key="receive", width="stretch"):
            run_analysis(pending_transcript)


# ---------- Calls ----------

heading("Calls", f"compared only with {first_name(client)}'s own earlier calls")

selected_key = f"call-{client_id}-{selected['call_number']}"
st.markdown(f"<style>.st-key-{selected_key} {{ border:2px solid {INK} !important; }}</style>",
            unsafe_allow_html=True)

cols = st.columns(len(results))
for col, r in zip(cols, results):
    s = style_for(r["level"])
    is_selected = r["call_number"] == selected["call_number"]
    top = strongest(r)
    if r["level"] == "unavailable":
        body = '<div class="sl-quiet">Not scored. Read the transcript.</div>'
        score = ""
    else:
        body = (f'<div class="sl-call-quote">{mark(top["quote"], top["score"])}</div>' if top
                else '<div class="sl-quiet">Nothing flagged.</div>')
        score = f'<span> · {r["total"]} of {MAX_SCORE}</span>'
    with col:
        with st.container(border=True, key=f"call-{client_id}-{r['call_number']}"):
            st.markdown(
                f'<div class="sl-call-when">Call {r["call_number"]} · {nice_date(r["date"])}</div>'
                f'<div class="sl-call-level" style="--lvl:{s["color"]}">{s["label"]}{score}</div>{body}',
                unsafe_allow_html=True,
            )
            if st.button("Shown below" if is_selected else "Open call", key=f"view-{client_id}-{r['call_number']}",
                         disabled=is_selected, type="tertiary"):
                st.session_state.selected[client_id] = r["call_number"]
                st.rerun()

fig = go.Figure()
for y, lvl, text in ((YELLOW_LINE, "yellow", "Watch from 4"), (RED_LINE, "red", "Review from 8")):
    fig.add_hline(y=y, line_dash="dot", line_width=1, line_color=LEVEL_STYLE[lvl]["color"],
                  annotation_text=text, annotation_position="top left",
                  annotation_font=dict(family=FONT, size=12, color=LEVEL_STYLE[lvl]["color"]))
fig.add_trace(go.Scatter(
    x=[f'Call {r["call_number"]}' for r in results],
    y=[r["total"] for r in results],
    customdata=[nice_date(r["date"]) for r in results],
    mode="lines+markers",
    line=dict(color=INK, width=2),
    marker=dict(size=12, color=[style_for(r["level"])["color"] for r in results], line=dict(width=2, color="white")),
    hovertemplate="%{x}, %{customdata}<br>Score %{y} of 15<extra></extra>",
))
fig.update_layout(
    height=210, margin=dict(l=0, r=0, t=16, b=0), showlegend=False,
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family=FONT, size=13, color=GRAPHITE),
    xaxis=dict(showgrid=False, linecolor=RULE),
    yaxis=dict(range=[0, MAX_SCORE], dtick=4, gridcolor="rgba(213,219,228,0.6)", zeroline=False, title=None),
)
st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


# ---------- Evidence for the selected call + people ----------

detail_col, people_col = st.columns([3, 2], gap="large")

with detail_col:
    sel_style = style_for(selected["level"])
    heading(f'What changed in Call {selected["call_number"]}', nice_date(selected["date"]))
    if selected["level"] == "unavailable":
        st.warning("Analysis unavailable, review manually. Scoring didn't run for this call, "
                   "so no level is shown. Read the transcript before acting.")
    else:
        rows = ""
        for name in SIGNALS:
            sig = selected["signals"][name]
            quote = mark(sig["quote"], sig["score"]) if sig["score"] > 0 else '<span class="sl-quiet">Not present</span>'
            rows += (f'<div class="sl-signal"><div class="sl-signal-name">{SIGNAL_LABELS[name]}</div>'
                     f'{pips(sig["score"])}<div class="sl-signal-quote">{quote}</div></div>')
        st.markdown(f'<div class="sl-summary">{html.escape(selected["summary"])}</div>{rows}',
                    unsafe_allow_html=True)

with people_col:
    heading(f"People {first_name(client)} mentions")
    names = []
    for r in results:
        for p in r["people_mentioned"]:
            if p not in names:
                names.append(p)
    if not names:
        st.markdown('<div class="sl-quiet">No one mentioned yet.</div>', unsafe_allow_html=True)
    else:
        header = "".join(f"<th>Call {r['call_number']}</th>" for r in results)
        rows, notes = "", []
        for name in names:
            seen_in = [r["call_number"] for r in results if name in r["people_mentioned"]]
            first_seen = seen_in[0]
            is_new = first_seen > results[0]["call_number"] and any(name in r["new_people"] for r in results)
            if is_new:
                later = len(seen_in) - 1
                notes.append(f"{html.escape(name)} first appears in Call {first_seen}"
                             + (f" and comes up in {later} later call{'s' if later > 1 else ''}." if later
                                else ", mentioned once."))
            cells = ""
            for r in results:
                if name not in r["people_mentioned"]:
                    cells += '<td class="gap">–</td>'
                elif is_new and r["call_number"] == first_seen:
                    cells += '<td><span class="sl-new">New</span></td>'
                else:
                    cells += '<td><span class="dot"></span></td>'
            rows += f"<tr><td>{html.escape(name)}</td>{cells}</tr>"
        note_html = f'<div class="sl-people-note">{" ".join(notes)}</div>' if notes else ""
        st.markdown(f'<table class="sl-people"><tr><th>Person</th>{header}</tr>{rows}</table>{note_html}',
                    unsafe_allow_html=True)


# ---------- Next steps ----------

def decision_row(item_key, label, body, quoted=True):
    key = (client_id, selected["call_number"], item_key)
    decision = st.session_state.decisions.get(key)
    with st.container(border=True):
        st.markdown(f"**{label}**")
        st.markdown(f'<div class="{"sl-draft" if quoted else "sl-plain"}">{html.escape(body)}</div>',
                    unsafe_allow_html=True)
        if decision == "approved":
            st.success("Approved. Nothing is sent from this demo.", icon=":material/check:")
        elif decision == "dismissed":
            st.markdown('<div class="sl-quiet">Dismissed.</div>', unsafe_allow_html=True)
        else:
            with st.container(horizontal=True):
                if st.button("Approve", key=f"approve-{key}", type="primary"):
                    st.session_state.decisions[key] = "approved"
                    st.rerun()
                if st.button("Dismiss", key=f"dismiss-{key}"):
                    st.session_state.decisions[key] = "dismissed"
                    st.rerun()


steps = selected.get("next_steps")
if selected["level"] in ("yellow", "red") and steps:
    heading("Suggested next steps", f"nothing is sent until {html.escape(client['advisor'])} approves it")
    decision_row("script", "Check-in call script", steps["advisor_script"].strip('"'))
    if steps.get("trusted_contact_message"):
        decision_row("contact", f'Message to {client["trusted_contact"]}, needs compliance approval',
                     steps["trusted_contact_message"])
    if steps.get("hold_note"):
        decision_row("hold", "Possible temporary hold under FINRA Rule 2165", steps["hold_note"], quoted=False)


# ---------- Ask + manual notes ----------

ask_col, add_col = st.columns(2, gap="large")

with ask_col:
    heading(f"Ask about {first_name(client)}")
    with st.form(f"ask-{client_id}", clear_on_submit=True, border=False):
        question = st.text_input("Question", label_visibility="collapsed",
                                 placeholder=f"What changed in {first_name(client)}'s last call?")
        asked = st.form_submit_button("Ask")
    if asked and question.strip():
        if any(term in question.lower() for term in DIAGNOSIS_TERMS):
            st.session_state.answers[client_id] = (question, GUARDRAIL_REFUSAL, True)
        else:
            st.session_state.answers[client_id] = (
                question, f'Latest call ({nice_date(latest["date"])}): {latest["summary"]}', False)
    last = st.session_state.answers.get(client_id)
    if last:
        q, answer, blocked = last
        st.markdown(f"**You asked:** {html.escape(q)}")
        if blocked:
            st.error(answer, icon=":material/block:")
        else:
            st.info(answer)
        st.caption("Placeholder answers until the agent and Bedrock guardrail are connected.")

with add_col:
    heading("Add a call without a note-taker")
    pasted = st.text_area("Transcript or typed notes", height=110, label_visibility="collapsed",
                          placeholder="Paste the transcript or type your call notes")
    if st.button("Analyze notes", disabled=not pasted.strip()):
        run_analysis(pasted.strip())
