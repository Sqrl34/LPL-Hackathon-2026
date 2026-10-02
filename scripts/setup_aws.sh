#!/usr/bin/env bash
# Run only in a terminal where the hackathon temporary AWS credentials are exported.
# Creates Second Look's private, encrypted S3 store and shows available Bedrock profiles.

set -euo pipefail

readonly AWS_REGION="us-east-1"
readonly S3_BUCKET="${S3_BUCKET:-second-look-lpl-432810293903}"

if ! aws sts get-caller-identity --region "$AWS_REGION" >/dev/null; then
  echo "AWS credentials are missing or expired. Export the temporary event credentials first." >&2
  exit 1
fi

if aws s3api head-bucket --bucket "$S3_BUCKET" 2>/dev/null; then
  echo "Bucket already exists: $S3_BUCKET"
else
  aws s3api create-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION"
  echo "Created bucket: $S3_BUCKET"
fi

aws s3api put-public-access-block \
  --bucket "$S3_BUCKET" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-encryption \
  --bucket "$S3_BUCKET" \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'

for key in clients/ transcripts/walter/ transcripts/maria/ transcripts/linda/ uploads/; do
  aws s3api put-object --bucket "$S3_BUCKET" --key "$key" >/dev/null
done

echo
echo "S3 is ready: $S3_BUCKET (private, encrypted, us-east-1)"
echo
echo "Available Claude inference profiles (copy the IDs for Sonnet 4.6 and Haiku 4.5):"
aws bedrock list-inference-profiles --region "$AWS_REGION" \
  --query 'inferenceProfileSummaries[?contains(inferenceProfileName, `Claude`)].[inferenceProfileName,inferenceProfileId]' \
  --output table
