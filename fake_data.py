"""Made-up clients and per-call results so the screen works before scoring is ready.

Every person, quote and number here is fictional. Results follow schema.py, plus two
fields the screen also reads:
  - "date": carried over from the transcript file
  - "next_steps": what A's draft_next_steps returns for yellow/red calls
      {"advisor_script": str, "trusted_contact_message": str | None, "hold_note": str | None}
"""


def _signals(rep=(0, ""), mem=(0, ""), infl=(0, ""), ooc=(0, ""), urg=(0, "")):
    pairs = {
        "repetition": rep,
        "memory_gaps": mem,
        "new_influencer": infl,
        "out_of_character": ooc,
        "urgency_secrecy": urg,
    }
    return {name: {"score": s, "quote": q} for name, (s, q) in pairs.items()}


def _result(client_id, call_number, date, signals, level, summary,
            people, new_people, next_steps=None):
    return {
        "client_id": client_id,
        "call_number": call_number,
        "date": date,
        "signals": signals,
        "people_mentioned": people,
        "new_people": new_people,
        "total": sum(s["score"] for s in signals.values()),
        "level": level,
        "summary": summary,
        "next_steps": next_steps,
    }


CLIENTS = {
    "walter": {
        "client_id": "walter",
        "name": "Walter Brooks",
        "age": 81,
        "trusted_contact": "Ellen Brooks (daughter)",
        "advisor": "Sarah Lin",
        "notes": "Retired engineer, conservative bond investor, client for 12 years",
    },
    "maria": {
        "client_id": "maria",
        "name": "Maria Delgado",
        "age": 54,
        "trusted_contact": "Marcus Delgado (son)",
        "advisor": "James Okafor",
        "notes": "Recently widowed, moderate-growth investor, client for 8 years",
    },
    "linda": {
        "client_id": "linda",
        "name": "Linda Park",
        "age": 79,
        "trusted_contact": "Thomas Park (son)",
        "advisor": "Priya Shah",
        "notes": "Retired teacher, income-focused, client for 15 years. "
                 "Always tells a garden story and double-checks fees on every call.",
    },
}


