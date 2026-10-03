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

import streamlit as st

import agent
import aws_clients
import config
import scoring
from aws_clients import AWSServiceError, GuardrailBlocked
from schema import SIGNALS

st.set_page_config(page_title="Second Look", page_icon=":material/manage_search:", layout="wide")

INK, GRAPHITE, RULE, LOUPE = "#173042", "#607483", "#D7E2E8", "#0067B9"
NAVY, PALE_BLUE, CANVAS = "#062E46", "#EAF4FA", "#F7F9FA"
FONT = "Arial, Helvetica, sans-serif"

CLIENT_IDS = ["walter", "maria", "linda"]

LEVEL_STYLE = {
    "green": {"color": "#16735C", "label": "Steady"},
    "yellow": {"color": "#A66B00", "label": "Watch"},
    "red": {"color": "#B33A32", "label": "Review now"},
    "unavailable": {"color": "#667985", "label": "Review manually"},
    "pending": {"color": "#667985", "label": "Not analyzed yet"},
    "analyzing": {"color": "#667985", "label": "Analyzing"},
}

SIGNAL_LABELS = {
    "repetition": "Repetition",
    "memory_gaps": "Memory gaps",
    "new_influencer": "New influencer",
    "out_of_character": "Out of character",
    "urgency_secrecy": "Urgency or secrecy",
}

