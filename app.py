"""Second Look: Streamlit screen (owner: B). Run with `streamlit run app.py`.

Live mode (AWS credentials + model IDs set): transcripts come from the private S3 bucket
(falling back to data/ if S3 isn't reachable), Comprehend finds the people mentioned,
A's scoring.py scores each call with Bedrock, uploads are saved to S3, and the Ask box
goes through the Bedrock Guardrail. Without AWS it shows A's hand-written sample results,
clearly labeled. Analysis results are kept in memory only, never stored.

Look and feel: palette and fonts live in .streamlit/config.toml. The CSS below adds the
evidence "marker" highlight (heavier marker = stronger signal) and the call cards.
"""

import datetime
import hashlib
import html
import json

import plotly.graph_objects as go
import streamlit as st

import aws_clients
import config
import scoring
from aws_clients import AWSServiceError, GuardrailBlocked
from schema import SIGNALS

st.set_page_config(page_title="Second Look", page_icon=":material/manage_search:", layout="wide")

INK, GRAPHITE, RULE, LOUPE = "#1B2433", "#5E6B7E", "#D5DBE4", "#2D4FD6"
FONT = "Schibsted Grotesk, system-ui, sans-serif"

CLIENT_IDS = ["walter", "maria", "linda"]

LEVEL_STYLE = {
    "green": {"color": "#2F7D6D", "label": "Steady"},
    "yellow": {"color": "#B9800F", "label": "Watch"},
    "red": {"color": "#B23A2E", "label": "Review now"},
    "unavailable": {"color": "#7A8494", "label": "Review manually"},
    "pending": {"color": "#7A8494", "label": "Not analyzed yet"},
}

SIGNAL_LABELS = {
    "repetition": "Repetition",
    "memory_gaps": "Memory gaps",
    "new_influencer": "New influencer",
    "out_of_character": "Out of character",
    "urgency_secrecy": "Urgency or secrecy",
}

# Lighter versions of the level colors, readable on the dark sidebar.
SIDEBAR_LEVEL_COLOR = {"green": "#6CC4AE", "yellow": "#E8B84A", "red": "#F08A7E",
                       "unavailable": "#A9B3C1", "pending": "#A9B3C1"}

YELLOW_LINE, RED_LINE, MAX_SCORE = 4, 8, 15

ASK_SYSTEM = (
    "You answer a financial advisor's questions about one client, using only the call evidence provided. "
    "Describe observable behavior and cite the client's quoted words. Do not name, suggest, or speculate "
    "about any medical or mental health condition. If the evidence doesn't answer the question, say so. "
    "Answer in at most 120 words."
)

