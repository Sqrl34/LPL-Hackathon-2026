# Second Look

Second Look helps financial advisors spot when a client's money may be at risk. It compares a client's calls over time and flags changes such as repeated questions, forgotten decisions, or pressure from a newly mentioned person.

Every flag comes with quotes from the transcript as evidence. AI finds the signals, fixed Python rules set the risk level, and the advisor makes the final decision. Second Look never gives a medical diagnosis.

## Run with AWS (live mode)

All AWS services run in `us-east-1`.

1. **Clone the repo and install:**

   ```bash
   git clone https://github.com/Sqrl34/LPL-Hackathon-2026.git
   cd LPL-Hackathon-2026
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   ```

2. **Add your AWS credentials.** Export them in your terminal (or put them in `.env`, which git ignores):

   ```bash
   export AWS_ACCESS_KEY_ID='...'
   export AWS_SECRET_ACCESS_KEY='...'
   export AWS_SESSION_TOKEN='...'
   ```

3. **Create the S3 bucket and upload the sample data:**

   ```bash
   python aws_setup.py --upload-data your-bucket-name
   ```

4. **Create the guardrail.** Note the ID it prints:

   ```bash
   python aws_setup.py --guardrail
   ```

5. **Find the model IDs** for Claude Sonnet and Claude Haiku:

   ```bash
   python aws_setup.py --list-inference-profiles
   ```

6. **Fill in `.env`:**

   ```dotenv
   AWS_REGION=us-east-1
   S3_BUCKET=your-bucket-name
   SONNET_MODEL_ID=...
   HAIKU_MODEL_ID=...
   GUARDRAIL_ID=...
   GUARDRAIL_VERSION=DRAFT
   ```

7. **Check the connection, then start the app:**

   ```bash
   python scripts/verify_aws.py
   streamlit run app.py
   ```

8. **Open** http://localhost:8501.

Results are kept in memory until the server restarts. Click **Re-run analysis** in the sidebar to score a client again.

## Backup: run without AWS

If AWS is down or you don't have keys, the app falls back to the sample results in `data/`.

```bash
git clone https://github.com/Sqrl34/LPL-Hackathon-2026.git
cd LPL-Hackathon-2026
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

Open http://localhost:8501. If AWS is already set up, you can also switch to the backup data with the **Live Bedrock analysis** toggle in the sidebar.

## Tests

```bash
python tests/run_all.py
```

The tests use fake AWS responses, so they don't need credentials and never call AWS.

## How it works

| Service | What it does |
|---|---|
| Amazon S3 | Stores client records, transcripts, and uploaded calls |
| Amazon Comprehend | Finds the people mentioned in each call |
| Amazon Bedrock (Claude Sonnet 4.6) | Finds the risk signals in each call |
| Amazon Bedrock (Claude Haiku 4.5) | Drafts next steps for the advisor to approve |
| Bedrock Guardrails | Blocks diagnosis language and masks account and Social Security numbers |
| Strands Agents | Answers the advisor's questions about a client |
| Streamlit | The web app |

If analysis fails, the app shows "Analysis unavailable, review manually." It never falls back to a low-risk result.

## Project files

| Path | Purpose |
|---|---|
| `app.py` | Streamlit app |
| `scoring.py` | Signal extraction, risk rules, and draft next steps |
| `agent.py` | Advisor question-and-answer agent |
| `aws_clients.py` | S3, Comprehend, and Bedrock helpers |
| `aws_setup.py` | Creates the S3 bucket, guardrail, and IAM policy |
| `schema.py` | Shared result format |
| `run_scoring.py` | Runs scoring from the command line (`python run_scoring.py --help`) |
| `data/` | Synthetic clients, transcripts, and sample results |
| `iam/` | Least-privilege IAM policy |
| `tests/` | Offline tests |
