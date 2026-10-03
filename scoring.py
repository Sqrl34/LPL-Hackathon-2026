"""Bedrock scoring, risk rules, and next-step drafts (owner: A).

The AI only finds evidence and quotes. apply_rules() (plain Python) decides the
color. A human decides what to do with the drafts.
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
from schema import SIGNALS

DATA_DIR = Path(__file__).parent / "data"

GREEN_MAX = 3  # total under 4 is green
RED_MIN = 8
MIN_CALLS_FOR_YELLOW = 2  # one bad call never triggers yellow by itself

UNAVAILABLE_SUMMARY = "Analysis unavailable, review manually"
APPROVAL_LABEL = "Draft, requires human approval"
NEUTRAL_LINE = (
    "Behavior in this call differs from the client's own past calls. "
    "Review the quoted evidence and consider a check-in conversation."
)
BANNED_TERMS = re.compile(
    r"dementia|alzheimer|cognitive\s+(impairment|decline)|senil|diagnos",
    re.IGNORECASE,
)
MONEY_MOVEMENT = re.compile(
    r"\b(?:wir|transfer|send|sent|withdr[ae]w|mov|liquidat)\w*|\bcash(?:ing)?\s+out\b", re.IGNORECASE
)

# Diagnosis words are kept out of the prompts on purpose: the guardrail's
# "Medical diagnosis" denied topic would otherwise block our own instructions.
RUBRIC_PROMPT = """You are reviewing a financial advisor's call transcript with a client.
Compare the NEW call to the client's PAST calls.
Do not name, suggest, or speculate about any medical or mental health condition. Describe only observable behavior.
Score each of these 5 signals from 0 (none) to 3 (strong), based only on changes compared to the client's own past behavior:
- repetition: repeating the same question or story within this call. Ignore asking once for something to be explained again. 1 = repeats something but notices or corrects it right away; 2 = asks the same question again within minutes without noticing; 3 = repeats the same question or story three or more times.
- memory_gaps: contradicting or forgetting decisions recorded in past calls, or not recognizing long-known people. Ignore forgetting exact dates.
- new_influencer: a person who was not part of the client's life in the first (baseline) call and now comes up in the client's money conversations. Keep scoring that person in every later call where their influence continues, even if they were already mentioned in an earlier call. A new name mentioned once in passing scores 0. 1 = a new person comes up more than once but has no opinions about the client's money; 2 = a new person is giving opinions or advice about the client's money or strategy, even if the client hasn't acted on it yet; 3 = a new person is directing specific money moves, handling the client's bills or accounts, or would receive money.
- out_of_character: requests that clash with the client's history and style, such as speculative investments, adding someone to the account or as beneficiary, or unusually large withdrawals. Changes with a clear normal reason (like a home repair paid to their own account) score 0 or 1.
- urgency_secrecy: pressure to act fast, or asking to hide things from family or the trusted contact. Normal deadlines like a tax bill score 0.
For every score above 0, include a short direct quote copied exactly, word for word, from the CLIENT's lines in the NEW call as evidence. Copy one continuous passage; do not join separate sentences with "...". For a score of 0, use an empty quote.
Also list the first names of every person mentioned in the NEW call (including the advisor).
Return only JSON, no other text, in this format:
{"signals": {"repetition": {"score": 0, "quote": ""}, "memory_gaps": {"score": 0, "quote": ""}, "new_influencer": {"score": 0, "quote": ""}, "out_of_character": {"score": 0, "quote": ""}, "urgency_secrecy": {"score": 0, "quote": ""}}, "people_mentioned": [], "summary": "One or two plain-language sentences on what changed versus past calls."}"""

NEXT_STEPS_PROMPT = """You help a financial advisor follow up with a client whose recent calls show warning signs of possible financial exploitation or vulnerability.
Do not name, suggest, or speculate about any medical or mental health condition, and do not comment on the client's capacity or competence. Do not accuse anyone. Keep a warm, respectful tone.
These are drafts only: never say that anything has been sent, notified, initiated, scheduled, or done.
Use only details that appear in the evidence or the client's lines. Never write placeholders or brackets such as [amount] or [name]; if a detail is unknown, say 'the requested amount' or leave it out.
Write short drafts a human will review before anything is sent:
- advisor_script: what the advisor could say on a check-in call with the client (3 to 5 sentences).
- trusted_contact_message: a brief message to the client's trusted contact asking to talk. Do not share account details or balances.
- hold_note: a short internal note to compliance describing the pending money movement and the pattern, recommending review for a possible temporary hold under FINRA Rule 2165.
Return only JSON, no other text: {"advisor_script": "", "trusted_contact_message": "", "hold_note": ""}"""


# ---------- Bedrock access ----------

try:
    from aws_clients import check_output as _shared_check_output
    from aws_clients import converse as _shared_converse
except ImportError:
    _shared_converse = None
    _shared_check_output = None

_bedrock = None
_last_call = 0.0


def _local_converse(model_id, system, prompt, guardrail=False, max_tokens=1500):
    """Fallback until aws_clients.converse exists: paced, retried, us-east-1 only."""
    global _bedrock, _last_call
    import boto3
    from botocore.exceptions import ClientError

    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)

    kwargs = {
        "modelId": model_id,
        "system": [{"text": system}],
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
    }
    if guardrail and config.GUARDRAIL_ID:
        kwargs["guardrailConfig"] = {
            "guardrailIdentifier": config.GUARDRAIL_ID,
            "guardrailVersion": config.GUARDRAIL_VERSION,
        }

    for attempt in range(5):
        wait = config.BEDROCK_PAUSE_SECONDS - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        try:
            response = _bedrock.converse(**kwargs)
            if response.get("stopReason") == "guardrail_intervened":
                raise RuntimeError("Guardrail blocked the response")
            return response["output"]["message"]["content"][0]["text"]
        except ClientError as err:
            code = err.response["Error"]["Code"]
            if code in ("ThrottlingException", "ServiceUnavailableException") and attempt < 4:
                time.sleep(2 ** (attempt + 1))
                continue
            raise


def converse(model_id, system, prompt, guardrail=False):
    if not model_id:
        raise RuntimeError("Model ID missing; set SONNET_MODEL_ID / HAIKU_MODEL_ID in .env")
    if _shared_converse is not None:
        return _shared_converse(model_id, prompt, system=system, guardrail=guardrail and bool(config.GUARDRAIL_ID),
                                max_tokens=1500)
    return _local_converse(model_id, system, prompt, guardrail=guardrail)


def guard_output(texts):
    """Check model-written text with the guardrail (as OUTPUT). Returns (texts, blocked).

    Transcripts go to the model without an input guardrail, so a client's own words can't
    get the analysis blocked. What the model writes back is what we check. If the guardrail
    can't be reached, the text falls back to the local diagnosis-word scrub.
    """
    if not config.GUARDRAIL_ID or _shared_check_output is None:
        return texts, False
    try:
        return _shared_check_output(texts)
    except Exception:
        return texts, False


# ---------- Helpers ----------

def _parse_json(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Model did not return JSON")
    return json.loads(match.group(0))


def _normalize(text):
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:\.\s*){2,}\]|\.{3,}|…)\s*")
_SPEAKER = re.compile(r"^\s*[A-Za-z][\w .'-]{0,30}:\s*")  # "Maria: " at the start of a line


def quote_in_transcript(quote, transcript):
    """True if the quote really appears in the transcript.

    Guardrail PII masking can turn part of a quote into a {PLACEHOLDER}, so
    placeholders match any text.
    """
    # A quote stitched together with "..." passes only if every piece is in the
    # transcript, in order. Pieces can't be reordered or invented.
    fragments = [f.strip(" \"'.,") for f in _ELLIPSIS.split(_normalize(quote))]
    fragments = [f for f in fragments if f]
    if not fragments:
        return False
    pattern = r".+?".join(
        r".+?".join(re.escape(p) for p in re.split(r"\{[a-z_]+\}", f)) for f in fragments
    )
    # Also match against the client's lines alone, so a quote spanning two client
    # lines with an advisor line in between still counts. Every word must be theirs.
    client_only = " ".join(
        _SPEAKER.sub("", line) for line in transcript.splitlines()
        if line.strip() and not line.lower().lstrip().startswith("advisor:")
    )
    return any(re.search(pattern, _normalize(text)) for text in (transcript, client_only))


def verify_quote(quote, transcript):
    """Return the quote if the client really said it, else "".

    Models often stitch real sentences together out of order or skip text between them.
    If every sentence is in the transcript, keep them, in the order they were said,
    with "…" between them. One invented sentence and the whole quote is rejected.
    """
    if quote_in_transcript(quote, transcript):
        return quote
    # Sentence by sentence, every one must be the client's own words (advisor lines removed).
    client_only = "\n".join(
        line for line in transcript.splitlines()
        if line.strip() and not line.lower().lstrip().startswith("advisor:")
    )
    sentences = [s.strip(" \"'") for s in re.split(r"(?<=[.!?])\s+", quote.strip(" \"'")) if s.strip(" \"'")]
    if len(sentences) < 2 or not all(quote_in_transcript(s, client_only) for s in sentences):
        return ""
    text = _normalize(client_only)

    def said_at(sentence):
        found = text.find(_ELLIPSIS.split(_normalize(sentence))[0].strip(" \"'.,"))
        return found if found >= 0 else len(text)

    ordered = sorted(dict.fromkeys(sentences), key=said_at)
    joined = ordered[0]
    for sentence in ordered[1:]:
        together = f"{joined} {sentence}"
        joined = together if quote_in_transcript(together, client_only) else f"{joined} … {sentence}"
    return joined


_DONE_CLAIM = re.compile(
    r"\b(initiated|already (?:contacted|notified|sent|called|reached)|"
    r"(?:has|have|was|were) been (?:sent|notified|contacted|scheduled|completed|placed|initiated)|"
    r"(?:notification|outreach|message|hold) (?:sent|completed|placed|scheduled))\b",
    re.IGNORECASE,
)
_CAPACITY = re.compile(r"\b(?:capacity|competen\w*)\b", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\[[^\]]*\]")


def _tidy_draft(text):
    """Drafts must not claim anything was done, or judge the client's capacity. Enforced, not just asked."""
    if not text:
        return text
    text = re.sub(r"\s+(?:and|or)\s+(?:mental\s+)?(?:capacity|competence)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:mental\s+)?(?:capacity|competence)\s+(?:and|or)\s+", "", text, flags=re.IGNORECASE)
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = [s for s in sentences
            if not _DONE_CLAIM.search(s) and not _CAPACITY.search(s) and not _PLACEHOLDER.search(s)]
    return " ".join(kept) if kept else None


