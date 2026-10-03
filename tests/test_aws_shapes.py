"""Validate every AWS request the new code builds against botocore's real API models (no network)."""
import os
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing", AWS_SESSION_TOKEN="testing")

import boto3
from botocore.stub import ANY, Stubber

import aws_clients
import aws_setup
import config
import scoring

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1
    print("ok ", name)


rt = boto3.client("bedrock-runtime", region_name="us-east-1")
aws_clients._clients["bedrock-runtime"] = rt
aws_clients.BEDROCK_PAUSE_SECONDS = 0
aws_clients.GUARDRAIL_ID, aws_clients.GUARDRAIL_VERSION = "gr123", "DRAFT"

# 1. converse with system prompt, guardrail and guardContent -> request shape is valid
with Stubber(rt) as stub:
    stub.add_response("converse", {
        "output": {"message": {"role": "assistant", "content": [{"text": "hello"}]}},
        "stopReason": "end_turn", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1}}, {
        "modelId": "m", "system": [{"text": "sys"}],
        "messages": [{"role": "user", "content": [{"text": "evidence + q"}, {"guardContent": {"text": {"text": "q"}}}]}],
        "inferenceConfig": {"maxTokens": 50, "temperature": 0},
        "guardrailConfig": {"guardrailIdentifier": "gr123", "guardrailVersion": "DRAFT"}})
    check("converse returns text", aws_clients.converse("m", "evidence + q", system="sys", guardrail=True,
                                                         guard_text="q", max_tokens=50) == "hello")

# 2. guardrail_intervened -> GuardrailBlocked carrying the guardrail's message
with Stubber(rt) as stub:
    stub.add_response("converse", {
        "output": {"message": {"role": "assistant", "content": [{"text": "Cannot diagnose."}]}},
        "stopReason": "guardrail_intervened", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1}})
    try:
        aws_clients.converse("m", "Does Walter have dementia?", guardrail=True, guard_text="Does Walter have dementia?")
        check("blocked raises", False)
    except aws_clients.GuardrailBlocked as b:
        check("blocked raises GuardrailBlocked with message", str(b) == "Cannot diagnose.")

# 3. check_output: NONE, ANONYMIZED (split back into parts), BLOCKED
texts = ["Summary here.", "The account number is 000-123456-00."]
joined = aws_clients._SPLIT.join(texts)
req = {"guardrailIdentifier": "gr123", "guardrailVersion": "DRAFT", "source": "OUTPUT",
       "content": [{"text": {"text": joined}}]}
base = {"usage": {"topicPolicyUnits": 1, "contentPolicyUnits": 0, "wordPolicyUnits": 0,
                  "sensitiveInformationPolicyUnits": 1, "sensitiveInformationPolicyFreeUnits": 0,
                  "contextualGroundingPolicyUnits": 0}}
with Stubber(rt) as stub:
    stub.add_response("apply_guardrail", {**base, "action": "NONE", "outputs": [], "assessments": []}, req)
    check("check_output none", aws_clients.check_output(texts) == (texts, False))
masked = joined.replace("000-123456-00", "{US_BANK_ACCOUNT_NUMBER}")
with Stubber(rt) as stub:
    stub.add_response("apply_guardrail", {**base, "action": "GUARDRAIL_INTERVENED", "outputs": [{"text": masked}],
        "assessments": [{"sensitiveInformationPolicy": {"piiEntities": [
            {"match": "000-123456-00", "type": "US_BANK_ACCOUNT_NUMBER", "action": "ANONYMIZED"}], "regexes": []}}]}, req)
    out, blocked = aws_clients.check_output(texts)
    check("check_output masks and splits", not blocked and out == ["Summary here.", "The account number is {US_BANK_ACCOUNT_NUMBER}."])
with Stubber(rt) as stub:
    stub.add_response("apply_guardrail", {**base, "action": "GUARDRAIL_INTERVENED", "outputs": [{"text": "Cannot diagnose."}],
        "assessments": [{"topicPolicy": {"topics": [{"name": "Medical diagnosis", "type": "DENY", "action": "BLOCKED"}]}}]}, req)
    check("check_output blocked", aws_clients.check_output(texts) == (texts, True))

