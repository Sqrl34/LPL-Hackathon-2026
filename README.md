# Second Look

Second Look is an AI-assisted review tool for financial advisors. It compares a client's behavior across calls and surfaces changes that may put the client's money at risk, including repeated questions, forgotten decisions, or pressure from a newly mentioned person.

The system provides transcript quotes as evidence. AI extracts signals, deterministic Python rules assign the risk level, and the advisor makes the final decision. Second Look does not provide medical diagnoses.

## Technology

- Amazon S3 stores client records and call transcripts.
- Amazon Bedrock runs Claude Sonnet 4.6 for analysis and Claude Haiku 4.5 for draft next steps.
- Amazon Bedrock Guardrails blocks diagnostic language and masks sensitive account information.
- Amazon Comprehend identifies people mentioned in transcripts.
- Streamlit provides the web interface.
- Strands Agents powers the advisor question-and-answer workflow.

All AWS services run in `us-east-1`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

The application can run without AWS by using the local sample data. To use live AWS services, configure these values in the local `.env` file:

```dotenv
AWS_REGION=us-east-1
S3_BUCKET=your-private-bucket
SONNET_MODEL_ID=your-sonnet-inference-profile-id
HAIKU_MODEL_ID=your-haiku-inference-profile-id
GUARDRAIL_ID=your-guardrail-id
GUARDRAIL_VERSION=DRAFT
```

Provide AWS credentials either through the standard AWS credential chain or as temporary environment variables. Never commit credentials or place them in `.env.example`.

```bash
export AWS_ACCESS_KEY_ID='...'
export AWS_SECRET_ACCESS_KEY='...'
export AWS_SESSION_TOKEN='...'
export AWS_REGION='us-east-1'
```

Verify the live AWS connection:

```bash
.venv/bin/python scripts/verify_aws.py
```

## Running the application

```bash
source .venv/bin/activate
streamlit run app.py
```

In live mode, the application loads transcripts from S3, uses Comprehend to identify people, and sends call evidence to Bedrock for analysis. Results are cached in memory to avoid unnecessary model calls. Uploaded calls are stored under `uploads/` in S3 before analysis.

If AWS is unavailable or live mode is disabled, the application uses `data/sample_results.json` and local transcripts.

## Risk analysis

`scoring.py` combines model-extracted signals with deterministic rules:

```python
import scoring

client = scoring.load_client_local("walter")
calls = scoring.load_calls_local("walter")
results = scoring.analyze_client(client, calls)
```

Each result follows the format defined in `schema.py`. A result includes the risk level, evidence quotes, summary, and any draft next steps. Drafts require human approval. If analysis fails, the application reports `Analysis unavailable, review manually` instead of assigning a safe result.

Useful scoring commands:

```bash
python run_scoring.py --rules-only
python run_scoring.py walter --mock --include-demo
python run_scoring.py walter
python run_scoring.py --all --no-next-steps --targets
```

## AWS resources

Create or reuse the private S3 bucket and upload the included sample data:

```bash
python aws_setup.py --upload-data your-bucket-name
```

Create or reuse the Bedrock guardrail:

```bash
python aws_setup.py --guardrail
```

Generate and validate the least-privilege runtime policy:

```bash
python aws_setup.py --iam-policy
```

The runtime policy permits only the required operations: reading client and transcript data, managing uploaded calls, invoking the configured inference profiles and guardrail, and calling Comprehend entity detection.

## Guardrail behavior

Client transcripts are analyzed without an input guardrail so that quoted client language does not prevent analysis. Model-generated summaries, evidence, drafts, and advisor-facing answers are checked as output. Advisor questions are checked as input before the agent responds.

The guardrail blocks medical diagnosis language and masks account numbers and Social Security numbers. The application also includes a local refusal when a diagnosis is requested without a configured guardrail.

## Project structure

| Path | Purpose |
|---|---|
| `app.py` | Streamlit user interface and application flow |
| `scoring.py` | Signal extraction, risk rules, and draft next steps |
| `aws_clients.py` | S3, Comprehend, Bedrock, pacing, and retry helpers |
| `agent.py` | Guardrailed advisor assistant and tools |
| `aws_setup.py` | S3, guardrail, and IAM setup utilities |
| `schema.py` | Shared analysis result format |
| `data/` | Synthetic client data, transcripts, and offline results |
| `tests/` | Offline application, agent, rules, and AWS request tests |
| `iam/` | Least-privilege runtime policy |

## Tests

Run the offline test suite:

```bash
python tests/run_all.py
```

The offline tests use mocked AWS responses and do not make live AWS calls.

## Security principles

- Use only synthetic data for development and demonstrations.
- Keep the S3 bucket private.
- Never commit AWS credentials.
- Apply least-privilege IAM permissions.
- Treat all generated next steps as drafts requiring advisor approval.
- Report analysis failures explicitly; never replace them with a low-risk result.