def _scrub(text):
    if text and BANNED_TERMS.search(text):
        return NEUTRAL_LINE
    return text


def _empty_signals():
    return {name: {"score": 0, "quote": ""} for name in SIGNALS}


def unavailable_result(call, error=""):
    return {
        "client_id": call.get("client_id"),
        "call_number": call.get("call_number"),
        "date": call.get("date"),
        "signals": _empty_signals(),
        "people_mentioned": [],
        "new_people": [],
        "total": None,
        "level": "unavailable",
        "summary": UNAVAILABLE_SUMMARY,
        "next_steps": None,
        "error": str(error),
    }


def _first_name(name):
    return name.strip().split()[0].strip(".,()").lower() if name and name.strip() else ""


def compute_new_people(client, people_by_call):
    """Names not in the client's baseline (Call 1 plus the client file), per call.

    people_by_call is a list of name lists in call order, from Comprehend (C)
    or from the model's people_mentioned.
    """
    known = {
        _first_name(client.get("name", "")),
        _first_name(client.get("advisor", "")),
        _first_name(client.get("trusted_contact", "")),
    }
    if people_by_call:
        known |= {_first_name(p) for p in people_by_call[0]}
    result = [[]]
    for people in people_by_call[1:]:
        seen = set()
        new = []
        for p in people:
            key = _first_name(p)
            if key and key not in known and key not in seen:
                seen.add(key)
                new.append(p.strip())
        result.append(new)
    return result[: len(people_by_call)]