# 4. scoring uses the new path: masked quote still passes quote_in_transcript, blocked summary -> neutral
config.GUARDRAIL_ID = "gr123"
calls = scoring.load_calls_local("walter", include_demo=True)
client = scoring.load_client_local("walter")
call4 = calls[3]
fake_json = ('{"signals": {"repetition": {"score": 0, "quote": ""}, "memory_gaps": {"score": 0, "quote": ""},'
             ' "new_influencer": {"score": 3, "quote": "It goes to his account, he\'s handling it for me. The account number is 000-123456-00."},'
             ' "out_of_character": {"score": 0, "quote": ""}, "urgency_secrecy": {"score": 0, "quote": ""}},'
             ' "people_mentioned": ["Sarah", "Daniel"], "summary": "Daniel is directing a wire."}')
seen = {}
def fake_shared_converse(model_id, prompt, *, system=None, guardrail=False, max_tokens=1200):
    seen.update(model_id=model_id, system=system, guardrail=guardrail, max_tokens=max_tokens)
    return fake_json
scoring._shared_converse = fake_shared_converse
scoring._shared_check_output = lambda t: ([x.replace("000-123456-00", "{US_BANK_ACCOUNT_NUMBER}") for x in t], False)
scored = scoring.score_call(call4, calls[:3], client)
check("scoring passes system prompt, no input guardrail", seen["system"] == scoring.RUBRIC_PROMPT and seen["guardrail"] is False)
check("quote kept and masked", scored["signals"]["new_influencer"]["score"] == 3
      and "{US_BANK_ACCOUNT_NUMBER}" in scored["signals"]["new_influencer"]["quote"])
scoring._shared_check_output = lambda t: (t, True)
check("blocked summary replaced", scoring.score_call(call4, calls[:3], client)["summary"] == scoring.NEUTRAL_LINE)
scoring._shared_check_output = lambda t: (_ for _ in ()).throw(aws_clients.AWSServiceError("down"))
check("guardrail outage falls back to text", scoring.score_call(call4, calls[:3], client)["summary"] == "Daniel is directing a wire.")

# 5. drafts: blocked -> manual message
def fake_drafts(model_id, prompt, *, system=None, guardrail=False, max_tokens=1200):
    return '{"advisor_script": "a", "trusted_contact_message": "b", "hold_note": "c"}'
scoring._shared_converse = fake_drafts
scoring._shared_check_output = lambda t: (t, True)
result = {"level": "red", "summary": "s", "signals": {s: {"score": 2 if s == "out_of_character" else 0, "quote": "wire"} for s in scoring.SIGNALS}}
steps = scoring.draft_next_steps(result, client, "Walter: I need it wired today.")
check("blocked drafts -> manual", steps["advisor_script"].startswith("Drafts unavailable") and steps["hold_note"] is None)
scoring._shared_check_output = lambda t: (t, False)
steps = scoring.draft_next_steps(result, client, "Walter: I need it wired today.")
check("red + money moving -> all three drafts", (steps["advisor_script"], steps["trusted_contact_message"], steps["hold_note"]) == ("a", "b", "c"))

# 5b. drafts see the client's lines (with the amount) and never show placeholders
draft_prompts = []
def fake_placeholder_drafts(model_id, prompt, *, system=None, guardrail=False, max_tokens=1200):
    draft_prompts.append(prompt)
    return ('{"advisor_script": "a", "trusted_contact_message": "b",'
            ' "hold_note": "Pending wire (~$[amount]) to an agent. Recommend review."}')
