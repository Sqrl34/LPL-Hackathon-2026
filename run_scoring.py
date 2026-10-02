"""Tune and check scoring from the terminal.

    py run_scoring.py --check                     # preflight: credentials, model IDs, 2 tiny Bedrock calls
    py run_scoring.py walter --call 2             # score only Call 2 against Call 1
    py run_scoring.py walter                      # score Walter's calls 1-3 with Bedrock
    py run_scoring.py walter --include-demo       # also score the live-upload Call 4
    py run_scoring.py --all --no-next-steps --targets   # every client, PASS/FAIL vs expected colors
    py run_scoring.py walter --show-prompt 3      # print the exact prompt, no Bedrock call
    py run_scoring.py walter --mock --include-demo     # full pipeline offline with fake Bedrock
    py run_scoring.py --rules-only                # offline: check rules and quotes on sample_results.json

Run one client at a time, and only one teammate at a time, to stay under the
Bedrock rate limit.
"""

import argparse
import subprocess
import sys
import time

import config
import scoring
from schema import SIGNALS

CLIENT_IDS = ["walter", "maria", "linda"]
LABELS = {
    "repetition": "rep",
    "memory_gaps": "mem",
    "new_influencer": "infl",
    "out_of_character": "ooc",
    "urgency_secrecy": "urg",
}
CREDENTIAL_HELP = """  Paste the event's temporary credentials, either in this PowerShell terminal:
    $env:AWS_ACCESS_KEY_ID="..."
    $env:AWS_SECRET_ACCESS_KEY="..."
    $env:AWS_SESSION_TOKEN="..."
  or by uncommenting the same three keys in your local .env (git-ignored)."""


# ---------- Output ----------

def print_results(results):
    header = "call  " + " ".join(f"{LABELS[s]:>4}" for s in SIGNALS) + "  total  level"
    print(header)
    print("-" * len(header))
    for r in results:
        scores = " ".join(f"{r['signals'][s]['score']:>4}" for s in SIGNALS)
        total = "-" if r["total"] is None else r["total"]
        print(f"{r['call_number']:>4}  {scores}  {total:>5}  {r['level']}")
    for r in results:
        print(f"\nCall {r['call_number']} ({r['level']}): {r['summary']}")
        if r.get("error"):
            print(f"  error: {r['error']}")
        for s in SIGNALS:
            if r["signals"][s]["score"]:
                print(f"  {s}: \"{r['signals'][s]['quote']}\"")
        if r.get("new_people"):
            print(f"  new people: {', '.join(r['new_people'])}")
        steps = r.get("next_steps")
        if steps:
            print(f"  [{steps['label']}]")
            for key in ("advisor_script", "trusted_contact_message", "hold_note", "error"):
                if steps.get(key):
                    print(f"  {key}: {steps[key]}")


def expected_levels(client_id):
    samples = scoring.load_sample_results()
    results = list(samples[client_id])
    if f"{client_id}_demo_call_4" in samples:
        results.append(samples[f"{client_id}_demo_call_4"])
    return {r["call_number"]: r["level"] for r in results}


def check_targets(client_id, results):
    expected = expected_levels(client_id)
    misses = 0
    print(f"\nTargets for {client_id}:")
    for r in results:
        want = expected.get(r["call_number"])
        ok = r["level"] == want
        misses += not ok
        print(f"  call {r['call_number']}: got {r['level']:<11} want {want:<7} {'PASS' if ok else 'FAIL'}")
    return misses


# ---------- Offline rules check ----------

