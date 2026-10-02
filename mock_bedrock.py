"""Offline stand-in for Bedrock, used only by `run_scoring.py --mock`.

Replays data/sample_results.json so the full pipeline (prompt building, JSON
parsing, quote checks, rules, drafts) can be tested without AWS. Never import
this from scoring.py or app.py.
"""

import json
import re

import scoring


def _client_ids_by_name():
    return {
        json.loads(p.read_text(encoding="utf-8"))["name"]: p.stem
        for p in (scoring.DATA_DIR / "clients").glob("*.json")
    }


def _sample_for(client_id, call_number):
    samples = scoring.load_sample_results()
    candidates = list(samples.get(client_id, []))
    if f"{client_id}_demo_call_4" in samples:
        candidates.append(samples[f"{client_id}_demo_call_4"])
    for result in candidates:
        if result["call_number"] == call_number:
            return result
    raise ValueError(f"No sample result for {client_id} call {call_number}")


def fake_converse(model_id, system, prompt, guardrail=False):
    if system == scoring.NEXT_STEPS_PROMPT:
        name = re.search(r"^Client: (.+)$", prompt, re.MULTILINE).group(1)
        first = name.split()[0]
        return json.dumps({
            "advisor_script": f"[mock] Hi {first}, I wanted to follow up on our last call and talk things through together.",
            "trusted_contact_message": f"[mock] Hello, I'm {first}'s advisor. You are listed as the trusted contact; could we talk briefly?",
            "hold_note": "[mock] Pending money movement matches an exploitation pattern. Recommend review under FINRA Rule 2165.",
        })

    name = re.search(r"^Name: (.+)$", prompt, re.MULTILINE).group(1).strip()
    new_call = prompt.split("NEW CALL:", 1)[1]
    call_number = int(re.search(r"--- Call (\d+)", new_call).group(1))
    sample = _sample_for(_client_ids_by_name()[name], call_number)
    return "```json\n" + json.dumps({
        "signals": sample["signals"],
        "people_mentioned": sample["people_mentioned"],
        "summary": sample["summary"],
    }) + "\n```"
