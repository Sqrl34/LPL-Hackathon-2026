"""Second Look agent: one Strands agent on Claude Sonnet, three tools, the Bedrock Guardrail (owner: C).

Optional and first to cut: the screen and scoring work without it.

    python agent.py walter "What changed in Walter's last call?"
    python agent.py walter "Does Walter have dementia?"      # the guardrail refuses

Safety:
- The guardrail checks only the advisor's question on input (guardrail_latest_message), so transcripts
  returned by tools can't get the agent blocked, and it checks every answer on output.
- Every model call waits on the same pacing gate as the rest of the app (about 1 call per 1.2 s).
- Tools never raise: on any AWS failure they return "Analysis unavailable, review manually".
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

import aws_clients
import config
import scoring
from aws_clients import AWSServiceError, converse
from config import AWS_REGION, GUARDRAIL_ID, GUARDRAIL_VERSION, SONNET_MODEL_ID

UNAVAILABLE = "Analysis unavailable, review manually."

SYSTEM_PROMPT = (
    "You are Second Look, an assistant for financial advisors. You help an advisor notice when a client's "
    "behavior across calls changes in ways that could put their money at risk. Use the tools: "
    "get_call_history to read the client's calls, compare_to_baseline to score a call against the client's "
    "own earlier calls, and draft_next_steps for suggested follow-ups. Base every statement on tool results "
    "and use only the quotes exactly as the tools return them; never paraphrase a quote or describe a request "
    "as pending or approved unless a tool says so. Never name, suggest, or speculate about a medical or mental "
    "health condition. Every draft needs human approval; never say anything was sent or done. If a tool "
    "returns an error, say: Analysis unavailable, review manually. Only call draft_next_steps when the "
    "advisor asks what to do next. Reply in plain text: no headings, tables, or emoji, and at most 120 words."
)


# ---------- Data (S3 first, local data/ copy as backup) ----------

_analysis_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

# What the screen is already showing, per client, while ask() runs. Tools use it instead of
# re-scoring, so the agent agrees with the screen and saves Bedrock calls.
_screen_context: dict[str, dict[str, Any]] = {}


def _load(client_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    client_id = aws_clients._client_id(client_id)  # raises ValueError on anything but a plain ID
    if client_id in _screen_context:
        shown = _screen_context[client_id]
        return shown["client"], shown["calls"], shown["source"]
    if config.S3_BUCKET:
        try:
            calls = aws_clients.load_transcripts(client_id)
            if calls:
                return aws_clients.load_client(client_id), calls, "s3"
        except (AWSServiceError, ValueError):
            pass
    return scoring.load_client_local(client_id), scoring.load_calls_local(client_id), "local"


def _results(client: dict[str, Any], calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scores for every call, each against the calls before it. Cached in memory for this process."""
    shown = _screen_context.get(client.get("client_id", ""))
    if shown and shown.get("results") is not None:
        return shown["results"]
    key = (client["client_id"], hashlib.sha256(json.dumps(calls, sort_keys=True).encode()).hexdigest())
    if key not in _analysis_cache:
        results = scoring.analyze_client(client, calls, with_next_steps=False)
        if any(r["level"] == "unavailable" for r in results):
            return results  # don't cache failures, so a retry can succeed
        _analysis_cache[key] = results
    return _analysis_cache[key]


def _failure(err: Exception) -> dict[str, Any]:
    if isinstance(err, (FileNotFoundError, ValueError)):
        return {"error": "Unknown client ID. Known clients: walter, maria, linda."}
    return {"error": UNAVAILABLE, "reason": scoring.friendly_error(err)}


def _find_call(results: list[dict[str, Any]], call_number: int | None) -> dict[str, Any] | None:
    if call_number is None:
        return results[-1] if results else None
    return next((r for r in results if r["call_number"] == int(call_number)), None)


# ---------- The three tools ----------

def get_call_history(client_id: str) -> dict[str, Any]:
    """Load a client's profile and all of their call transcripts, oldest first.

    Args:
        client_id: The client's ID, for example "walter", "maria" or "linda".
    """
    try:
        client, calls, source = _load(client_id)
    except Exception as err:  # never let a tool crash the agent
        return _failure(err)
    return {
        "client": client,
        "source": "private S3 bucket" if source == "s3" else "local backup files",
        "calls": [{"call_number": c["call_number"], "date": c.get("date"), "transcript": c["transcript"]}
                  for c in calls],
    }


def compare_to_baseline(client_id: str, call_number: int | None = None) -> dict[str, Any]:
    """Score one call against the client's own earlier calls on the 5 warning signs.

    Returns each signal's 0-3 score with a direct quote, the total, the color set by fixed rules
    (green, yellow, red), new people, and a plain-language summary.

    Args:
        client_id: The client's ID, for example "walter".
        call_number: Which call to score. Leave empty for the latest call.
    """
    try:
        client, calls, _ = _load(client_id)
        result = _find_call(_results(client, calls), call_number)
    except Exception as err:
        return _failure(err)
    if result is None:
        return {"error": f"No call {call_number} for {client_id}."}
    if result["level"] == "unavailable":
        return {"call_number": result["call_number"], "error": UNAVAILABLE, "reason": result.get("error", "")}
    return {
        "call_number": result["call_number"],
        "date": result["date"],
        "level": result["level"],
        "total": result["total"],
        "summary": result["summary"],
        "signals": {name: sig for name, sig in result["signals"].items() if sig["score"] > 0},
        "new_people": result["new_people"],
    }


