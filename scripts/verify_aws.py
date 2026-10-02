"""Verify Second Look's S3 bucket and Bedrock model connections.

Run from a shell containing the event's temporary AWS credentials:
    .venv/bin/python scripts/verify_aws.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import boto3

# Let the script run directly while importing project settings from its parent.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import AWS_REGION, HAIKU_MODEL_ID, S3_BUCKET, SONNET_MODEL_ID


def invoke(model_id: str, label: str) -> None:
    if not model_id:
        raise RuntimeError(f"{label}_MODEL_ID is missing from .env")
    response = boto3.client("bedrock-runtime", region_name=AWS_REGION).converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": "Reply with exactly: Second Look ready"}]}],
        inferenceConfig={"maxTokens": 20, "temperature": 0},
    )
    answer = "".join(item.get("text", "") for item in response["output"]["message"]["content"])
    if not answer:
        raise RuntimeError(f"{label} returned no text")
    print(f"{label}: connected")


def main() -> int:
    if AWS_REGION != "us-east-1":
        raise RuntimeError("AWS_REGION must be us-east-1")
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET is missing from .env")

    boto3.client("s3", region_name=AWS_REGION).head_bucket(Bucket=S3_BUCKET)
    print(f"S3: connected to {S3_BUCKET}")
    invoke(SONNET_MODEL_ID, "Sonnet")
    time.sleep(1.2)  # Event rule: roughly one Bedrock request per second.
    invoke(HAIKU_MODEL_ID, "Haiku")
    print("AWS verification passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"AWS verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
