"""Optional Strands wrapper for the three Second Look tools (owner: C)."""

from __future__ import annotations

from typing import Any

from aws_clients import AWSServiceError, converse, load_client, load_transcripts, new_people_in_call
from config import AWS_REGION, GUARDRAIL_ID, GUARDRAIL_VERSION, SONNET_MODEL_ID


def get_call_history(client_id: str) -> dict[str, Any]:
    """Load client information and all historical transcripts from private S3."""
    return {"client": load_client(client_id), "calls": load_transcripts(client_id)}


def compare_to_baseline(new_call: str, past_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Use A's scorer plus Comprehend-derived people absent from prior calls."""
    try:
        from scoring import score_call
    except ImportError as exc:
        raise AWSServiceError("Scoring is unavailable; review manually.") from exc
    return score_call(new_call, past_calls, new_people_in_call(new_call, past_calls))


def draft_next_steps(signals: dict[str, Any], trusted_contact: str) -> dict[str, Any]:
    """Use A's Haiku drafting routine; all drafts remain for human approval."""
    try:
        from scoring import draft_next_steps as draft
    except ImportError as exc:
        raise AWSServiceError("Next-step drafting is unavailable; review manually.") from exc
    return draft(signals, {"trusted_contact": trusted_contact})


def ask_with_guardrail(question: str) -> str:
    """Safe direct path for the UI's Guardrails refusal demonstration."""
    return converse(SONNET_MODEL_ID, question, guardrail=True, max_tokens=300)


def build_agent() -> Any:
    """Create the optional one-agent Strands experience on Sonnet in us-east-1."""
    if not SONNET_MODEL_ID or not GUARDRAIL_ID:
        raise AWSServiceError("SONNET_MODEL_ID and GUARDRAIL_ID are required; use direct scoring instead.")
    try:
        from strands import Agent, tool
        from strands.models import BedrockModel
    except ImportError as exc:
        raise AWSServiceError("Strands is not installed; use direct scoring instead.") from exc
    model = BedrockModel(
        model_id=SONNET_MODEL_ID,
        region_name=AWS_REGION,
        guardrail_id=GUARDRAIL_ID,
        guardrail_version=GUARDRAIL_VERSION,
    )
    return Agent(
        model=model,
        tools=[tool(get_call_history), tool(compare_to_baseline), tool(draft_next_steps)],
        system_prompt=("Second Look supports advisors. Never diagnose a medical or mental condition. "
                       "Cite transcript evidence, require human approval, and say review manually on tool failure."),
    )