RESULTS = {
    "walter": [
        _result(
            "walter", 1, "2025-03-11",
            _signals(),
            "green",
            "Baseline call. Walter was detailed and cautious, asked about the 0.85% fee, "
            "and chose to keep the bond ladder as is.",
            ["Ellen"], [],
        ),
        _result(
            "walter", 2, "2025-09-02",
            _signals(
                rep=(2, "What's my balance right now? ... Okay. And sorry, what was the balance again?"),
                infl=(2, "My friend Daniel from church says I'm too conservative."),
            ),
            "yellow",
            "Walter asked for his balance twice within a few minutes, which he has not done before. "
            "A new person, Daniel, came up and is already shaping how Walter views his strategy. "
            "Walter agreed to a small rebalance at the end of the call.",
            ["Ellen", "Daniel"], ["Daniel"],
            next_steps={
                "advisor_script": (
                    "\"Walter, it was good to talk last week. I wanted to follow up and make sure "
                    "everything we discussed made sense. You mentioned Daniel has been sharing some "
                    "ideas with you. I'm always happy to walk through any suggestions together "
                    "before you make a decision. Would it help to set up a short call, maybe with "
                    "Ellen joining too?\""
                ),
                "trusted_contact_message": None,
                "hold_note": None,
            },
        ),
        _result(
            "walter", 3, "2026-02-17",
            _signals(
                rep=(1, "Is the fee still what it was? What is it again?"),
                mem=(3, "I never agreed to rebalance anything."),
                infl=(3, "Daniel thinks I should put money in crypto. He's been helping me with my bills."),
                ooc=(2, "Maybe we move forty thousand out of the bonds and into Bitcoin."),
            ),
            "red",
            "Walter did not recall the rebalance he agreed to in Call 2. Daniel now helps with his "
            "bills and is pushing a crypto purchase, a sharp break from 12 years of conservative "
            "bond investing.",
            ["Daniel", "Ellen"], ["Daniel"],
            next_steps={
                "advisor_script": (
                    "\"Walter, before we make any changes I'd like to slow down and review the "
                    "decisions we've made together over the last year. Could we schedule a meeting, "
                    "and would you be comfortable with Ellen joining us?\""
                ),
                "trusted_contact_message": (
                    "Hello Ellen, this is Sarah Lin, Walter's financial advisor. You are listed as "
                    "Walter's trusted contact. I'd appreciate a few minutes to talk about some recent "
                    "account activity and make sure Walter has the support he needs. "
                    "Please call me at your convenience."
                ),
                "hold_note": None,
            },
        ),
    ],
    "maria": [
        _result(
            "maria", 1, "2025-04-08",
            _signals(),
            "green",
            "Baseline call. Maria reviewed the account after her husband's passing, asked clear "
            "questions about the survivor benefits, and kept her moderate-growth allocation.",
            ["Marcus"], [],
        ),
        _result(
            "maria", 2, "2025-10-14",
            _signals(
                infl=(2, "I've been talking with someone I met online, Victor. He's been so kind."),
                ooc=(1, "Victor says crypto is really the only way to grow money now."),
            ),
            "green",
            "A new person, Victor, met online, came up for the first time and has opinions about "
            "her investments. No requests were made. Worth noting, not yet a pattern.",
            ["Marcus", "Victor"], ["Victor"],
        ),
        _result(
            "maria", 3, "2026-03-03",
            _signals(
                infl=(3, "Victor set up an account for me on the platform he uses."),
                ooc=(3, "I'd like to move $25,000 into the crypto platform Victor recommended."),
                urg=(2, "I haven't told Marcus. He wouldn't understand."),
            ),
            "red",
            "Victor now directs a specific $25,000 transfer to a platform he chose, and Maria is "
            "keeping it from her son. New influencer plus secrecy is the classic exploitation pattern.",
            ["Victor", "Marcus"], ["Victor"],
            next_steps={
                "advisor_script": (
                    "\"Maria, I want to make sure this move is right for you. Before we send anything, "
                    "could we look at this platform together? I'd like to understand who runs it and "
                    "how withdrawals work.\""
                ),
                "trusted_contact_message": (
                    "Hello Marcus, this is James Okafor, Maria's financial advisor. You are listed as "
                    "Maria's trusted contact. I'd like to speak with you briefly about some recent "
                    "account activity. Please call me when you can."
                ),
                "hold_note": (
                    "Requested $25,000 transfer to an unfamiliar crypto platform. Refer to compliance "
                    "to consider a temporary hold under FINRA Rule 2165."
                ),
            },
        ),
        _result(
            "maria", 4, "2026-07-21",
            _signals(
                mem=(1, "Did we ever set Marcus up as my trusted contact? I don't think we did."),
                infl=(3, "Victor is the only one who really understands my situation."),
                ooc=(3, "I need to send another $40,000 so I can unlock my earnings."),
                urg=(3, "It has to be this week or the account closes. Please keep this between us."),
            ),
            "red",
            "Maria is asked to send more money to 'unlock' earnings, a common scam tactic. She is "
            "under deadline pressure, asking for secrecy, and did not recall naming Marcus as her "
            "trusted contact in 2025.",
            ["Victor", "Marcus"], ["Victor"],
            next_steps={
                "advisor_script": (
                    "\"Maria, I'm concerned about this platform. Having to pay to unlock earnings is "
                    "a warning sign we see in scams. Let's pause and talk this through before any "
                    "money moves. Can we meet this week?\""
                ),
                "trusted_contact_message": (
                    "Hello Marcus, this is James Okafor, Maria's financial advisor. You are listed as "
                    "Maria's trusted contact. I have a time-sensitive concern about recent account "
                    "activity and would like to speak with you today if possible."
                ),
                "hold_note": (
                    "Second transfer request ($40,000) tied to an 'unlock earnings' claim with a "
                    "one-week deadline. Escalate to compliance for a temporary hold under FINRA Rule 2165."
                ),
            },
        ),
    ],
    "linda": [
        _result(
            "linda", 1, "2025-02-19",
            _signals(),
            "green",
            "Baseline call. Linda shared her usual garden update with Rose from the garden club, "
            "double-checked the fee, and kept her income allocation.",
            ["Rose", "Thomas"], [],
        ),
        _result(
            "linda", 2, "2025-08-12",
            _signals(rep=(1, "Just so I'm sure, the fee is still 0.75%, right?")),
            "green",
            "Linda double-checked the fee, which she does on every call. Same garden story, same "
            "people, consistent decisions. Nothing unusual compared to her own history.",
            ["Rose", "Thomas"], [],
        ),
        _result(
            "linda", 3, "2026-01-27",
            _signals(),
            "green",
            "Routine review. Linda confirmed her required minimum distribution and kept her "
            "allocation unchanged.",
            ["Rose", "Thomas"], [],
        ),
        _result(
            "linda", 4, "2026-07-09",
            _signals(ooc=(1, "Thomas and I are thinking of selling the house to be closer to Ava and the grandkids.")),
            "green",
            "Linda is considering downsizing to move near family, with her son involved. A clear, "
            "normal reason for a change. Ava was mentioned once in passing and is not influencing decisions.",
            ["Rose", "Thomas", "Ava"], ["Ava"],
        ),
    ],
}


