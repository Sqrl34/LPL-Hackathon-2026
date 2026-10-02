"""AWS plumbing: S3, Comprehend, Bedrock calls with pacing and retries (owner: C)."""

# TODO(C): boto3 clients pinned to config.AWS_REGION
# TODO(C): load_client / load_transcripts / save_upload (S3)
# TODO(C): extract_people(text) via Comprehend DetectEntities (PERSON)
# TODO(C): converse(model_id, prompt, guardrail=False) with BEDROCK_PAUSE_SECONDS pause
#          and backoff on ThrottlingException