def recurring_new_people(new_people_by_call):
    """New names that appear in 2 or more calls (the ones worth a closer look)."""
    counts, display = {}, {}
    for people in new_people_by_call:
        for key, name in {_first_name(x): x for x in people}.items():
            counts[key] = counts.get(key, 0) + 1
            display.setdefault(key, name)
    return [display[k] for k, n in counts.items() if n >= 2]


# ---------- Scoring ----------

def _format_call(call):
    return f"--- Call {call['call_number']} ({call.get('date', 'unknown date')}) ---\n{call['transcript']}"


def build_score_prompt(new_call, past_calls, client):
    past_text = "\n\n".join(_format_call(c) for c in past_calls) or "(No past calls. This is the baseline call; score only what stands out within this call.)"
    return (
        f"CLIENT PROFILE:\nName: {client.get('name')}\nAge: {client.get('age')}\n"
        f"Trusted contact: {client.get('trusted_contact')}\nAdvisor: {client.get('advisor')}\n"
        f"Notes: {client.get('notes')}\n\n"
        f"PAST CALLS:\n{past_text}\n\n"
        f"NEW CALL:\n{_format_call(new_call)}"
    )


def _converse_json(model_id, system, prompt):
    """One retry if the model's reply isn't valid JSON."""
    try:
        return _parse_json(converse(model_id, system, prompt))
    except ValueError:  # includes json.JSONDecodeError
        return _parse_json(converse(model_id, system, prompt + "\n\nReturn only the JSON object."))


