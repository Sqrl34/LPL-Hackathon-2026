"""Run the real Strands agent loop offline: Bedrock replies are stubbed, requests are validated."""
import json
import os
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing", AWS_SESSION_TOKEN="testing")

from botocore.stub import Stubber

import agent
import aws_clients
import config
import mock_bedrock
import scoring

agent.GUARDRAIL_ID = "gr-test"
config.S3_BUCKET = ""                      # tools read the local data/ copy
scoring.converse = mock_bedrock.fake_converse  # tool-side scoring without AWS
scoring._shared_check_output = lambda t: (t, False)
paced = []
aws_clients.wait_for_bedrock_slot = lambda: paced.append(1)
checks = 0


def check(name, cond):
    global checks
    assert cond, name
    checks += 1
    print("ok ", name)


def reply(content, stop):
    return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": stop,
            "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}, "metrics": {"latencyMs": 5}}


def run(question, responses):
    """Ask the real agent with stubbed Bedrock responses. Returns (reply, captured requests)."""
    built, tools_used = agent.build_agent()
    client = built.model.client
    sent = []
    client.meta.events.register("before-parameter-build.bedrock-runtime.Converse",
                                lambda params, **kw: sent.append(json.dumps(params, default=str)))
    original_build = agent.build_agent
    agent.build_agent = lambda: (built, tools_used)
    with Stubber(client) as stub:
        for r in responses:
            stub.add_response("converse", r) if isinstance(r, dict) else stub.add_client_error("converse", r)
        out = agent.ask("walter", question)
    agent.build_agent = original_build
    return out, sent


# 1. Tool use: model calls compare_to_baseline, gets real scoring output, then answers.
out, sent = run("What changed in Walter's last call?", [
    reply([{"toolUse": {"toolUseId": "t1", "name": "compare_to_baseline", "input": {"client_id": "walter"}}}], "tool_use"),
    reply([{"text": "Call 3 is red: \"I never agreed to rebalance anything.\""}], "end_turn"),
])
check("agent answers", "Call 3 is red" in out["answer"] and not out["blocked"] and out["error"] is None)
check("tool was called", out["tools_used"] == ["compare_to_baseline"])
first = sent[0]
check("guardrail attached to the agent's model calls", '"guardrailIdentifier": "gr-test"' in first)
check("only the question is guarded (guardContent), not tool results", '"guardContent"' in first and "What changed in Walter" in first)
second = sent[1]
tool_result_block = second.split('"toolResult"', 1)[1]
check("tool result carries the real score", "red" in tool_result_block and "rebalance" in tool_result_block)
check("tool result is not wrapped in guardContent", '"guardContent"' not in tool_result_block)
check("every model call went through the pacing gate", len(paced) == 2)

# 2. The guardrail blocks a diagnosis question.
out, _ = run("Does Walter have dementia?", [
    reply([{"text": "Second Look cannot provide medical diagnoses."}], "guardrail_intervened"),
])
check("diagnosis question blocked", out["blocked"] and "cannot provide medical" in out["answer"] and not out["tools_used"])

# 3. AWS failure fails safe, never raises.
out, _ = run("What changed?", ["AccessDeniedException"])
check("access denied -> review manually", out["answer"] == agent.UNAVAILABLE and "Access denied" in out["error"])

# 4. Tools on their own.
h = agent.get_call_history("walter")
check("get_call_history loads 3 calls", len(h["calls"]) == 3 and h["client"]["name"] == "Walter Brooks")
check("bad client id refused", "Unknown client" in agent.get_call_history("../../etc/passwd")["error"])
c2 = agent.compare_to_baseline("walter", 2)
check("compare_to_baseline call 2 is yellow", c2["level"] == "yellow" and "repetition" in c2["signals"])
d1 = agent.draft_next_steps("walter", 1)
check("green call -> no drafts", d1["level"] == "green")
d3 = agent.draft_next_steps("walter", 3)
check("red call -> drafts with approval label", d3["level"] == "red" and d3.get("advisor_script") and "approval" in d3.get("label", ""))
check("missing call handled", "No call 9" in agent.compare_to_baseline("walter", 9)["error"])

# 5. Tool failure returns the fail-safe message instead of crashing.
real = scoring.analyze_client
scoring.analyze_client = lambda *a, **k: (_ for _ in ()).throw(aws_clients.AWSServiceError("down"))
agent._analysis_cache.clear()
check("tool failure -> review manually", agent.compare_to_baseline("walter")["error"] == agent.UNAVAILABLE)
scoring.analyze_client = real

# 6. With the screen's data, tools reuse its scores and drafts: no re-scoring, no new Haiku call.
client = scoring.load_client_local("walter")
calls = scoring.load_calls_local("walter")
screen_results = [dict(r) for r in scoring.load_sample_results()["walter"]]
screen_results[2] = {**screen_results[2], "total": 99}  # marker: only the screen's copy says 99
scored = []
scoring.analyze_client = lambda *a, **k: scored.append(1) or real(*a, **k)
drafted = []
real_draft = scoring.draft_next_steps
scoring.draft_next_steps = lambda *a, **k: drafted.append(1) or real_draft(*a, **k)
seen = {}
def fake_build():
    class Fake:
        def __call__(self, prompt):
            seen["compare"] = agent.compare_to_baseline("walter", 3)
            seen["drafts"] = agent.draft_next_steps("walter", 3)
            class R:
                stop_reason = "end_turn"
                def __str__(self): return "ok"
            return R()
    return Fake(), ["compare_to_baseline", "draft_next_steps"]
original_build = agent.build_agent
agent.build_agent = fake_build
out = agent.ask("walter", "What changed?", client=client, calls=calls, results=screen_results, source="s3")
agent.build_agent = original_build
check("agent used the screen's scores", seen["compare"]["total"] == 99)
check("no re-scoring and no new Haiku call", not scored and not drafted and seen["drafts"].get("advisor_script"))
check("screen data is cleared after the question", "walter" not in agent._screen_context)
scoring.analyze_client, scoring.draft_next_steps = real, real_draft

print(f"\nAll {checks} agent checks passed.")