# Used only when no guardrail is configured, so the demo never answers a diagnosis question.
DIAGNOSIS_TERMS = ("dementia", "alzheimer", "cognitive", "diagnos", "senile", "mental illness", "impair")
LOCAL_REFUSAL = (
    "Second Look can't assess medical or cognitive conditions. It only flags changes in behavior "
    "that may be worth a conversation."
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


# ---------- Session state ----------

st.session_state.setdefault("client_id", CLIENT_IDS[0])
st.session_state.setdefault("use_live", True)
st.session_state.setdefault("uploaded", {})          # mode -> client_id -> [{"call", "result", "people"}]
st.session_state.setdefault("session_analysis", {})  # analyses with a failed call, kept for this session only
st.session_state.setdefault("selected", {})          # client_id -> selected call_number
st.session_state.setdefault("decisions", {})         # (client_id, call_number, item) -> "approved" | "dismissed"
st.session_state.setdefault("answers", {})           # client_id -> (question, answer, kind, note)
st.session_state.setdefault("flash", "")


# ---------- AWS status and data ----------

@st.cache_data(ttl=300, show_spinner=False)
def aws_status():
    """Which live pieces work right now. Cheap checks only, no model calls."""
    if not (config.SONNET_MODEL_ID and config.HAIKU_MODEL_ID):
        return {"live": False, "s3": False, "why": "The model IDs are missing in .env."}
    try:
        aws_clients.caller_arn()
    except AWSServiceError as err:
        return {"live": False, "s3": False, "why": str(err)}
    return {"live": True, "s3": aws_clients.bucket_ready(), "why": ""}


@st.cache_data(ttl=600, show_spinner=False)
def load_history(client_id, use_s3):
    """Client file and past transcripts: S3 first, the local data/ copy if S3 can't be read."""
    if use_s3:
        try:
            client = aws_clients.load_client(client_id)
            calls = aws_clients.load_transcripts(client_id, include_uploads=False)
            if calls:
                return client, calls, "s3"
        except AWSServiceError:
            pass
    return scoring.load_client_local(client_id), scoring.load_calls_local(client_id), "local"


@st.cache_resource
def analysis_store():
    """Live analyses shared across page refreshes, in memory only (never written anywhere)."""
    return {}


status = aws_status()
live = status["live"] and st.session_state.use_live
mode = "live" if live else "sample"


def store_key(client_id, calls, source):
    digest = hashlib.sha256(json.dumps(calls, sort_keys=True).encode()).hexdigest()[:16]
    return (client_id, source, digest)


def base_state(client_id):
    """History for a client. results is None if live analysis hasn't run yet."""
    client, calls, source = load_history(client_id, status["s3"])
    state = {"client": client, "calls": calls, "source": source, "results": None, "people": None}
    if not live:
        state["results"] = list(scoring.load_sample_results().get(client_id, []))
        return state
    key = store_key(client_id, calls, source)
    entry = analysis_store().get(key) or st.session_state.session_analysis.get(key)
    if entry:
        state.update(entry)
    return state


def comprehend_people(calls):
    try:
        return [aws_clients.extract_people(c["transcript"]) for c in calls]
    except AWSServiceError:
        return None


def ensure_analyzed(client_id):
    state = base_state(client_id)
    if state["results"] is not None:
        return state
    client, calls = state["client"], state["calls"]
    with st.status(f"Analyzing {client['name']}'s {len(calls)} calls with Bedrock", expanded=True) as box:
        people = comprehend_people(calls)
        box.write("Found the people mentioned with Amazon Comprehend" if people is not None
                  else "Comprehend unavailable, using the names Claude found")
        results = []
        for i, call in enumerate(calls):
            box.write(f"Scoring Call {call['call_number']} against the calls before it")
            results.append(scoring.analyze_new_call(client, calls[:i], results, call,
                                                    people[: i + 1] if people else None))
        failed = any(r["level"] == "unavailable" for r in results)
        box.update(label="Some calls couldn't be analyzed" if failed else "Analysis complete",
                   state="error" if failed else "complete")
    entry = {"results": results, "people": people}
    key = store_key(client_id, calls, state["source"])
    if failed:
        st.session_state.session_analysis[key] = entry  # retried only when someone clicks Re-run
    else:
        analysis_store()[key] = entry
    state.update(entry)
    return state


def uploads(client_id):
    return st.session_state.uploaded.setdefault(mode, {}).setdefault(client_id, [])


def full_history(state, client_id):
    """Base history plus calls received this session: (calls, results, people or None)."""
    added = uploads(client_id)
    calls = state["calls"] + [u["call"] for u in added]
    results = (state["results"] or []) + [u["result"] for u in added]
    people = None
    if state["people"] is not None and all(u["people"] is not None for u in added):
        people = state["people"] + [u["people"] for u in added]
    return calls, results, people


def pending_demo_call(client_id, calls):
    """The note-taker's next transcript (Walter's Call 4), if it hasn't arrived yet."""
    have = {c["call_number"] for c in calls}
    return next((c for c in scoring.load_calls_local(client_id, include_demo=True)
                 if c["call_number"] not in have), None)


def receive_call(state, client_id, new_call, from_note_taker):
    client = state["client"]
    calls, results, people = full_history(state, client_id)
    new_people = None
    with st.status("New transcript received from the note-taker" if from_note_taker else "Analyzing your notes",
                   expanded=True) as box:
        if status["s3"]:
            try:
                box.write(f"Saved to the private S3 bucket ({aws_clients.save_upload(new_call)})")
            except AWSServiceError as err:
                box.write(f"Couldn't save to S3 ({err}). Analyzing anyway.")
        else:
            box.write("S3 isn't connected, so this transcript stays on this screen only")
        if live:
            if people is not None:
                try:
                    new_people = aws_clients.extract_people(new_call["transcript"])
                    box.write("Found the people mentioned with Amazon Comprehend")
                except AWSServiceError:
                    people = None
                    box.write("Comprehend unavailable, using the names Claude finds")
            box.write(f"Comparing with {first_name(client)}'s past calls (Claude Sonnet), "
                      "then drafting next steps (Claude Haiku)")
            result = scoring.analyze_new_call(
                client, calls, results, new_call,
                people + [new_people] if people is not None and new_people is not None else None)
        else:
            samples = scoring.load_sample_results()
            result = samples.get(f"{client_id}_demo_call_{new_call['call_number']}") if from_note_taker else None
            if result is None:
                result = scoring.unavailable_result(
                    new_call, "Sample mode can't score new notes. Connect AWS to analyze them.")
            box.write("Sample mode: showing the team's hand-written result, not a live analysis")
        failed = result["level"] == "unavailable"
        box.update(label="Analysis unavailable, review manually" if failed else "Analysis complete",
                   state="error" if failed else "complete")
    uploads(client_id).append({"call": new_call, "result": result, "people": new_people if live else None})
    st.session_state.selected[client_id] = result["call_number"]
    st.rerun()


def answer_question(client, results, question):
    """Returns (answer, kind, note). kind is "answer", "blocked" or "error"."""
    asks_diagnosis = any(term in question.lower() for term in DIAGNOSIS_TERMS)
    if not live:
        if asks_diagnosis:
            return LOCAL_REFUSAL, "blocked", "Sample mode: local refusal. Connect AWS for the real Bedrock Guardrail."
        latest = results[-1]
        return (f'Latest call ({nice_date(latest["date"])}): {latest["summary"]}', "answer",
                "Sample mode: this is the latest call's summary, not a live answer.")
    if not config.GUARDRAIL_ID and asks_diagnosis:
        return LOCAL_REFUSAL, "blocked", "Local refusal: GUARDRAIL_ID isn't set, so the Bedrock Guardrail isn't connected yet."

    lines = [f"Client: {client['name']}. Advisor: {client['advisor']}. Trusted contact: {client['trusted_contact']}.",
             "Analyzed calls, oldest first:"]
    for r in results:
        if r["level"] == "unavailable":
            lines.append(f"Call {r['call_number']} ({r['date']}): not analyzed.")
            continue
        quotes = "; ".join(f'{SIGNAL_LABELS[s]} {r["signals"][s]["score"]}/3: "{r["signals"][s]["quote"]}"'
                           for s in SIGNALS if r["signals"][s]["score"] > 0) or "no signals"
        lines.append(f"Call {r['call_number']} ({r['date']}), {style_for(r['level'])['label']}, "
                     f"{r['total']}/{MAX_SCORE}. Summary: {r['summary']} Evidence: {quotes}")
    prompt = "\n".join(lines) + f"\n\nAdvisor's question: {question}"
    try:
        text = aws_clients.converse(config.SONNET_MODEL_ID, prompt, system=ASK_SYSTEM,
                                    guardrail=bool(config.GUARDRAIL_ID), guard_text=question, max_tokens=400)
    except GuardrailBlocked as blocked:
        return str(blocked), "blocked", "Blocked by the Bedrock Guardrail."
    except AWSServiceError as err:
        return scoring.friendly_error(err), "error", None
    note = None if config.GUARDRAIL_ID else "GUARDRAIL_ID isn't set, so this answer didn't go through the guardrail."
    return text, "answer", note


# ---------- Small render helpers ----------

def style_for(level):
    return LEVEL_STYLE.get(level, LEVEL_STYLE["unavailable"])


def nice_date(iso):
    try:
        d = datetime.date.fromisoformat(iso)
    except (TypeError, ValueError):
        return str(iso or "unknown date")
    return f"{d:%b} {d.day}, {d.year}"


def mark(quote, score):
    return f'<span class="sl-mark s{max(1, min(score, 3))}">“{html.escape(quote)}”</span>'


def pips(score):
    return (f'<span class="sl-pips" title="{score} of 3">' + '<i class="on"></i>' * score
            + "<i></i>" * (3 - score) + "</span>")


def strongest(result):
    flagged = [s for s in result["signals"].values() if s["score"] > 0 and s["quote"]]
    return max(flagged, key=lambda s: s["score"]) if flagged else None


def first_name(c):
    return c["name"].split()[0]


def given_name(name):
    return name.strip().split()[0].strip(".,()") if name and name.strip() else ""


def heading(text, aside=""):
    aside_html = f"<small>{aside}</small>" if aside else ""
    st.markdown(f'<div class="sl-h">{text}{aside_html}</div>', unsafe_allow_html=True)


# ---------- Load the open client (runs live analysis the first time) ----------

if st.session_state.pop("analyze_all", False):
    for cid in CLIENT_IDS:
        ensure_analyzed(cid)

client_id = st.session_state.client_id
state = ensure_analyzed(client_id)
client = state["client"]
calls, results, people = full_history(state, client_id)
selected_number = st.session_state.selected.get(client_id, results[-1]["call_number"])
selected = next((r for r in results if r["call_number"] == selected_number), results[-1])
latest = results[-1]


# ---------- Sidebar ----------

with st.sidebar:
    st.markdown('<div class="sl-wordmark">Second Look</div>', unsafe_allow_html=True)
    st.caption("Behavior change across client calls")
    st.write("")
    for cid in CLIENT_IDS:
        side_state = state if cid == client_id else base_state(cid)
        side_results = full_history(side_state, cid)[1] if side_state["results"] is not None else []
        level = side_results[-1]["level"] if side_results else "pending"
        name_col, status_col = st.columns([3, 2], vertical_alignment="center", gap="small")
        if name_col.button(side_state["client"]["name"], key=f"pick-{cid}",
                           type="secondary" if cid == client_id else "tertiary", width="stretch"):
            st.session_state.client_id = cid
            st.rerun()
        status_col.markdown(f'<div class="sl-side-status" style="color:{SIDEBAR_LEVEL_COLOR[level]}">'
                            f'{style_for(level)["label"]}</div>', unsafe_allow_html=True)
    st.divider()

    st.toggle("Live Bedrock analysis", key="use_live", disabled=not status["live"],
              help="Off: show the team's hand-written sample results without calling AWS.")
    if not status["live"]:
        st.caption(f"Live analysis is off. {status['why']}")
    st.caption("Transcripts: " + (f"S3 bucket {config.S3_BUCKET}" if state["source"] == "s3" else "local files"))
    if live:
        if st.button("Analyze all clients", width="stretch",
                     help="Runs Bedrock for any client not analyzed yet, so switching is instant."):
            st.session_state.analyze_all = True
            st.rerun()
        if st.button(f"Re-run {first_name(client)}'s analysis", width="stretch",
                     help="Scores this client's calls again with Bedrock."):
            key = store_key(client_id, state["calls"], state["source"])
            analysis_store().pop(key, None)
            st.session_state.session_analysis.pop(key, None)
            load_history.clear()
            st.rerun()
    if st.button("Reset demo", width="stretch",
                 help="Removes calls received this session (and their S3 uploads) and clears approvals."):
        for key in ("uploaded", "selected", "decisions", "answers"):
            st.session_state[key] = {}
        st.session_state.flash = ""
        if status["s3"]:
            try:
                count = aws_clients.delete_uploads(CLIENT_IDS)
                st.session_state.flash = f"Cleared {count} uploaded transcript{'s' if count != 1 else ''} from S3."
            except AWSServiceError as err:
                st.session_state.flash = str(err)
        st.rerun()
    if st.session_state.flash:
        st.caption(st.session_state.flash)
    st.caption("All clients and conversations are made up.")


# ---------- Client header ----------

if not live:
    st.info("Showing the team's hand-written sample results, not live Bedrock analysis. "
            + (status["why"] if not status["live"] else "Turn on Live Bedrock analysis in the sidebar."),
            icon=":material/info:")

latest_style = style_for(latest["level"])
if latest["level"] == "unavailable":
    standing = (f'<b>{latest_style["label"]}</b> · Call {latest["call_number"]} on '
                f'{nice_date(latest["date"])} couldn\'t be analyzed')
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

demo_call = pending_demo_call(client_id, calls)
if demo_call:
    st.write("")
    with st.container(border=True):
        msg_col, btn_col = st.columns([3, 1], vertical_alignment="center")
        msg_col.markdown(f"**{client['advisor']} just finished Call {demo_call['call_number']} with "
                         f"{first_name(client)}.** The note-taker has the transcript ready.")
        if btn_col.button(f"Receive Call {demo_call['call_number']} transcript", type="primary",
                          key="receive", width="stretch"):
            receive_call(state, client_id, demo_call, from_note_taker=True)


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
        body = '<div class="sl-quiet">Not analyzed. Read the transcript.</div>'
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
    text=[f'{nice_date(r["date"])}<br>' + (f'Score {r["total"]} of {MAX_SCORE}' if r["total"] is not None
                                           else "Not analyzed") for r in results],
    mode="lines+markers",
    line=dict(color=INK, width=2),
    marker=dict(size=12, color=[style_for(r["level"])["color"] for r in results], line=dict(width=2, color="white")),
    hovertemplate="%{x}, %{text}<extra></extra>",
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
    heading(f'What changed in Call {selected["call_number"]}', nice_date(selected["date"]))
    if selected["level"] == "unavailable":
        reason = f' Reason: {selected["error"]}' if selected.get("error") else ""
        st.warning("Analysis unavailable, review manually. No level is shown for this call, so read the "
                   f"transcript before acting.{reason}")
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
    hidden = {given_name(client["name"]).lower(), given_name(client["advisor"]).lower()}
    new_keys = {given_name(p).lower() for r in results for p in r.get("new_people") or []}
    names, display = [], {}
    for r in results:
        for p in r.get("people_mentioned") or []:
            key = given_name(p).lower()
            if key and key not in hidden and key not in display:
                names.append(key)
                display[key] = given_name(p)
    if not names:
        st.markdown('<div class="sl-quiet">No one mentioned yet.</div>', unsafe_allow_html=True)
    else:
        present = [{given_name(p).lower() for p in r.get("people_mentioned") or []} for r in results]
        header = "".join(f"<th>Call {r['call_number']}</th>" for r in results)
        rows, notes = "", []
        for key in names:
            seen_in = [r["call_number"] for r, here in zip(results, present) if key in here]
            first_seen = seen_in[0]
            is_new = key in new_keys and first_seen > results[0]["call_number"]
            if is_new:
                later = len(seen_in) - 1
                notes.append(f"{html.escape(display[key])} first appears in Call {first_seen}"
                             + (f" and comes up in {later} later call{'s' if later > 1 else ''}." if later
                                else ", mentioned once."))
            cells = ""
            for r, here in zip(results, present):
                if key not in here:
                    cells += '<td class="gap">–</td>'
                elif is_new and r["call_number"] == first_seen:
                    cells += '<td><span class="sl-new">New</span></td>'
                else:
                    cells += '<td><span class="dot"></span></td>'
            rows += f"<tr><td>{html.escape(display[key])}</td>{cells}</tr>"
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
    if steps.get("error"):
        st.caption(f"Drafting problem: {steps['error']}")
    if steps.get("advisor_script"):
        decision_row("script", "Check-in call script", steps["advisor_script"].strip('"'))
    if steps.get("trusted_contact_message"):
        decision_row("contact", f'Message to {client["trusted_contact"]}, needs compliance approval',
                     steps["trusted_contact_message"])
    if steps.get("hold_note"):
        decision_row("hold", "Possible temporary hold under FINRA Rule 2165", steps["hold_note"], quoted=False)
    if steps.get("label"):
        st.caption(steps["label"])


# ---------- Ask + manual notes ----------

ask_col, add_col = st.columns(2, gap="large")

with ask_col:
    heading(f"Ask about {first_name(client)}")
    with st.form(f"ask-{client_id}", clear_on_submit=True, border=False):
        question = st.text_input("Question", label_visibility="collapsed",
                                 placeholder=f"What changed in {first_name(client)}'s last call?")
        asked = st.form_submit_button("Ask")
    if asked and question.strip():
        with st.spinner("Asking Claude, with the guardrail checking the question and the answer"):
            st.session_state.answers[client_id] = (question.strip(), *answer_question(client, results, question.strip()))
    last = st.session_state.answers.get(client_id)
    if last:
        q, answer, kind, note = last
        st.markdown(f"**You asked:** {html.escape(q)}")
        if kind == "blocked":
            st.error(answer, icon=":material/block:")
        elif kind == "error":
            st.warning(answer)
        else:
            st.info(answer)
        if note:
            st.caption(note)

with add_col:
    heading("Add a call without a note-taker")
    pasted = st.text_area("Transcript or typed notes", height=110, label_visibility="collapsed",
                          placeholder="Paste the transcript or type your call notes")
    if st.button("Analyze notes", disabled=not pasted.strip()):
        receive_call(state, client_id, {
            "client_id": client_id,
            "call_number": max(c["call_number"] for c in calls) + 1,
            "date": datetime.date.today().isoformat(),
            "transcript": pasted.strip(),
        }, from_note_taker=False)