def draft_next_steps(client_id: str, call_number: int | None = None) -> dict[str, Any]:
    """Draft follow-ups for a yellow or red call: an advisor check-in script and, for red, a message to the
    trusted contact and a possible-hold note. Every draft requires human approval; nothing is sent.

    Args:
        client_id: The client's ID, for example "walter".
        call_number: Which call. Leave empty for the latest call.
    """
    try:
        client, calls, _ = _load(client_id)
        result = _find_call(_results(client, calls), call_number)
        if result is None:
            return {"error": f"No call {call_number} for {client_id}."}
        if result["level"] == "unavailable":
            return {"error": UNAVAILABLE, "reason": result.get("error", "")}
        if result["level"] == "green":
            return {"level": "green", "message": "This call is green, so no follow-up is suggested."}
        steps = result.get("next_steps")  # drafts already on screen: reuse them, no new Haiku call
        if not steps:
            transcript = next(c["transcript"] for c in calls if c["call_number"] == result["call_number"])
            steps = scoring.draft_next_steps(result, client, transcript)
    except Exception as err:
        return _failure(err)
    return {"level": result["level"], **{k: v for k, v in (steps or {}).items() if v}}


def ask_with_guardrail(question: str) -> str:
    """Safe direct path for the UI's Guardrails refusal demonstration (no tools)."""
    return converse(SONNET_MODEL_ID, question, guardrail=True, guard_text=question, max_tokens=300)


# ---------- The agent ----------

def _hooks() -> tuple[Any, list[str]]:
    from strands.hooks import AfterToolCallEvent, BeforeModelCallEvent, HookProvider, HookRegistry

    tools_used: list[str] = []

    class SecondLookHooks(HookProvider):
        def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
            registry.add_callback(BeforeModelCallEvent, lambda event: aws_clients.wait_for_bedrock_slot())
            registry.add_callback(AfterToolCallEvent, lambda event: tools_used.append(event.tool_use["name"]))

    return SecondLookHooks(), tools_used


def build_agent() -> tuple[Any, list[str]]:
    """Create the Strands agent. Returns (agent, list that fills with the names of tools it calls)."""
    if not SONNET_MODEL_ID or not GUARDRAIL_ID:
        raise AWSServiceError("SONNET_MODEL_ID and GUARDRAIL_ID are required; use direct scoring instead.")
    try:
        from strands import Agent, ModelRetryStrategy, tool
        from strands.models import BedrockModel
    except ImportError as exc:
        raise AWSServiceError("Strands is not installed; use direct scoring instead.") from exc
    model = BedrockModel(
        model_id=SONNET_MODEL_ID,
        region_name=AWS_REGION,
        guardrail_id=GUARDRAIL_ID,
        guardrail_version=GUARDRAIL_VERSION,
        guardrail_latest_message=True,  # input check covers the question, not tool results (transcripts)
        temperature=0,
        max_tokens=800,
        streaming=False,
    )
    hooks, tools_used = _hooks()
    agent = Agent(
        model=model,
        tools=[tool(get_call_history), tool(compare_to_baseline), tool(draft_next_steps)],
        system_prompt=SYSTEM_PROMPT,
        hooks=[hooks],
        retry_strategy=ModelRetryStrategy(max_attempts=4, initial_delay=2, max_delay=16),
        callback_handler=None,
    )
    return agent, tools_used


def ask(client_id: str, question: str, *, client: dict[str, Any] | None = None,
        calls: list[dict[str, Any]] | None = None, results: list[dict[str, Any]] | None = None,
        source: str = "screen") -> dict[str, Any]:
    """Ask the agent about one client. Never raises.

    Pass client, calls and results (what the screen shows) so the tools use those exact scores
    instead of scoring again. Returns {"answer", "blocked", "tools_used", "error"}; blocked is
    True when the guardrail refused.
    """
    key = str(client_id or "").strip().lower()
    if client is not None and calls is not None:
        _screen_context[key] = {"client": client, "calls": calls, "results": results, "source": source}
    try:
        agent, tools_used = build_agent()
        result = agent(f"Client ID: {client_id}\nAdvisor's question: {question}")
    except Exception as err:
        return {"answer": UNAVAILABLE, "blocked": False, "tools_used": [], "error": scoring.friendly_error(err)}
    finally:
        _screen_context.pop(key, None)
    blocked = result.stop_reason == "guardrail_intervened"
    answer = str(result).strip() or UNAVAILABLE
    if not blocked:
        answer = scoring._scrub(answer)  # last line of defense against diagnosis wording
    return {"answer": answer, "blocked": blocked, "tools_used": tools_used, "error": None}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit('Usage: python agent.py <client_id> "<question>"')
    reply = ask(sys.argv[1], sys.argv[2])
    print(reply["answer"])
    print(f"\n[blocked by guardrail: {reply['blocked']} | tools used: {', '.join(reply['tools_used']) or 'none'}]")
    if reply["error"]:
        print(f"[error: {reply['error']}]")