scoring._shared_converse = fake_placeholder_drafts
transcript = "Advisor: Is the wire going to James?\nMaria: I need to wire $45,000 today."
steps = scoring.draft_next_steps(result, client, transcript)
check("draft prompt has the client's amount", "Maria: I need to wire $45,000 today." in draft_prompts[-1])
check("draft prompt leaves out advisor lines", "Is the wire going to James?" not in draft_prompts[-1])
check("placeholder sentence dropped", steps["hold_note"] == "Recommend review.")
moving = {"signals": {"out_of_character": {"score": 2}}}
check("moving counts as money movement", scoring.money_is_moving(moving, "Maria: I'm moving everything to CoinVaultX."))
check("cash out counts as money movement", scoring.money_is_moving(moving, "Maria: I want to cash out."))
check("advisor-only wire doesn't count", not scoring.money_is_moving(moving, "Advisor: Did you wire anything?\nMaria: No."))
config.GUARDRAIL_ID = ""

# 5c. parallel analysis keeps the in-order rules (2-call yellow) and reports calls in order
import mock_bedrock
real_converse = scoring.converse
scoring.converse = mock_bedrock.fake_converse
scoring._shared_check_output = lambda t: (t, False)
reported = []
levels = [r["level"] for r in scoring.analyze_calls(client, calls[:3], on_result=lambda r: reported.append(r["call_number"]))]
check("parallel analysis: green, yellow, red", levels == ["green", "yellow", "red"])
check("parallel analysis reports calls in order", reported == [1, 2, 3])
check("parallel analysis drafts flagged calls", all(r["next_steps"] for r in scoring.analyze_calls(client, calls[:3])[1:]))
scoring.converse = real_converse

# 6. friendly_error sees through aws_clients' wrapper
from botocore.exceptions import ClientError
inner = ClientError({"Error": {"Code": "ExpiredTokenException", "Message": "x"}}, "Converse")
try:
    raise aws_clients.AWSServiceError("Analysis unavailable, review manually.") from inner
except aws_clients.AWSServiceError as wrapped:
    check("friendly_error unwraps expired token", "expired" in scoring.friendly_error(wrapped).lower())

# 7. create_guardrail request matches the API model (and the shell script's settings)
bedrock = boto3.client("bedrock", region_name="us-east-1")
orig = boto3.client
aws_setup.boto3.client = lambda *a, **k: bedrock
with Stubber(bedrock) as stub:
    stub.add_response("list_guardrails", {"guardrails": []}, {})
    stub.add_response("create_guardrail", {"guardrailId": "abc123", "guardrailArn": "arn:aws:bedrock:us-east-1:1:guardrail/abc123",
                                            "version": "DRAFT", "createdAt": "2026-10-02T00:00:00Z"}, None)
    check("ensure_guardrail creates", aws_setup.ensure_guardrail() == "abc123")
with Stubber(bedrock) as stub:
    stub.add_response("list_guardrails", {"guardrails": [{"id": "abc123", "arn": "arn:aws:bedrock:us-east-1:1:guardrail/abc123",
        "status": "READY", "name": "SecondLookSafety", "version": "DRAFT", "createdAt": "2026-10-02T00:00:00Z",
        "updatedAt": "2026-10-02T00:00:00Z"}]}, {})
    check("ensure_guardrail reuses existing", aws_setup.ensure_guardrail() == "abc123")
aws_setup.boto3.client = orig

# 8. delete_uploads request shapes
s3 = boto3.client("s3", region_name="us-east-1")
aws_clients._clients["s3"] = s3
aws_clients.S3_BUCKET = "second-look-test"
with Stubber(s3) as stub:
    for cid in ("walter", "maria"):
        contents = [{"Key": "uploads/walter/call-4.json"}] if cid == "walter" else []
        stub.add_response("list_objects_v2", {"Contents": contents, "IsTruncated": False}, {"Bucket": "second-look-test", "Prefix": f"uploads/{cid}/"})
        if contents:
            stub.add_response("delete_object", {}, {"Bucket": "second-look-test", "Key": "uploads/walter/call-4.json"})
    check("delete_uploads deletes walter call 4", aws_clients.delete_uploads(["walter", "maria"]) == 1)

print(f"\nAll {ok} checks passed.")
