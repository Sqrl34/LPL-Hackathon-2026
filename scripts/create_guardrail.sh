#!/usr/bin/env bash
# Run only in a terminal where the hackathon temporary AWS credentials are exported.
# Creates the safety guardrail used by Second Look's Bedrock calls.

set -euo pipefail

readonly AWS_REGION="us-east-1"
readonly GUARDRAIL_NAME="SecondLookSafety"

if ! aws sts get-caller-identity --region "$AWS_REGION" >/dev/null; then
  echo "AWS credentials are missing or expired. Export the temporary event credentials first." >&2
  exit 1
fi

existing_id="$(aws bedrock list-guardrails --region "$AWS_REGION" \
  --query "guardrails[?name=='$GUARDRAIL_NAME'].id | [0]" --output text)"

if [[ -n "$existing_id" && "$existing_id" != "None" ]]; then
  guardrail_id="$existing_id"
  echo "Using existing guardrail: $GUARDRAIL_NAME"
else
  guardrail_id="$(aws bedrock create-guardrail \
    --region "$AWS_REGION" \
    --name "$GUARDRAIL_NAME" \
    --description "Blocks medical diagnoses and masks financial identifiers in Second Look." \
    --blocked-input-messaging "Second Look cannot provide medical diagnoses. Please review the documented conversation signals with a qualified professional." \
    --blocked-outputs-messaging "Second Look cannot provide medical diagnoses. Please review the documented conversation signals with a qualified professional." \
    --topic-policy-config '{"topicsConfig":[{"name":"Medical diagnosis","definition":"Medical or mental-health diagnosis, including identifying, confirming, or speculating that a person has dementia, Alzheimer’s disease, cognitive impairment, or any other condition.","examples":["Does Walter have dementia?","Is Maria suffering from Alzheimer’s?","Diagnose this client based on their call."],"type":"DENY","inputAction":"BLOCK","outputAction":"BLOCK","inputEnabled":true,"outputEnabled":true}],"tierConfig":{"tierName":"CLASSIC"}}' \
    --sensitive-information-policy-config '{"piiEntitiesConfig":[{"type":"US_SOCIAL_SECURITY_NUMBER","action":"ANONYMIZE"},{"type":"US_BANK_ACCOUNT_NUMBER","action":"ANONYMIZE"},{"type":"US_BANK_ROUTING_NUMBER","action":"ANONYMIZE"}]}' \
    --query 'guardrailId' --output text)"
  echo "Created guardrail: $GUARDRAIL_NAME"
fi

echo
echo "Add these values to .env:"
echo "GUARDRAIL_ID=$guardrail_id"
echo "GUARDRAIL_VERSION=DRAFT"
