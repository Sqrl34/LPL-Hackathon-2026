# Second Look

An AI tool for LPL advisors. It notices when a client's behavior across calls starts changing in ways that put their money at risk, such as repeated questions, forgotten decisions, or a new "friend" pushing urgent moves. It flags the warning signs with quotes as evidence. The AI finds evidence, fixed Python rules set the risk color, and a human decides what to do. It never diagnoses.

LPL Financial University Hackathon 2026.

## Stack
Amazon S3 · Amazon Bedrock (Claude Sonnet 4.6, Claude Haiku 4.5) · Bedrock Guardrails · Amazon Comprehend · Streamlit · Plotly · Strands Agents. Region: **us-east-1** only.

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in bucket, model IDs, guardrail. No keys!
# export the event's temporary AWS credentials in this terminal
streamlit run app.py
```

## Layout
| Path | Owner | What |
|---|---|---|
| `data/` | A | Made-up clients and transcripts (backup copy of the S3 bucket) |
| `data/demo/` | A | Walter's Call 4, kept aside for the live upload |
| `schema.py` | all | Shared result format, so change it only after telling the team |
| `scoring.py` | A | Bedrock scoring, risk rules, next-step drafts |
| `app.py` | B | Streamlit screen |
| `aws_clients.py` | C | S3, Comprehend, Bedrock with pacing and retries |
| `agent.py` | C | Strands agent (cut first if behind) |

## Rules
- Made-up data only. Private S3 bucket only. No AWS keys in code.
- About 1 Bedrock call per second. Only one person calls Bedrock at a time during testing.
- If anything fails, show "Analysis unavailable, review manually", never a fake green.

## Branches
Work on your own branch (`data-ai`, `ui`, `aws-plumbing`) and merge to `main` at 3 PM, 6 PM, and 9 PM. Keep `main` demo-ready.
