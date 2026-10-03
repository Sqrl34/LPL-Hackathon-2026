"""Click through app.py in sample mode (no AWS) or simulated live mode (all AWS calls faked)."""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
os.chdir(ROOT)
sys.path.insert(0, ROOT)
MODE = sys.argv[1]

from streamlit.testing.v1 import AppTest

import agent
import aws_clients
import config
import mock_bedrock
import scoring

calls_log = {"bedrock": 0, "saved": [], "deleted": 0, "comprehend": 0}

if MODE == "live":
    config.GUARDRAIL_ID = "gr-test"
    aws_clients.caller_arn = lambda: "arn:aws:sts::1:assumed-role/test"
    aws_clients.bucket_ready = lambda: True
    aws_clients.load_client = lambda cid: scoring.load_client_local(cid)
    aws_clients.load_transcripts = lambda cid, include_uploads=True: scoring.load_calls_local(cid)
    aws_clients.save_upload = lambda t: calls_log["saved"].append((t["client_id"], t["call_number"])) or f"uploads/{t['client_id']}/call-{t['call_number']}.json"
    def fake_delete(ids):
        calls_log["deleted"] += 1
        return len(calls_log["saved"])
    aws_clients.delete_uploads = fake_delete
    NAMES = {"Sarah", "Walter", "Ellen", "Daniel", "Maria", "Diego", "Ray", "James", "Linda", "Emma", "Jack", "Michael", "Gloria"}
    def fake_people(text):
        calls_log["comprehend"] += 1
        found = []
        for w in re.findall(r"[A-Z][a-z]+", text):
            if w in NAMES and w not in found:
                found.append(w)
        return found
    aws_clients.extract_people = fake_people
    def counting_converse(*a, **k):
        calls_log["bedrock"] += 1
        return mock_bedrock.fake_converse(*a, **k)
    scoring.converse = counting_converse
    scoring._shared_check_output = lambda t: (t, False)
    def fake_ask(model_id, prompt, *, system=None, guardrail=False, guard_text=None, max_tokens=1200):
        assert guardrail and guard_text and system, "Ask must use the guardrail, a system prompt and guard_text"
        if "dementia" in guard_text.lower():
            raise aws_clients.GuardrailBlocked("Second Look cannot provide medical diagnoses.")
        assert "Call 1" in prompt, "Ask must include the call evidence"
        return "Daniel appears from Call 2 on and pushes a $60,000 wire."
    aws_clients.converse = fake_ask  # the direct path, used only if the agent can't run

    agent_calls = []

    def fake_agent_ask(client_id, question, *, client=None, calls=None, results=None, source="screen"):
        agent_calls.append({"client_id": client_id, "calls": calls, "results": results})
        assert client and calls and results and len(calls) == len(results), "agent must get the screen's data"
        if "dementia" in question.lower():
            return {"answer": "Second Look cannot provide medical diagnoses.", "blocked": True,
                    "tools_used": [], "error": None}
        return {"answer": "Daniel appears from Call 2 on and pushes a $60,000 wire.", "blocked": False,
                "tools_used": ["get_call_history", "compare_to_baseline"], "error": None}
    agent.ask = fake_agent_ask

at = AppTest.from_file(f"{ROOT}/app.py", default_timeout=60).run()


def no_error(step):
    assert not at.exception, f"{step}: {at.exception}"
    print("ok ", step)


def labels():
    return [b.label for b in at.button]


def text_of(elements):
    return " | ".join(e.value for e in elements)


def side_status():
    return [m.value for m in at.sidebar.markdown if "sl-side-status" in m.value]


no_error("first load")
if MODE == "sample":
    assert "hand-written sample results" in text_of(at.info), "sample banner missing"
    print("ok  sample banner shown:", at.info[0].value[:90])
else:
    assert not any("hand-written" in i.value for i in at.info), "sample banner shown in live mode"
    n = calls_log["bedrock"]
    print(f"ok  live analysis ran: {n} Bedrock calls, {calls_log['comprehend']} Comprehend calls")
    at.run()
    assert calls_log["bedrock"] == n, "a plain rerun called Bedrock again (cache broken)"
    print("ok  rerun made no new Bedrock calls")
    st = side_status()
    assert "Review now" in st[0] and "Not analyzed yet" in st[1] and "Not analyzed yet" in st[2], st
    print("ok  sidebar: Walter analyzed, others pending")

cards = [m.value for m in at.markdown if m.value.startswith("<div class=\"sl-call-when\"")]
assert len(cards) == 3 and "Steady" in cards[0] and "Watch" in cards[1] and "Review now" in cards[2], cards
print("ok  Walter calls: Steady, Watch, Review now")
people = next(m.value for m in at.markdown if m.value.startswith("<table class=\"sl-people\""))
assert "Daniel" in people and ">New<" in people and "Sarah" not in people and "Walter" not in people, people
print("ok  people panel: Daniel new, advisor and client hidden")

at.button(key="view-walter-2").click().run(); no_error("open Call 2")
assert any("What changed in Call 2" in m.value for m in at.markdown)
assert "Approve" in labels(), "yellow call should show next steps"
[b for b in at.button if b.label == "Approve"][0].click().run(); no_error("approve")
assert "Approved" in text_of(at.success)

