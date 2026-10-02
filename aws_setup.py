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
    args = parser.parse_args()
    if not args.bucket and not args.list_inference_profiles:
        parser.error("choose --bucket and/or --list-inference-profiles")
    if args.bucket:
        ensure_private_bucket(args.bucket)
    if args.list_inference_profiles:
        list_inference_profiles()


if __name__ == "__main__":
    main()
