"""One-time AWS setup for the Second Look demo.

Uses the temporary AWS credentials already exported in the shell. It creates
only the team's private S3 bucket and placeholder prefixes; it never writes
credentials or makes the bucket public.

Examples:
    python aws_setup.py --bucket second-look-team-name
    python aws_setup.py --list-inference-profiles
"""

from __future__ import annotations

import argparse
from typing import Any

import boto3

from config import AWS_REGION


def _s3() -> Any:
    return boto3.client("s3", region_name=AWS_REGION)


def ensure_private_bucket(bucket: str) -> None:
    if not bucket or "/" in bucket:
        raise ValueError("Pass a plain S3 bucket name, not a URL or ARN.")
    s3 = _s3()
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        s3.create_bucket(Bucket=bucket)

    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    for key in ("clients/.keep", "transcripts/.keep", "uploads/.keep"):
        s3.put_object(Bucket=bucket, Key=key, Body=b"")
    print(f"Private encrypted bucket ready: {bucket} ({AWS_REGION})")


def upload_data(bucket: str) -> None:
    """Python version of the README's `aws s3 sync` commands. Walter's Call 4 (data/demo) stays local."""
    from pathlib import Path

    data_dir = Path(__file__).parent / "data"
    s3 = _s3()
    count = 0
    for folder in ("clients", "transcripts"):
        for path in sorted((data_dir / folder).rglob("*.json")):
            key = path.relative_to(data_dir).as_posix()
            s3.upload_file(str(path), bucket, key,
                           ExtraArgs={"ContentType": "application/json", "ServerSideEncryption": "AES256"})
            count += 1
    print(f"Uploaded {count} files to {bucket} (clients/, transcripts/)")


GUARDRAIL_NAME = "SecondLookSafety"
GUARDRAIL_MESSAGE = (
    "Second Look cannot provide medical diagnoses. Please review the documented "
    "conversation signals with a qualified professional."
)


def ensure_guardrail() -> str:
    """Create (or reuse) the guardrail, same settings as scripts/create_guardrail.sh, no AWS CLI needed."""
    bedrock = boto3.client("bedrock", region_name=AWS_REGION)
    for page in bedrock.get_paginator("list_guardrails").paginate():
        for guardrail in page.get("guardrails", []):
            if guardrail.get("name") == GUARDRAIL_NAME:
                print(f"Using existing guardrail {GUARDRAIL_NAME}")
                return guardrail["id"]
    response = bedrock.create_guardrail(
        name=GUARDRAIL_NAME,
        description="Blocks medical diagnoses and masks financial identifiers in Second Look.",
        blockedInputMessaging=GUARDRAIL_MESSAGE,
        blockedOutputsMessaging=GUARDRAIL_MESSAGE,
        topicPolicyConfig={
            "topicsConfig": [{
                "name": "Medical diagnosis",
                "definition": (
                    "Medical or mental-health diagnosis, including identifying, confirming, or speculating "
                    "that a person has dementia, Alzheimer's disease, cognitive impairment, or any other condition."
                ),
                "examples": [
                    "Does Walter have dementia?",
                    "Is Maria suffering from Alzheimer's?",
                    "Diagnose this client based on their call.",
                ],
                "type": "DENY",
                "inputAction": "BLOCK",
                "outputAction": "BLOCK",
                "inputEnabled": True,
                "outputEnabled": True,
            }],
            "tierConfig": {"tierName": "CLASSIC"},
        },
        sensitiveInformationPolicyConfig={
            "piiEntitiesConfig": [
                {"type": "US_SOCIAL_SECURITY_NUMBER", "action": "ANONYMIZE"},
                {"type": "US_BANK_ACCOUNT_NUMBER", "action": "ANONYMIZE"},
                {"type": "US_BANK_ROUTING_NUMBER", "action": "ANONYMIZE"},
            ]
        },
    )
    print(f"Created guardrail {GUARDRAIL_NAME}")
    return response["guardrailId"]


def list_inference_profiles() -> None:
    bedrock = boto3.client("bedrock", region_name=AWS_REGION)
    paginator = bedrock.get_paginator("list_inference_profiles")
    for page in paginator.paginate():
        for profile in page.get("inferenceProfileSummaries", []):
            name = profile.get("inferenceProfileName", "")
            if any(term in name.casefold() for term in ("sonnet", "haiku")):
                print(f"{name}\t{profile.get('inferenceProfileId', '')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", help="Team S3 bucket name to create/configure")
    parser.add_argument("--list-inference-profiles", action="store_true")
    parser.add_argument("--guardrail", action="store_true", help="create or reuse the SecondLookSafety guardrail")
    parser.add_argument("--upload-data", metavar="BUCKET",
                        help="upload data/clients and data/transcripts (not data/demo) to the bucket")
    args = parser.parse_args()
    if not (args.bucket or args.list_inference_profiles or args.guardrail or args.upload_data):
        parser.error("choose --bucket, --upload-data, --guardrail and/or --list-inference-profiles")
    if args.bucket:
        ensure_private_bucket(args.bucket)
    if args.upload_data:
        upload_data(args.upload_data)
    if args.guardrail:
        guardrail_id = ensure_guardrail()
        print(f"\nAdd these to .env:\nGUARDRAIL_ID={guardrail_id}\nGUARDRAIL_VERSION=DRAFT")
    if args.list_inference_profiles:
        list_inference_profiles()


if __name__ == "__main__":
    main()