# Calls held back for the live upload in the demo.
PENDING_UPLOADS = {
    "walter": _result(
        "walter", 4, "2026-08-20",
        _signals(
            rep=(2, "Did I already tell you about the wire? I can't remember if I said."),
            mem=(2, "Sarah, remind me, how long have you been handling my account?"),
            infl=(3, "Daniel set up the receiving account for me, I just need to send it there."),
            ooc=(3, "I need $60,000 wired today."),
            urg=(3, "It's urgent. Please don't mention this to Ellen, she worries."),
        ),
        "red",
        "Walter wants $60,000 wired today to an account Daniel set up, and asked that Ellen not be "
        "told. Urgency, secrecy, and cutting out the trusted contact, after a year of Daniel's "
        "growing influence.",
        ["Daniel", "Ellen"], ["Daniel"],
        next_steps={
            "advisor_script": (
                "\"Walter, I want to help you, and I also want to make sure your money is safe. "
                "Wires can't be undone once they go out, so let's take a day to confirm the "
                "receiving account. Can we meet tomorrow in person?\""
            ),
            "trusted_contact_message": (
                "Hello Ellen, this is Sarah Lin, Walter's financial advisor. You are listed as "
                "Walter's trusted contact. I have a time-sensitive concern about a requested "
                "transfer and would like to speak with you today if possible."
            ),
            "hold_note": (
                "$60,000 same-day wire to an account set up by a third party, with a request for "
                "secrecy from the trusted contact. Escalate to compliance for a temporary hold "
                "under FINRA Rule 2165."
            ),
        },
    ),
}

PENDING_TRANSCRIPTS = {
    "walter": (
        "Advisor: Good afternoon Walter, how are you?\n"
        "Walter: Sarah, I need $60,000 wired today. It's urgent.\n"
        "Advisor: Okay, can you tell me what it's for?\n"
        "Walter: Daniel set up the receiving account for me, I just need to send it there. "
        "Please don't mention this to Ellen, she worries.\n"
        "Advisor: I understand. Can I ask a couple of questions about the account first?\n"
        "Walter: Sarah, remind me, how long have you been handling my account?\n"
        "Advisor: About twelve years now.\n"
        "Walter: Right. Did I already tell you about the wire? I can't remember if I said."
    ),
}