def friendly_error(err):
    # aws_clients wraps boto errors; the original (with its error code) is the cause.
    if not hasattr(err, "response") and getattr(err, "__cause__", None) is not None:
        if hasattr(err.__cause__, "response") or type(err.__cause__).__name__.endswith("CredentialsError"):
            err = err.__cause__
    code = ""
    if hasattr(err, "response"):
        code = err.response.get("Error", {}).get("Code", "")
    name = type(err).__name__
    if name in ("NoCredentialsError", "PartialCredentialsError") or code in (
        "ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId", "UnrecognizedClientException",
    ):
        return "AWS credentials missing or expired. Paste fresh event credentials (see README Going live)."
    if code == "AccessDeniedException":
        return "Access denied. Check region us-east-1 and model access for this inference profile."
    if code == "ValidationException" and "model" in str(err).lower():
        return f"Invalid model ID; use the inference profile ID. ({err})"
    if code == "ThrottlingException":
        return "Bedrock throttled. Wait a minute; only one teammate should call Bedrock at a time."
    return str(err)


def score_call(new_call, past_calls, client):
    """Ask Sonnet to score the 5 signals for new_call against past_calls.

    Returns {"signals", "people_mentioned", "summary"}. Raises on failure;
    callers turn that into an "unavailable" result.
    """
    prompt = build_score_prompt(new_call, past_calls, client)
    raw = _converse_json(config.SONNET_MODEL_ID, RUBRIC_PROMPT, prompt)

    signals = {}
    unverified = []  # scores dropped because the quote isn't in the transcript (shown by run_scoring.py)
    for name in SIGNALS:
        item = (raw.get("signals") or {}).get(name) or {}
        try:
            score = max(0, min(3, int(item.get("score", 0))))
        except (TypeError, ValueError):
            score = 0
        quote = (item.get("quote") or "").strip()
        if score > 0:
            verified = verify_quote(quote, new_call["transcript"])
            if not verified:
                unverified.append({"signal": name, "score": score, "quote": quote})
                score = 0
            quote = verified
        if score == 0:
            quote = ""
        signals[name] = {"score": score, "quote": quote}

    people = [p for p in raw.get("people_mentioned") or [] if isinstance(p, str) and p.strip()]
    summary = _scrub((raw.get("summary") or "").strip())

    # Guardrail on what the model wrote: masks account numbers/SSNs, blocks diagnosis talk.
    flagged = [name for name in SIGNALS if signals[name]["score"] > 0]
    checked, blocked = guard_output([summary] + [signals[name]["quote"] for name in flagged])
    if blocked:
        summary = NEUTRAL_LINE
    else:
        summary = checked[0]
        for name, quote in zip(flagged, checked[1:]):
            signals[name]["quote"] = quote

    return {
        "signals": signals,
        "people_mentioned": people,
        "summary": summary,
        "unverified_quotes": unverified,
    }


