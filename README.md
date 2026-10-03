# Second Look

An AI tool for LPL advisors. It notices when a client's behavior across calls starts changing in ways that put their money at risk, such as repeated questions, forgotten decisions, or a new "friend" pushing urgent moves. It flags the warning signs with quotes as evidence. The AI finds evidence, fixed Python rules set the risk color, and a human decides what to do. It never diagnoses.

LPL Financial University Hackathon 2026.

## Stack
Amazon S3 · Amazon Bedrock (Claude Sonnet 4.6, Claude Haiku 4.5) · Bedrock Guardrails · Amazon Comprehend · Streamlit · Plotly · Strands Agents. Region: **us-east-1** only.

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in bucket, model IDs, guardrail (.env is git-ignored)
# AWS credentials: terminal or local .env only, never in code (see "Going live")
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
- **For C:** `scoring.py` calls `aws_clients.converse(model_id, prompt, system=..., guardrail=False)` and `aws_clients.check_output(texts)` for the output guardrail. If `aws_clients` can't be imported, it falls back to its own paced boto3 calls.
- **For B:** `data/sample_results.json` has hand-written results for every call (`walter`, `maria`, `linda`, plus `walter_demo_call_4`), so you can build the screen without Bedrock.

On Windows, run Python as `py`. Full flag list: `py run_scoring.py -h`.
```bash
py run_scoring.py --rules-only                    # offline: rules, quotes, new people vs sample data
py run_scoring.py walter --mock --include-demo    # offline: full pipeline with fake Bedrock
py run_scoring.py walter --show-prompt 3          # print the exact scoring prompt, no Bedrock
py run_scoring.py walter                          # live Bedrock run, calls 1-3
py run_scoring.py walter --include-demo           # also score Call 4
```

### Going live (once AWS credentials arrive)
1. Put the event's temporary credentials in **one** of these places. Never put them in code or `.env.example`.
   ```powershell
   # Option A: this PowerShell terminal (lasts until you close it)
   $env:AWS_ACCESS_KEY_ID="..."
   $env:AWS_SECRET_ACCESS_KEY="..."
   $env:AWS_SESSION_TOKEN="..."
   $env:AWS_DEFAULT_REGION="us-east-1"
   ```
   Option B: uncomment the same three keys in your local `.env`, which is git-ignored.
2. Look up the inference profile IDs:
   ```powershell
   aws bedrock list-inference-profiles --region us-east-1 --query "inferenceProfileSummaries[?contains(inferenceProfileName,'Claude')].[inferenceProfileName,inferenceProfileId]" --output table
   ```
   Paste the Sonnet 4.6 and Haiku 4.5 IDs (they usually start with `us.anthropic.`) into `.env` as `SONNET_MODEL_ID` and `HAIKU_MODEL_ID`. Get `GUARDRAIL_ID` and `S3_BUCKET` from C.
3. Run these in order, and stop to fix anything that fails:
   ```powershell
   py run_scoring.py --check                           # credentials, IDs, 2 tiny Bedrock calls, guardrail
   py run_scoring.py walter --call 2                   # Walter's Call 2 vs Call 1, should be yellow
   py run_scoring.py --all --no-next-steps --targets   # PASS/FAIL per call vs expected colors
   py run_scoring.py walter --include-demo             # full run with Haiku drafts
   ```
4. If a call misses its target color, adjust `RUBRIC_PROMPT` in `scoring.py` and rerun step 3. Run `--targets` 2 or 3 times to check that the scores stay consistent.

Troubleshooting: if you see `ExpiredToken`, paste fresh credentials. If you see access denied, check the region (us-east-1) and model access. If you see an invalid model error, use the inference profile ID, not the plain model name. A full run of all three clients is about 17 Bedrock calls, and only one teammate should run it at a time.

### Upload the data to S3 (Call 4 stays out for the live demo)
```bash
aws s3 sync data/clients s3://$S3_BUCKET/clients --region us-east-1
aws s3 sync data/transcripts s3://$S3_BUCKET/transcripts --region us-east-1
```
Before presenting, delete `uploads/walter/call-4.json` from the bucket (the screen's **Reset demo** button does this).

No AWS CLI? The same setup in Python, with credentials exported in the terminal:
```bash
python aws_setup.py --upload-data second-look-lpl-432810293903   # clients/ and transcripts/, not data/demo
python aws_setup.py --guardrail                                   # creates or reuses SecondLookSafety, prints GUARDRAIL_ID
```

## Screen (B): how it uses AWS
- **Live mode** (credentials and model IDs set): transcripts load from S3 (falling back to `data/` if S3 can't be read), Comprehend finds the people mentioned, and `scoring.py` scores each call. Results are cached in memory, so a refresh doesn't call Bedrock again. **Receive Call 4 transcript** saves to `uploads/` in S3, then scores the call. **Ask** goes through the guardrail, which checks only the question on input and checks the whole answer.
- **Guardrail placement:** transcripts are sent to Sonnet *without* an input guardrail, so a client's own words can't get their analysis blocked. What the model writes (summaries, quotes, drafts) is checked as OUTPUT with `ApplyGuardrail`, which blocks diagnosis talk and masks account numbers and SSNs.
- **Without AWS** the screen shows `data/sample_results.json`, with a banner saying these are hand-written sample results. The sidebar toggle switches between the two.
- **Before presenting:** click **Analyze all clients** once so switching clients is instant. A first run is about 30 Bedrock calls, paced at about one per second.

## Rules
- Made-up data only. Private S3 bucket only. No AWS keys in code.
- About 1 Bedrock call per second. Only one person calls Bedrock at a time during testing.
- If anything fails, show "Analysis unavailable, review manually", never a fake green.

## Branches
Work on your own branch (`data-ai`, `ui`, `aws-plumbing`) and merge to `main` at 3 PM, 6 PM, and 9 PM. Keep `main` demo-ready.