at.button(key="receive").click().run(); no_error("receive Call 4")
cards = [m.value for m in at.markdown if m.value.startswith("<div class=\"sl-call-when\"")]
assert len(cards) == 4 and "Review now" in cards[3] and "13 of 15" in cards[3], cards[-1]
assert any("What changed in Call 4" in m.value for m in at.markdown)
drafts = [m.value for m in at.markdown if "Possible temporary hold" in m.value or "Message to Ellen" in m.value]
assert len(drafts) == 2, "Call 4 should have trusted contact message and hold note"
assert "receive" not in [b.key for b in at.button], "receive button should disappear after upload"
print("ok  Call 4 is red 13/15 with contact message and hold note")
if MODE == "live":
    assert calls_log["saved"] == [("walter", 4)], calls_log["saved"]
    print("ok  Call 4 saved to S3 uploads")

at.text_input[0].input("Does Walter have dementia?")
at.button[[b.label for b in at.button].index("Ask")].click().run(); no_error("ask diagnosis question")
assert at.error and ("cannot provide medical" in at.error[0].value.lower() or "can't assess medical" in at.error[0].value.lower()), text_of(at.error)
print("ok  diagnosis question refused:", at.error[0].value[:60], "|", text_of(at.caption)[-80:])
if MODE == "live":
    at.text_input[0].input("Who is Daniel?")
    at.button[[b.label for b in at.button].index("Ask")].click().run(); no_error("ask normal question")
    assert "Daniel appears" in text_of(at.info), text_of(at.info)
    assert "Second Look agent" in text_of(at.caption) and "compare_to_baseline" in text_of(at.caption)
    assert len(agent_calls[-1]["results"]) == 4, "agent should see all 4 calls on screen, including Call 4"
    print("ok  normal question answered by the agent with the screen's 4 calls:", text_of(at.caption)[:70])

if MODE == "live":
    # An interrupted first load resumes: Maria's Call 1 is already done, so only Calls 2-4 are scored.
    maria_calls = scoring.load_calls_local("maria")
    digest = hashlib.sha256(json.dumps(maria_calls, sort_keys=True).encode()).hexdigest()[:16]
    at.session_state["partial"] = {("maria", "s3", digest): {
        "results": [scoring.load_sample_results()["maria"][0]], "people": None, "people_done": True}}
    scored = []
    real_analyze = scoring.analyze_new_call
    scoring.analyze_new_call = lambda client, past, results, call, *a, **k: (
        scored.append(call["call_number"]) or real_analyze(client, past, results, call, *a, **k))
    at.button(key="pick-maria").click().run(); no_error("open Maria mid-analysis")
    scoring.analyze_new_call = real_analyze
    assert scored == [2, 3, 4], scored
    assert not at.session_state["partial"], "partial progress should be cleared once stored"
    print("ok  interrupted analysis resumed from Call 2")

    at.button[labels().index("Analyze all clients")].click().run(); no_error("analyze all clients")
    st = side_status()
    assert "Not analyzed yet" not in " ".join(st), st
    print("ok  all clients analyzed:", [re.sub("<[^>]+>", "", s) for s in st])

at.button(key="pick-linda").click().run(); no_error("open Linda")
cards = [m.value for m in at.markdown if m.value.startswith("<div class=\"sl-call-when\"")]
assert len(cards) == 4 and all("Steady" in c for c in cards), cards
assert "Approve" not in labels(), "green client should have no next steps"
print("ok  Linda: all 4 calls Steady, no next steps")

at.text_area[0].input("Advisor: Hi Linda.\nLinda: Hello! The garden is lovely this year.").run()
at.button[labels().index("Analyze notes")].click().run(); no_error("paste notes")
assert any("Analysis unavailable, review manually" in w.value for w in at.warning), text_of(at.warning)
cards = [m.value for m in at.markdown if m.value.startswith("<div class=\"sl-call-when\"")]
assert len(cards) == 5 and "Review manually" in cards[4], cards[-1]
print("ok  unscorable notes fail safe to 'Review manually', never green:", at.warning[0].value[-70:])

at.button(key="pick-maria").click().run(); no_error("open Maria")
cards = [m.value for m in at.markdown if m.value.startswith("<div class=\"sl-call-when\"")]
assert [("Review now" in c) for c in cards] == [False, False, True, True], cards
print("ok  Maria: red by Call 3")

at.button[labels().index("Reset demo")].click().run(); no_error("reset demo")
at.button(key="pick-walter").click().run(); no_error("back to Walter")
assert "receive" in [b.key for b in at.button], "receive button should be back after reset"
assert len([m for m in at.markdown if m.value.startswith("<div class=\"sl-call-when\"")]) == 3
if MODE == "live":
    assert calls_log["deleted"] == 1
    print("ok  reset cleared S3 uploads:", text_of(at.sidebar.caption))
    at.toggle[0].set_value(False).run(); no_error("switch to sample mode")
    assert "hand-written sample results" in text_of(at.info)
    print("ok  toggling live off shows the sample banner")
print(f"\n{MODE.upper()} MODE: all checks passed")