def rules_only():
    """Offline check of the rules and the sample data B builds against."""
    samples = scoring.load_sample_results()
    failures = 0
    for client_id in CLIENT_IDS:
        client = scoring.load_client_local(client_id)
        results = list(samples[client_id])
        if client_id == "walter":
            results.append(samples["walter_demo_call_4"])
        calls = {c["call_number"]: c for c in scoring.load_calls_local(client_id, include_demo=True)}
        new_people = scoring.compute_new_people(client, [r["people_mentioned"] for r in results])

        for i, r in enumerate(results):
            total, level = scoring.apply_rules(r["signals"], results[:i])
            problems = []
            if (total, level) != (r["total"], r["level"]):
                problems.append(f"rules give {total}/{level}, sample says {r['total']}/{r['level']}")
            for s in SIGNALS:
                sig = r["signals"][s]
                if sig["score"] and not scoring.quote_in_transcript(sig["quote"], calls[r["call_number"]]["transcript"]):
                    problems.append(f"{s} quote not in transcript: {sig['quote']!r}")
            if new_people[i] != r["new_people"]:
                problems.append(f"new_people {new_people[i]} != sample {r['new_people']}")
            status = "ok" if not problems else "FAIL"
            failures += bool(problems)
            print(f"{client_id} call {r['call_number']}: {total:>2} {level:<6} {status}")
            for p in problems:
                print(f"    {p}")
    print("\nAll checks passed." if not failures else f"\n{failures} call(s) failed.")
    return failures == 0


# ---------- Preflight ----------

def _ok(msg):
    print(f"[ok]   {msg}")


def _fail(msg, fix=""):
    print(f"[FAIL] {msg}")
    if fix:
        print(fix)
    return False


def _warn(msg):
    print(f"[warn] {msg}")


def check_credentials():
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return _fail("boto3 is not installed.", "  Run: py -m pip install -r requirements.txt")
    try:
        identity = boto3.client("sts", region_name=config.AWS_REGION).get_caller_identity()
    except (BotoCoreError, ClientError) as err:
        return _fail(f"AWS credentials: {scoring.friendly_error(err)}", CREDENTIAL_HELP)
    _ok(f"AWS credentials for {identity['Arn']}")
    return True


def preflight():
    try:
        import boto3  # noqa: F401
        import dotenv  # noqa: F401
    except ImportError as err:
        return _fail(f"Missing package: {err.name}", "  Run: py -m pip install -r requirements.txt")
    _ok("packages installed")

    try:
        tracked = subprocess.run(["git", "ls-files", ".env"], capture_output=True, text=True).stdout.strip()
        if tracked:
            _warn(".env is tracked by git! Run: git rm --cached .env")
        else:
            _ok(".env is not tracked by git")
    except OSError:
        _warn("git not found; could not check whether .env is tracked")

    if not check_credentials():
        return False
    _ok(f"region {config.AWS_REGION}")

    for label, model_id in (("SONNET_MODEL_ID", config.SONNET_MODEL_ID), ("HAIKU_MODEL_ID", config.HAIKU_MODEL_ID)):
        if not model_id:
            return _fail(f"{label} is empty in .env", "  See README 'Going live' for the inference profile lookup.")
        if not (model_id.startswith("us.") or model_id.startswith("arn:")):
            _warn(f"{label}={model_id} doesn't look like an inference profile ID (usually starts with 'us.')")

    for label, model_id in (("Sonnet", config.SONNET_MODEL_ID), ("Haiku", config.HAIKU_MODEL_ID)):
        try:
            reply = scoring.converse(model_id, "You are a test.", "Reply with OK.", guardrail=False)
        except Exception as err:
            return _fail(f"{label} call failed: {scoring.friendly_error(err)}")
        _ok(f"{label} replied: {reply.strip()[:40]!r}")

    if config.GUARDRAIL_ID:
        import boto3
        try:
            boto3.client("bedrock", region_name=config.AWS_REGION).get_guardrail(
                guardrailIdentifier=config.GUARDRAIL_ID, guardrailVersion=config.GUARDRAIL_VERSION
            )
            _ok(f"guardrail {config.GUARDRAIL_ID} (version {config.GUARDRAIL_VERSION}) exists")
        except Exception as err:
            return _fail(f"Guardrail lookup failed: {scoring.friendly_error(err)}")
    else:
        _warn("GUARDRAIL_ID not set; scoring runs without the guardrail until C shares it")

    if not config.S3_BUCKET:
        _warn("S3_BUCKET not set (not needed for scoring from local data)")
    print("\nPreflight passed.")
    return True


# ---------- Runs ----------