# Lighter versions of the level colors, readable on the dark sidebar.
SIDEBAR_LEVEL_COLOR = {"green": "#78D1BA", "yellow": "#F3C969", "red": "#FF9D94",
                       "unavailable": "#B7C8D1", "pending": "#B7C8D1", "analyzing": "#B7C8D1"}

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
      :root {{ --ink:{INK}; --graphite:{GRAPHITE}; --rule:{RULE}; --loupe:{LOUPE};
               --navy:{NAVY}; --pale-blue:{PALE_BLUE}; --canvas:{CANVAS}; }}
      html, body, [class*="css"], .stApp, button, input, textarea, select {{
        font-family:Arial,Helvetica,sans-serif;
      }}
      code, pre, kbd, samp {{ font-family:Arial,Helvetica,sans-serif !important; }}
      h1, h2, h3, h4, h5, h6 {{ font-family:"Times New Roman",Times,serif !important; }}
      [data-testid="stAppViewContainer"] {{ background:var(--canvas); }}
      .block-container {{ padding-top:2rem; padding-bottom:4rem; max-width:1200px; }}
      [data-testid="stHeader"] {{ background:transparent; }}

      .sl-client-hero {{ background:#fff; border:1px solid var(--rule);
                         border-radius:.4rem; padding:1.35rem 1.5rem; margin-bottom:1rem; }}
      .sl-client-top {{ display:flex; align-items:flex-start; justify-content:space-between; gap:1rem; }}
      .sl-kicker {{ text-transform:uppercase; letter-spacing:.11em; color:var(--loupe);
                    font-size:.7rem; font-weight:700; margin-bottom:.32rem; }}
      .sl-name {{ font-family:"Times New Roman",Times,serif; font-size:2.35rem; font-weight:700;
                  letter-spacing:-.02em; line-height:1.05; color:var(--navy); margin:0; }}
      .sl-risk-pill {{ flex:0 0 auto; border-left:3px solid var(--lvl); color:var(--lvl);
                       padding:.2rem 0 .2rem .65rem; font-size:.8rem; font-weight:700; }}
      .sl-standing {{ font-size:.98rem; color:var(--ink); margin-top:.72rem; }}
      .sl-standing b {{ color:var(--lvl); }}
      .sl-meta {{ color:var(--graphite); margin-top:.42rem; max-width:65rem; font-size:.9rem; line-height:1.55; }}
      .sl-h {{ display:flex; align-items:baseline; flex-wrap:wrap; gap:.35rem .55rem;
               font-family:"Times New Roman",Times,serif; font-size:1.35rem; font-weight:700;
               color:var(--navy); background:#EEF4F7; border-left:3px solid var(--loupe);
               border-bottom:1px solid var(--rule); margin:1.4rem 0 .9rem; padding:.65rem .8rem; }}
      .sl-section-label {{ font-family:Arial,Helvetica,sans-serif; font-size:.66rem; line-height:1;
                           font-weight:700; text-transform:uppercase; letter-spacing:.1em;
                           color:var(--loupe); margin-right:.15rem; }}
      .sl-h small {{ font-family:Arial,Helvetica,sans-serif; font-size:.8rem; font-weight:400;
                     color:var(--graphite); letter-spacing:0; }}
      .sl-section-rule {{ height:1px; background:var(--rule); margin:2.2rem 0 .2rem; }}
      .sl-quiet {{ color:var(--graphite); }}

      [data-testid="stAlert"] {{ border-radius:.35rem; border-width:1px; }}
      [data-testid="stStatusWidget"] {{ border-radius:.4rem; }}
      [data-testid="stVerticalBlockBorderWrapper"] {{ border-color:var(--rule) !important;
                                                       border-radius:.4rem !important; }}
      .stButton > button, .stFormSubmitButton > button {{ font-weight:600; border-radius:.3rem;
                                                         min-height:2.5rem; }}
      .stTextInput input, .stTextArea textarea {{ border-radius:.3rem !important; border-color:var(--rule) !important;
                                                  background:#fff !important; }}
      .stTextInput input:focus, .stTextArea textarea:focus {{ border-color:var(--loupe) !important;
                                                             outline:1px solid var(--loupe) !important; }}

      /* The signature: quoted words get a marker stroke whose height grows with the score. */
      .sl-mark {{ font-style:italic; color:var(--ink); padding:0 .1em;
                  box-decoration-break:clone; -webkit-box-decoration-break:clone; }}
      .sl-mark.s1 {{ background:linear-gradient(transparent 72%, rgba(0,103,185,.18) 72%); }}
      .sl-mark.s2 {{ background:linear-gradient(transparent 42%, rgba(0,103,185,.19) 42%); }}
      .sl-mark.s3 {{ background:linear-gradient(rgba(0,103,185,.20), rgba(0,103,185,.20)); }}

      /* Call cards: Streamlit containers keyed call-<client>-<n>. */
      [class*="st-key-call-"] {{ position:relative; overflow:hidden; background:#fff;
                                 min-height:12rem; justify-content:space-between; cursor:pointer;
                                 transition:border-color .12s ease, background-color .12s ease; }}
      [class*="st-key-call-"]:has(button:not(:disabled)):hover {{
        border-color:var(--loupe) !important; background:#F7FBFD;
      }}
      [class*="st-key-call-"]:has(button:focus-visible) {{ outline:2px solid var(--loupe); outline-offset:2px; }}
      [class*="st-key-call-"]:has(button:disabled) {{ cursor:default; }}
      [class*="st-key-call-"] > [class*="st-key-view-"] {{ position:absolute !important; inset:0;
                                                            width:100% !important; height:100% !important;
                                                            z-index:5; margin:0 !important; }}
      [class*="st-key-call-"] [class*="st-key-view-"] .stButton,
      [class*="st-key-call-"] [class*="st-key-view-"] .stButton > div {{ width:100%; height:100%; }}
      [class*="st-key-call-"] [class*="st-key-view-"] button {{ position:absolute; inset:0; width:100%; height:100%;
                                                                 min-height:100%; opacity:0 !important;
                                                                 color:transparent !important; font-size:0 !important;
                                                                 cursor:pointer; }}
      [class*="st-key-call-"] .stButton button:disabled {{ cursor:default; }}
      .sl-call-when {{ color:var(--graphite); font-size:.78rem; font-weight:550; text-transform:uppercase;
                       letter-spacing:.04em; }}
      .sl-call-level {{ font-weight:750; color:var(--lvl); margin:.2rem 0 .65rem; }}
      .sl-call-level span {{ font-weight:400; color:var(--graphite); }}
      .sl-call-quote {{ line-height:1.55; font-size:.9rem; }}

      .sl-score-panel {{ margin-top:1rem; background:#fff; border:1px solid var(--rule); }}
      .sl-score-head {{ display:flex; align-items:center; justify-content:space-between; gap:1rem;
                        padding:.65rem .8rem; border-bottom:1px solid var(--rule); }}
      .sl-score-title {{ font-family:"Times New Roman",Times,serif; color:var(--navy);
                         font-size:1.05rem; font-weight:700; }}
      .sl-score-key {{ color:var(--graphite); font-size:.72rem; }}
      .sl-score-key b {{ font-weight:700; }}
      .sl-score-row {{ display:grid; grid-template-columns:8rem minmax(10rem,1fr) 3.2rem 6.5rem;
                       gap:.8rem; align-items:center; padding:.72rem .8rem; border-bottom:1px solid var(--rule); }}
      .sl-score-row:last-child {{ border-bottom:0; }}
      .sl-score-row.selected {{ background:#F7FBFD; }}
      .sl-score-call {{ color:var(--navy); font-weight:700; font-size:.86rem; }}
      .sl-score-date {{ color:var(--graphite); font-size:.7rem; font-weight:400; margin-top:.1rem; }}
      .sl-score-track {{ position:relative; height:.48rem; background:#E7EDF0; overflow:hidden; }}
      .sl-score-fill {{ height:100%; background:var(--lvl); min-width:0; }}
      .sl-score-value {{ color:var(--ink); font-size:.82rem; font-weight:700; text-align:right; }}
      .sl-score-status {{ color:var(--lvl); font-size:.78rem; font-weight:700; text-align:right; }}

      .sl-summary {{ font-size:1rem; line-height:1.65; max-width:42rem; margin-bottom:.75rem;
                     color:#294657; }}
      .sl-signal {{ display:grid; grid-template-columns:9.5rem 2.6rem 1fr; column-gap:1rem;
                    align-items:baseline; padding:.8rem 0; border-top:1px solid var(--rule); }}
      .sl-signal-name {{ font-weight:650; color:var(--navy); }}
      .sl-pips i {{ display:inline-block; width:.5rem; height:.85rem; margin-right:3px;
                    background:var(--rule); border-radius:2px; vertical-align:-1px; }}
      .sl-pips i.on {{ background:var(--loupe); }}
      .sl-signal-quote {{ line-height:1.55; }}
      @media (max-width:640px) {{
        .sl-client-top {{ align-items:flex-start; flex-direction:column; }}
        .sl-signal {{ grid-template-columns:1fr auto; row-gap:0.3rem; }}
        .sl-signal-quote {{ grid-column:1 / -1; }}
        .sl-name {{ font-size:2rem; }}
        .sl-score-head {{ align-items:flex-start; flex-direction:column; }}
        .sl-score-row {{ grid-template-columns:5.5rem 1fr 2.8rem; gap:.55rem; }}
        .sl-score-status {{ grid-column:2 / -1; text-align:left; margin-top:-.35rem; }}
      }}

      .sl-people {{ border-collapse:collapse; width:100%; border:1px solid var(--rule); background:#fff; }}
      .sl-people th {{ font-weight:650; font-size:.78rem; color:var(--graphite); background:#F1F6F8; }}
      .sl-people th, .sl-people td {{ border-bottom:1px solid var(--rule); padding:.65rem .45rem;
                                      text-align:center; }}
      .sl-people tr:last-child td {{ border-bottom:0; }}
      .sl-people th:first-child, .sl-people td:first-child {{ text-align:left; }}
      .sl-people .dot {{ display:inline-block; width:0.45rem; height:0.45rem; border-radius:50%;
                         background:var(--loupe); }}
      .sl-people .gap {{ color:#AEB7C4; }}
      .sl-new {{ font-weight:700; color:var(--loupe); }}
      .sl-people-note {{ color:var(--graphite); margin-top:0.6rem; font-size:0.92rem; }}

      .sl-draft {{ font-style:italic; line-height:1.6; max-width:44rem; margin:0.1rem 0 0.4rem; }}
      .sl-plain {{ line-height:1.6; max-width:44rem; margin:0.1rem 0 0.4rem; }}

      [data-testid="stSidebar"] {{ border-right:1px solid #28546C; }}
      [data-testid="stSidebar"] > div:first-child {{ padding-top:1.35rem; }}
      [data-testid="stSidebar"] .sl-wordmark {{ font-family:"Times New Roman",Times,serif;
                                                font-weight:700; font-size:1.45rem; }}
      [data-testid="stSidebar"] [class*="st-key-pick-"] button {{ justify-content:flex-start;
          padding:.38rem .65rem; border-radius:.3rem; min-height:2.45rem; border-color:transparent; }}
      [data-testid="stSidebar"] [class*="st-key-pick-"] button > div {{ justify-content:flex-start; width:100%; }}
      [data-testid="stSidebar"] [class*="st-key-pick-"] button p {{ text-align:left; }}
      [data-testid="stSidebar"] .sl-side-status {{ font-size:.76rem; font-weight:700; text-align:right;
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
st.session_state.setdefault("partial", {})           # store key -> in-progress analysis, so an interrupted run resumes
st.session_state.setdefault("selected", {})          # client_id -> selected call_number
st.session_state.setdefault("decisions", {})         # (client_id, call_number, item) -> "approved" | "dismissed"
st.session_state.setdefault("answers", {})           # client_id -> (question, answer, kind, note)
st.session_state.setdefault("flash", "")


# ---------- AWS status and data ----------

@st.cache_data(ttl=1800, show_spinner=False)
def aws_status():
    """Which live pieces work right now. Cheap checks only, no model calls."""
    if not (config.SONNET_MODEL_ID and config.HAIKU_MODEL_ID):
        return {"live": False, "s3": False, "why": "The model IDs are missing in .env."}
    try:
        aws_clients.caller_arn()
    except AWSServiceError as err:
        return {"live": False, "s3": False, "why": str(err)}
    return {"live": True, "s3": aws_clients.bucket_ready(), "why": ""}


@st.cache_data(ttl=3600, show_spinner=False)
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


@st.cache_resource
def aws_warmup():
    """Shared by every session: False until the first AWS check and S3 loads have filled the caches."""
    return {"done": False}


warm = aws_warmup()
if warm["done"]:
    status = aws_status()
else:
    # First visit after the server starts: draw from the local data/ copy with no network calls,
    # then check AWS at the end of the script.
    status = {"live": bool(config.SONNET_MODEL_ID and config.HAIKU_MODEL_ID), "s3": False,
              "why": "", "checking": True}
checking = status.get("checking", False)
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


def analyzing_result(call):
    """Placeholder shown for a call while live analysis is still running."""
    return {**scoring.unavailable_result(call), "level": "analyzing", "summary": "", "error": ""}


def run_analysis(client_id, state, box, on_call_done=None):
    """Score every call not scored yet, writing progress to box. Returns True if any call failed.

    Progress is saved after each call, so a run that Streamlit interrupts (an advisor clicks
    something mid-analysis) resumes from the last finished call instead of starting over.
    """
    client, calls = state["client"], state["calls"]
    key = store_key(client_id, calls, state["source"])
    progress = st.session_state.partial.setdefault(key, {"results": [], "people": None, "people_done": False})
    if not progress["people_done"]:
        progress["people"] = comprehend_people(calls)
        progress["people_done"] = True
    people = progress["people"]
    box.write("Found the people mentioned with Amazon Comprehend" if people is not None
              else "Comprehend unavailable, using the names Claude found")
    results = progress["results"]
    for i in range(len(results), len(calls)):
        call = calls[i]
        box.write(f"Scoring Call {call['call_number']} against the calls before it")
        result = scoring.analyze_new_call(client, calls[:i], results, call, people[: i + 1] if people else None)
        results.append(result)
        if on_call_done:
            on_call_done(result)
    failed = any(r["level"] == "unavailable" for r in results)
    entry = {"results": list(results), "people": people}
    if failed:
        st.session_state.session_analysis[key] = entry  # retried only when someone clicks Re-run
    else:
        analysis_store()[key] = entry
    st.session_state.partial.pop(key, None)
    state.update(entry)
    return failed


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


def answer_question(client, calls, results, question, source):
    """Returns (answer, kind, note). kind is "answer", "blocked" or "error".

    Live with a guardrail: the Strands agent answers, using the scores on screen. If the agent
    itself can't run, a direct guardrailed Sonnet call answers instead.
    """
    asks_diagnosis = any(term in question.lower() for term in DIAGNOSIS_TERMS)
    if not live:
        if asks_diagnosis:
            return LOCAL_REFUSAL, "blocked", "Sample mode: local refusal. Connect AWS for the real Bedrock Guardrail."
        latest = results[-1]
        return (f'Latest call ({nice_date(latest["date"])}): {latest["summary"]}', "answer",
                "Sample mode: this is the latest call's summary, not a live answer.")
    if not config.GUARDRAIL_ID and asks_diagnosis:
        return LOCAL_REFUSAL, "blocked", "Local refusal: GUARDRAIL_ID isn't set, so the Bedrock Guardrail isn't connected yet."

    if config.GUARDRAIL_ID:
        reply = agent.ask(client["client_id"], question, client=client, calls=calls, results=results, source=source)
        if reply["blocked"]:
            return reply["answer"], "blocked", "Blocked by the Bedrock Guardrail before the agent used any tools."
        if not reply["error"]:
            used = ", ".join(reply["tools_used"]) or "no tools"
            return reply["answer"], "answer", f"Answered by the Second Look agent (Strands) using: {used}."
        # The agent couldn't run; fall through to the direct guardrailed call below.

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


def heading(text, aside="", label=""):
    aside_html = f"<small>{aside}</small>" if aside else ""
    label_html = f'<span class="sl-section-label">{label}</span>' if label else ""
    st.markdown(f'<div class="sl-h">{label_html}<span>{text}</span>{aside_html}</div>',
                unsafe_allow_html=True)


def section_rule():
    st.markdown('<div class="sl-section-rule"></div>', unsafe_allow_html=True)


def select_call(selected_client_id, call_number):
    """Select the exact call bound to a card's stable Streamlit key."""
    st.session_state.selected[selected_client_id] = call_number


# ---------- Load the open client (live analysis runs at the end of the script) ----------

analyze_all = st.session_state.pop("analyze_all", False)
client_id = st.session_state.client_id
state = base_state(client_id)
analyzing = state["results"] is None
if analyzing:
    done = st.session_state.partial.get(store_key(client_id, state["calls"], state["source"]), {}).get("results", [])
    state["results"] = list(done) + [analyzing_result(c) for c in state["calls"][len(done):]]
busy = analyzing or checking
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
    if checking:
        st.caption("Connecting to AWS…")
    else:
        st.caption("Transcripts: " + (f"S3 bucket {config.S3_BUCKET}" if state["source"] == "s3" else "local files"))
    if live:
        if st.button("Analyze all clients", width="stretch", disabled=checking,
                     help="Runs Bedrock for any client not analyzed yet, so switching is instant."):
            st.session_state.analyze_all = True
            st.rerun()
        if st.button(f"Re-run {first_name(client)}'s analysis", width="stretch", disabled=busy,
                     help="Scores this client's calls again with Bedrock."):
            key = store_key(client_id, state["calls"], state["source"])
            analysis_store().pop(key, None)
            st.session_state.session_analysis.pop(key, None)
            st.session_state.partial.pop(key, None)
            load_history.clear()
            st.rerun()
    if st.button("Reset demo", width="stretch", disabled=checking,
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
if analyzing:
    standing = f'<b>{style_for("analyzing")["label"]}</b> · scoring {len(calls)} calls with Bedrock'
elif latest["level"] == "unavailable":
    standing = (f'<b>{latest_style["label"]}</b> · Call {latest["call_number"]} on '
                f'{nice_date(latest["date"])} couldn\'t be analyzed')
else:
    standing = (f'<b>{latest_style["label"]}</b> · Call {latest["call_number"]} on {nice_date(latest["date"])} '
                f'scored {latest["total"]} of {MAX_SCORE}')
st.markdown(
    f'<div class="sl-client-hero" style="--lvl:{latest_style["color"]}">'
    f'<div class="sl-client-top"><div><div class="sl-kicker">Client monitoring</div>'
    f'<div class="sl-name">{html.escape(client["name"])}</div></div>'
    f'<div class="sl-risk-pill">{latest_style["label"]}</div></div>'
    f'<div class="sl-standing">{standing}</div>'
    f'<div class="sl-meta">Advisor {html.escape(client["advisor"])} &nbsp;·&nbsp; Trusted contact '
    f'{html.escape(client["trusted_contact"])} &nbsp;·&nbsp; Age {client["age"]}'
    f'<br>{html.escape(client["notes"])}</div></div>',
    unsafe_allow_html=True,
)
progress_slot = st.empty()


# ---------- New call from the note-taker ----------

demo_call = pending_demo_call(client_id, calls)
if demo_call:
    st.write("")
    with st.container(border=True):
        msg_col, btn_col = st.columns([3, 1], vertical_alignment="center")
        msg_col.markdown(f"**{client['advisor']} just finished Call {demo_call['call_number']} with "
                         f"{first_name(client)}.** The note-taker has the transcript ready.")
        if btn_col.button(f"Receive Call {demo_call['call_number']} transcript", type="primary",
                          key="receive", width="stretch", disabled=busy):
            receive_call(state, client_id, demo_call, from_note_taker=True)


# ---------- Calls ----------

section_rule()
heading("Calls", f"compared only with {first_name(client)}'s own earlier calls", "Timeline")

selected_key = f"call-{client_id}-{selected['call_number']}"
st.markdown(f"<style>.st-key-{selected_key} {{ border:2px solid {LOUPE} !important; }}</style>",
            unsafe_allow_html=True)

def call_card_html(r):
    s = style_for(r["level"])
    top = strongest(r)
    if r["level"] == "analyzing":
        body = '<div class="sl-quiet">Analyzing with Bedrock…</div>'
        score = ""
    elif r["level"] == "unavailable":
        body = '<div class="sl-quiet">Not analyzed. Read the transcript.</div>'
        score = ""
    else:
        body = (f'<div class="sl-call-quote">{mark(top["quote"], top["score"])}</div>' if top
                else '<div class="sl-quiet">Nothing flagged.</div>')
        score = f'<span> · {r["total"]} of {MAX_SCORE}</span>'
    return (f'<div class="sl-call-when">Call {r["call_number"]} · {nice_date(r["date"])}</div>'
            f'<div class="sl-call-level" style="--lvl:{s["color"]}">{s["label"]}{score}</div>{body}')


card_slots = {}
cols = st.columns(len(results))
for col, r in zip(cols, results):
    is_selected = r["call_number"] == selected["call_number"]
    with col:
        with st.container(border=True, key=f"call-{client_id}-{r['call_number']}"):
            card_slots[r["call_number"]] = st.empty()
            card_slots[r["call_number"]].markdown(call_card_html(r), unsafe_allow_html=True)
            st.button(
                "\u200b",
                key=f"view-{client_id}-{r['call_number']}",
                disabled=is_selected,
                type="tertiary",
                on_click=select_call,
                args=(client_id, r["call_number"]),
            )

score_rows = ""
for r in results:
    total = r["total"]
    width = 0 if total is None else max(0, min(100, total / MAX_SCORE * 100))
    score_text = "—" if total is None else f"{total}/{MAX_SCORE}"
    style = style_for(r["level"])
    selected_class = " selected" if r["call_number"] == selected["call_number"] else ""
    score_rows += (
        f'<div class="sl-score-row{selected_class}" style="--lvl:{style["color"]}">'
        f'<div><div class="sl-score-call">Call {r["call_number"]}</div>'
        f'<div class="sl-score-date">{nice_date(r["date"])}</div></div>'
        f'<div class="sl-score-track"><div class="sl-score-fill" style="width:{width:.1f}%"></div></div>'
        f'<div class="sl-score-value">{score_text}</div>'
        f'<div class="sl-score-status">{style["label"]}</div></div>'
    )

st.markdown(
    '<div class="sl-score-panel"><div class="sl-score-head">'
    '<div class="sl-score-title">Score progression</div>'
    f'<div class="sl-score-key"><b>Watch</b> {YELLOW_LINE}+ &nbsp; · &nbsp; '
    f'<b>Review now</b> {RED_LINE}+</div></div>{score_rows}</div>',
    unsafe_allow_html=True,
)


# ---------- Evidence for the selected call + people ----------

section_rule()
detail_col, people_col = st.columns([3, 2], gap="large")

with detail_col:
    heading(f'What changed in Call {selected["call_number"]}', nice_date(selected["date"]), "Evidence")
    if selected["level"] == "analyzing":
        st.markdown('<div class="sl-quiet">Analyzing this call…</div>', unsafe_allow_html=True)
    elif selected["level"] == "unavailable":
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
    heading(f"People {first_name(client)} mentions", label="Context")
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
    section_rule()
    heading("Suggested next steps", f"nothing is sent until {html.escape(client['advisor'])} approves it",
            "Actions")
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

section_rule()
ask_col, add_col = st.columns(2, gap="large")

with ask_col:
    heading(f"Ask about {first_name(client)}", label="Advisor tools")
    with st.form(f"ask-{client_id}", clear_on_submit=True, border=False):
        question = st.text_input("Question", label_visibility="collapsed",
                                 placeholder=f"What changed in {first_name(client)}'s last call?")
        asked = st.form_submit_button("Ask", disabled=busy)
    if asked and question.strip():
        with st.spinner("The Second Look agent is checking the calls (the guardrail checks the question and the answer)"):
            st.session_state.answers[client_id] = (
                question.strip(), *answer_question(client, calls, results, question.strip(), state["source"]))
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
    heading("Add a call without a note-taker", label="Input")
    pasted = st.text_area("Transcript or typed notes", height=110, label_visibility="collapsed",
                          placeholder="Paste the transcript or type your call notes")
    if st.button("Analyze notes", disabled=busy or not pasted.strip()):
        receive_call(state, client_id, {
            "client_id": client_id,
            "call_number": max(c["call_number"] for c in calls) + 1,
            "date": datetime.date.today().isoformat(),
            "transcript": pasted.strip(),
        }, from_note_taker=False)


# ---------- AWS check and live analysis, after the page is drawn ----------

if checking:
    with progress_slot.container():
        with st.status("Connecting to AWS…", expanded=False):
            real = aws_status()
            if real["s3"]:
                for cid in CLIENT_IDS:
                    load_history(cid, True)
    warm["done"] = True
    st.rerun()

to_analyze = [client_id] if analyzing else []
if analyze_all and live:
    to_analyze += [cid for cid in CLIENT_IDS if cid != client_id and base_state(cid)["results"] is None]
if to_analyze:
    if len(to_analyze) == 1:
        only = base_state(to_analyze[0])
        label = f"Analyzing {only['client']['name']}'s {len(only['calls'])} calls with Bedrock"
    else:
        label = f"Analyzing {len(to_analyze)} clients with Bedrock"
    with progress_slot.container():
        with st.status(label, expanded=True) as box:
            failed = False
            for cid in to_analyze:
                is_open = cid == client_id
                cstate = state if is_open else base_state(cid)
                if len(to_analyze) > 1:
                    box.write(f"**{cstate['client']['name']}**")
                on_done = (lambda r: card_slots[r["call_number"]].markdown(call_card_html(r), unsafe_allow_html=True)
                           ) if is_open else None
                failed = run_analysis(cid, cstate, box, on_done) or failed
            box.update(label="Some calls couldn't be analyzed" if failed else "Analysis complete",
                       state="error" if failed else "complete")
    st.rerun()
