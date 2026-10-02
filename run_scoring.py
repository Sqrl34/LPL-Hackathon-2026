"""Tune and check scoring from the terminal.

    python run_scoring.py walter                 # score Walter's calls 1-3 with Bedrock
    python run_scoring.py walter --include-demo  # also score the live-upload Call 4
    python run_scoring.py --rules-only           # offline: check rules and quotes on sample_results.json

Run one client at a time, and only one teammate at a time, to stay under the
Bedrock rate limit.
"""

import argparse
import sys

import scoring
from schema import SIGNALS

LABELS = {
    "repetition": "rep",
    "memory_gaps": "mem",
    "new_influencer": "infl",
    "out_of_character": "ooc",
    "urgency_secrecy": "urg",
}


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
            for key in ("advisor_script", "trusted_contact_message", "hold_note"):
                if steps.get(key):
                    print(f"  {key}: {steps[key]}")


def rules_only():
    """Offline check of the rules and the sample data B builds against."""
    samples = scoring.load_sample_results()
    failures = 0
    for client_id in ("walter", "maria", "linda"):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("client_id", nargs="?", choices=["walter", "maria", "linda"])
    parser.add_argument("--include-demo", action="store_true", help="include data/demo calls (Walter's Call 4)")
    parser.add_argument("--no-next-steps", action="store_true", help="skip Haiku drafts (fewer Bedrock calls)")
    parser.add_argument("--rules-only", action="store_true", help="offline check, no Bedrock calls")
    args = parser.parse_args()

    if args.rules_only:
        sys.exit(0 if rules_only() else 1)
    if not args.client_id:
        parser.error("give a client_id or --rules-only")

    client = scoring.load_client_local(args.client_id)
    calls = scoring.load_calls_local(args.client_id, include_demo=args.include_demo)
    results = scoring.analyze_client(client, calls, with_next_steps=not args.no_next_steps)
    print_results(results)


if __name__ == "__main__":
    main()
