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


def _profile_arns(bedrock: Any, account_id: str, profile_id: str) -> tuple[str, list[str]]:
    """(inference profile ARN, foundation-model ARNs it routes to). Asks Bedrock; falls back to the US regions."""
    try:
        profile = bedrock.get_inference_profile(inferenceProfileIdentifier=profile_id)
        return profile["inferenceProfileArn"], sorted(m["modelArn"] for m in profile.get("models", []))
    except Exception:
        model = profile_id.split(".", 1)[1] if profile_id.startswith("us.") else profile_id
        return (f"arn:aws:bedrock:{AWS_REGION}:{account_id}:inference-profile/{profile_id}",
                [f"arn:aws:bedrock:{r}::foundation-model/{model}" for r in ("us-east-1", "us-east-2", "us-west-2")])


def runtime_policy() -> dict[str, Any]:
    """Least-privilege IAM policy for running Second Look (the app, scoring and the agent), built from .env."""
    from config import GUARDRAIL_ID, HAIKU_MODEL_ID, S3_BUCKET, SONNET_MODEL_ID

    missing = [n for n, v in (("S3_BUCKET", S3_BUCKET), ("SONNET_MODEL_ID", SONNET_MODEL_ID),
                              ("HAIKU_MODEL_ID", HAIKU_MODEL_ID), ("GUARDRAIL_ID", GUARDRAIL_ID)) if not v]
    if missing:
        raise SystemExit(f"Set {', '.join(missing)} in .env first.")
    account_id = boto3.client("sts", region_name=AWS_REGION).get_caller_identity()["Account"]
    bedrock = boto3.client("bedrock", region_name=AWS_REGION)
    profiles, models = [], []
    for profile_id in (SONNET_MODEL_ID, HAIKU_MODEL_ID):
        profile_arn, model_arns = _profile_arns(bedrock, account_id, profile_id)
        profiles.append(profile_arn)
        models.extend(model_arns)
    bucket = f"arn:aws:s3:::{S3_BUCKET}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "ListTeamBucket", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": bucket},
            {"Sid": "ReadTranscripts", "Effect": "Allow", "Action": "s3:GetObject",
             "Resource": [f"{bucket}/clients/*", f"{bucket}/transcripts/*", f"{bucket}/uploads/*"]},
            {"Sid": "WriteDemoUploadsOnly", "Effect": "Allow", "Action": ["s3:PutObject", "s3:DeleteObject"],
             "Resource": f"{bucket}/uploads/*"},
            {"Sid": "InvokeSonnetAndHaikuProfiles", "Effect": "Allow", "Action": "bedrock:InvokeModel",
             "Resource": profiles},
            {"Sid": "InvokeModelsOnlyThroughThoseProfiles", "Effect": "Allow", "Action": "bedrock:InvokeModel",
             "Resource": sorted(set(models)),
             "Condition": {"StringEquals": {"bedrock:InferenceProfileArn": profiles}}},
            {"Sid": "UseOurGuardrail", "Effect": "Allow", "Action": "bedrock:ApplyGuardrail",
             "Resource": f"arn:aws:bedrock:{AWS_REGION}:{account_id}:guardrail/{GUARDRAIL_ID}"},
            {"Sid": "FindPeopleInTranscripts", "Effect": "Allow", "Action": "comprehend:DetectEntities",
             "Resource": "*", "Condition": {"StringEquals": {"aws:RequestedRegion": AWS_REGION}}},
        ],
    }


def write_policy(path: str = "iam/second-look-runtime-policy.json") -> str:
    import json
    from pathlib import Path

    target = Path(__file__).parent / path
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(runtime_policy(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return str(target)


def validate_policy(path: str = "iam/second-look-runtime-policy.json") -> None:
    """Ask IAM Access Analyzer to check the policy (needs accessanalyzer:ValidatePolicy permission)."""
    from pathlib import Path

    document = (Path(__file__).parent / path).read_text(encoding="utf-8")
    try:
        findings = boto3.client("accessanalyzer", region_name=AWS_REGION).validate_policy(
            policyDocument=document, policyType="IDENTITY_POLICY")["findings"]
    except Exception as exc:
        print(f"Access Analyzer couldn't check it ({exc}). Paste the file into the IAM console's policy editor instead.")
        return
    if not findings:
        print("Access Analyzer: no findings. The policy is valid.")
    for finding in findings:
        print(f"{finding['findingType']}: {finding['issueCode']}: {finding['findingDetails']}")


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
    parser.add_argument("--iam-policy", action="store_true",
                        help="write the least-privilege runtime policy to iam/ and validate it with Access Analyzer")
    args = parser.parse_args()
    if not (args.bucket or args.list_inference_profiles or args.guardrail or args.upload_data or args.iam_policy):
        parser.error("choose --bucket, --upload-data, --guardrail, --iam-policy and/or --list-inference-profiles")
    if args.iam_policy:
        write_policy()
        validate_policy()
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
