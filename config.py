"""Shared settings, read from environment variables (or a local .env file)."""

import os

from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET", "")
SONNET_MODEL_ID = os.getenv("SONNET_MODEL_ID", "")
HAIKU_MODEL_ID = os.getenv("HAIKU_MODEL_ID", "")
GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")
BEDROCK_PAUSE_SECONDS = float(os.getenv("BEDROCK_PAUSE_SECONDS", "1.2"))

if AWS_REGION != "us-east-1":
    raise RuntimeError("Event rule: AWS_REGION must be us-east-1")