def _has_signal(signals):
    return any(s["score"] >= 1 for s in signals.values())


def apply_rules(signals, prior_results=()):
    """Fixed, readable rules. Returns (total, level)."""
    s = {name: signals[name]["score"] for name in SIGNALS}
    total = sum(s.values())

    if (
        total >= RED_MIN
        or (s["new_influencer"] >= 2 and s["urgency_secrecy"] >= 2)
        or (s["new_influencer"] >= 2 and s["out_of_character"] >= 2)
        or (s["memory_gaps"] >= 2 and s["urgency_secrecy"] >= 2)
    ):
        return total, "red"

    if total > GREEN_MAX:
        calls_with_signals = 1 + sum(
            1 for r in prior_results
            if r.get("level") != "unavailable" and _has_signal(r["signals"])
        )
        if calls_with_signals >= MIN_CALLS_FOR_YELLOW:
            return total, "yellow"

    return total, "green"


# ---------- Next steps ----------

def _client_lines(transcript):
    return "\n".join(
        line for line in (transcript or "").splitlines() if not line.lower().startswith("advisor:")
    )


def money_is_moving(result, transcript):
    return (result["signals"]["out_of_character"]["score"] >= 2
            and bool(MONEY_MOVEMENT.search(_client_lines(transcript))))


def draft_next_steps(result, client, transcript=""):
    """Haiku drafts for yellow/red. Nothing is ever sent automatically."""
    if result["level"] not in ("yellow", "red"):
        return None

    is_red = result["level"] == "red"
    moving = is_red and money_is_moving(result, transcript)
    evidence = "\n".join(
        f"- {name} ({sig['score']}/3): \"{sig['quote']}\""
        for name, sig in result["signals"].items() if sig["score"] > 0
    )
    prompt = (
        f"Client: {client.get('name')}\nAdvisor: {client.get('advisor')}\n"
        f"Trusted contact: {client.get('trusted_contact')}\n"
        f"Risk level: {result['level']}\n"
        f"Money movement pending: {'yes' if moving else 'no'}\n"
        f"What changed: {result['summary']}\n"
        f"Evidence from the latest call:\n{evidence}\n\n"
        f"Client's lines from the latest call:\n{_client_lines(transcript)}"
    )

    steps = {
        "label": APPROVAL_LABEL,
        "advisor_script": None,
        "trusted_contact_message": None,
        "hold_note": None,
    }
    try:
        raw = _converse_json(config.HAIKU_MODEL_ID, NEXT_STEPS_PROMPT, prompt)
    except Exception as err:
        steps["advisor_script"] = "Drafts unavailable, write the follow-up manually."
        steps["error"] = friendly_error(err)
        return steps

    steps["advisor_script"] = _tidy_draft(_scrub(raw.get("advisor_script")))
    if is_red:
        steps["trusted_contact_message"] = _tidy_draft(_scrub(raw.get("trusted_contact_message")))
    if moving:
        steps["hold_note"] = _tidy_draft(_scrub(raw.get("hold_note")))

    keys = [k for k in ("advisor_script", "trusted_contact_message", "hold_note") if steps[k]]
    checked, blocked = guard_output([steps[k] for k in keys])
    if blocked:
        steps.update({"advisor_script": "Drafts unavailable, write the follow-up manually.",
                      "trusted_contact_message": None, "hold_note": None,
                      "error": "The guardrail blocked the drafted text."})
        return steps
    for key, text in zip(keys, checked):
        steps[key] = text
    return steps


# ---------- Full analysis ----------

