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

## Scoring (A): how to use it
```python
import scoring
client = scoring.load_client_local("walter")            # or C's S3 loader
calls = scoring.load_calls_local("walter")               # calls 1-3; include_demo=True adds Call 4
results = scoring.analyze_client(client, calls)          # one result per call, schema.py shape
new = scoring.analyze_new_call(client, calls, results, call_4)   # live upload
```
- Every result matches `schema.py`, including `date` and `next_steps`. `next_steps` is `None` for green. For yellow or red it holds `advisor_script`, and red adds `trusted_contact_message`, plus `hold_note` when money is moving. Every draft is labeled "Draft, requires human approval".
- If Bedrock or parsing fails, the result has `level: "unavailable"`, `total: None` and the summary "Analysis unavailable, review manually". It never comes back green.
- Optional `people_by_call`: a list of Comprehend name lists, one per call in order. Without it, the names the model returns are used. `scoring.recurring_new_people(...)` lists new names that show up in 2 or more calls.
- `scoring.apply_rules(signals, prior_results)` is pure Python with no AWS calls. Thresholds and the 2-call yellow rule are constants at the top of `scoring.py`.
- **For C:** `scoring.py` uses `aws_clients.converse(model_id, system, prompt, guardrail=False) -> str` once it exists. Until then it uses its own paced boto3 fallback.
- **For B:** `data/sample_results.json` has hand-written results for every call (`walter`, `maria`, `linda`, plus `walter_demo_call_4`), so you can build the screen without Bedrock.

```bash
python run_scoring.py --rules-only            # offline: rules, quotes, new people vs sample data
python run_scoring.py walter                  # live Bedrock run, calls 1-3
python run_scoring.py walter --include-demo   # also score Call 4
```

### Upload the data to S3 (Call 4 stays out for the live demo)
```bash
aws s3 sync data/clients s3://$S3_BUCKET/clients --region us-east-1
aws s3 sync data/transcripts s3://$S3_BUCKET/transcripts --region us-east-1
```
Before presenting, delete `uploads/walter/call-4.json` from the bucket.

## Rules
- Made-up data only. Private S3 bucket only. No AWS keys in code.
- About 1 Bedrock call per second. Only one person calls Bedrock at a time during testing.
- If anything fails, show "Analysis unavailable, review manually", never a fake green.

## Branches
Work on your own branch (`data-ai`, `ui`, `aws-plumbing`) and merge to `main` at 3 PM, 6 PM, and 9 PM. Keep `main` demo-ready.