def run_one_call(client_id, call_number, include_demo, with_next_steps):
    client = scoring.load_client_local(client_id)
    calls = scoring.load_calls_local(client_id, include_demo=include_demo or call_number == 4)
    index = next((i for i, c in enumerate(calls) if c["call_number"] == call_number), None)
    if index is None:
        sys.exit(f"No call {call_number} for {client_id}")
    samples = {r["call_number"]: r for r in scoring.load_sample_results()[client_id]}
    prior = [samples[c["call_number"]] for c in calls[:index] if c["call_number"] in samples]
    if prior:
        print(f"(Earlier call results for the 2-call yellow rule come from sample_results.json: "
              f"calls {', '.join(str(r['call_number']) for r in prior)}.)\n")
    return [scoring.analyze_new_call(client, calls[:index], prior, calls[index], with_next_steps=with_next_steps)]


def run_client(client_id, include_demo, with_next_steps):
    client = scoring.load_client_local(client_id)
    calls = scoring.load_calls_local(client_id, include_demo=include_demo)
    return scoring.analyze_client(client, calls, with_next_steps=with_next_steps)


def show_prompt(client_id, call_number):
    calls = scoring.load_calls_local(client_id, include_demo=True)
    index = next(i for i, c in enumerate(calls) if c["call_number"] == call_number)
    prompt = scoring.build_score_prompt(calls[index], calls[:index], scoring.load_client_local(client_id))
    print("=== SYSTEM ===\n" + scoring.RUBRIC_PROMPT + "\n\n=== USER ===\n" + prompt)
    chars = len(scoring.RUBRIC_PROMPT) + len(prompt)
    print(f"\n(~{chars // 4} input tokens)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("client_id", nargs="?", choices=CLIENT_IDS)
    parser.add_argument("--all", action="store_true", help="run walter, maria and linda in turn")
    parser.add_argument("--call", type=int, metavar="N", help="score only call N against the calls before it")
    parser.add_argument("--include-demo", action="store_true", help="include data/demo calls (Walter's Call 4)")
    parser.add_argument("--no-next-steps", action="store_true", help="skip Haiku drafts (fewer Bedrock calls)")
    parser.add_argument("--targets", action="store_true", help="PASS/FAIL each call against the expected color")
    parser.add_argument("--show-prompt", type=int, metavar="N", help="print the scoring prompt for call N, no Bedrock")
    parser.add_argument("--mock", action="store_true", help="use fake Bedrock replaying sample_results.json")
    parser.add_argument("--check", action="store_true", help="preflight AWS setup (2 tiny Bedrock calls)")
    parser.add_argument("--rules-only", action="store_true", help="offline check, no Bedrock calls")
    args = parser.parse_args()

    if args.rules_only:
        sys.exit(0 if rules_only() else 1)
    if args.check:
        sys.exit(0 if preflight() else 1)
    if args.show_prompt:
        if not args.client_id:
            parser.error("--show-prompt needs a client_id")
        show_prompt(args.client_id, args.show_prompt)
        return

    clients = CLIENT_IDS if args.all else [args.client_id] if args.client_id else []
    if not clients:
        parser.error("give a client_id, --all, --check, --show-prompt or --rules-only")
    if args.call and args.all:
        parser.error("--call works with one client")

    if args.mock:
        import mock_bedrock
        scoring.converse = mock_bedrock.fake_converse
        print("(MOCK Bedrock: replaying sample_results.json, not real scoring)\n")
    elif not check_credentials():
        sys.exit(1)

    misses = 0
    for i, client_id in enumerate(clients):
        if i:
            time.sleep(config.BEDROCK_PAUSE_SECONDS)
        print(f"\n===== {client_id} =====")
        if args.call:
            results = run_one_call(client_id, args.call, args.include_demo, not args.no_next_steps)
        else:
            results = run_client(client_id, args.include_demo, not args.no_next_steps)
        print_results(results)
        if args.targets:
            misses += check_targets(client_id, results)

    if args.targets:
        print("\nAll targets hit." if not misses else f"\n{misses} call(s) missed their target color.")
        sys.exit(1 if misses else 0)


if __name__ == "__main__":
    main()