def analyze_new_call(client, past_calls, past_results, new_call, people_by_call=None, with_next_steps=True):
    """Analyze one call against its history. Never raises; fails to "unavailable".

    people_by_call: optional Comprehend names for past_calls + new_call, in order.
    """
    try:
        scored = score_call(new_call, past_calls, client)
    except Exception as err:
        return unavailable_result(new_call, friendly_error(err))
    result = finish_result(client, past_calls, past_results, new_call, scored, people_by_call)
    if with_next_steps:
        result["next_steps"] = draft_next_steps(result, client, new_call["transcript"])
    return result


def finish_result(client, past_calls, past_results, new_call, scored, people_by_call=None):
    """Turn score_call's output into a result: rules, new people, no drafts yet."""
    total, level = apply_rules(scored["signals"], past_results)

    if people_by_call is None:
        people_by_call = [r.get("people_mentioned", []) for r in past_results] + [scored["people_mentioned"]]
    new_people = compute_new_people(client, people_by_call)[-1] if past_calls else []

    result = {
        "client_id": new_call.get("client_id", client.get("client_id")),
        "call_number": new_call.get("call_number"),
        "date": new_call.get("date"),
        "signals": scored["signals"],
        "people_mentioned": people_by_call[-1],
        "new_people": new_people,
        "total": total,
        "level": level,
        "summary": scored["summary"],
        "next_steps": None,
        "unverified_quotes": scored.get("unverified_quotes", []),
    }
    return result


def _try_score(new_call, past_calls, client):
    try:
        return score_call(new_call, past_calls, client), None
    except Exception as err:
        return None, friendly_error(err)


def analyze_calls(client, calls, people_by_call=None, with_next_steps=True, done=(), on_result=None):
    """Analyze calls[len(done):], each against the calls before it. Never raises.

    Scoring requests overlap but each still waits its turn at the Bedrock pacing gate. The
    rules run in call order, because each call's level depends on the ones before it.
    on_result(result) is called in the caller's thread, in call order.
    """
    results = list(done)
    todo = range(len(results), len(calls))
    if not todo:
        return results
    reported = todo.start

    def report(upto, wait):
        nonlocal reported
        while reported < upto:
            job = draft_jobs.get(reported)
            if job is not None:
                if not (wait or job.done()):
                    return
                results[reported]["next_steps"] = job.result()
            if on_result:
                on_result(results[reported])
            reported += 1

    pool = ThreadPoolExecutor(max_workers=len(todo))
    draft_jobs = {}
    try:
        scoring_jobs = {i: pool.submit(_try_score, calls[i], calls[:i], client) for i in todo}
        for i in todo:
            scored, error = scoring_jobs[i].result()
            if error is not None:
                result = unavailable_result(calls[i], error)
            else:
                people = people_by_call[: i + 1] if people_by_call else None
                result = finish_result(client, calls[:i], results, calls[i], scored, people)
                if with_next_steps and result["level"] in ("yellow", "red"):
                    draft_jobs[i] = pool.submit(draft_next_steps, result, client, calls[i]["transcript"])
            results.append(result)
            report(i + 1, wait=False)
        report(len(calls), wait=True)
    finally:
        # If on_result raises (Streamlit stopping the script), don't block on requests still in flight.
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def analyze_client(client, calls, people_by_call=None, with_next_steps=True):
    """Analyze every call in order, each against the ones before it."""
    calls = sorted(calls, key=lambda c: c["call_number"])
    return analyze_calls(client, calls, people_by_call, with_next_steps)


# ---------- Local data (offline backup of the S3 bucket) ----------

def load_client_local(client_id):
    return json.loads((DATA_DIR / "clients" / f"{client_id}.json").read_text(encoding="utf-8"))


def load_calls_local(client_id, include_demo=False):
    folders = [DATA_DIR / "transcripts" / client_id]
    if include_demo:
        folders.append(DATA_DIR / "demo" / client_id)
    calls = [
        json.loads(path.read_text(encoding="utf-8"))
        for folder in folders if folder.exists()
        for path in folder.glob("call-*.json")
    ]
    return sorted(calls, key=lambda c: c["call_number"])


def load_sample_results():
    return json.loads((DATA_DIR / "sample_results.json").read_text(encoding="utf-8"))
