"""The shared contract between scoring (A), the screen (B), and the agent (C).

Change this file only after telling the whole team.
"""

SIGNALS = [
    "repetition",
    "memory_gaps",
    "new_influencer",
    "out_of_character",
    "urgency_secrecy",
]

LEVELS = ["green", "yellow", "red", "unavailable"]

# Shape of one analyzed call. "unavailable" means Bedrock or S3 failed;
# the screen shows "Analysis unavailable, review manually" (never a fake green).
EXAMPLE_RESULT = {
    "client_id": "walter",
    "call_number": 3,
    "signals": {
        "repetition": {"score": 1, "quote": "..."},
        "memory_gaps": {"score": 3, "quote": "I never agreed to rebalance anything."},
        "new_influencer": {"score": 3, "quote": "Daniel thinks I should put money in crypto."},
        "out_of_character": {"score": 2, "quote": "..."},
        "urgency_secrecy": {"score": 0, "quote": ""},
    },
    "people_mentioned": ["Ellen", "Daniel"],
    "new_people": ["Daniel"],
    "total": 9,
    "level": "red",
    "summary": "Plain-language summary of what changed, no diagnosis.",
    "date": "2026-02-17",
    # None for green; drafted by Haiku for yellow/red.
    "next_steps": {
        "label": "Draft, requires human approval",
        "advisor_script": "...",
        "trusted_contact_message": "...",  # red only, else None
        "hold_note": "...",  # red with money moving only, else None
    },
}
