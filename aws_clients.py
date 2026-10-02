"""AWS plumbing for Second Look (owner: C).

All clients are regional, created lazily, and use credentials exported by the
event account. This module deliberately never reads or stores AWS credentials.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from config import AWS_REGION, BEDROCK_PAUSE_SECONDS, GUARDRAIL_ID, GUARDRAIL_VERSION, S3_BUCKET


class AWSServiceError(RuntimeError):
    """A safe, user-displayable failure from an AWS dependency."""


_bedrock_lock = threading.Lock()
_last_bedrock_call = 0.0
_clients: dict[str, Any] = {}


def _boto3_client(service_name: str) -> Any:
    """Create an AWS client only when a cloud operation is requested."""
    if service_name in _clients:
        return _clients[service_name]
    try:
        import boto3
    except ImportError as exc:
        raise AWSServiceError("AWS SDK is unavailable; review manually.") from exc
    _clients[service_name] = boto3.client(service_name, region_name=AWS_REGION)
    return _clients[service_name]


def _bucket() -> str:
    if not S3_BUCKET:
        raise AWSServiceError("S3_BUCKET is not configured; review manually.")
    return S3_BUCKET


def _client_id(value: Any) -> str:
    """Accept only the simple IDs used by the S3 key layout."""
    client_id = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", client_id):
        raise ValueError("Client ID must contain only letters, numbers, hyphens, or underscores.")
    return client_id


def _read_json(key: str) -> dict[str, Any]:
    try:
        response = _boto3_client("s3").get_object(Bucket=_bucket(), Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except AWSServiceError:
        raise
    except Exception as exc:
        raise AWSServiceError("Transcript data is unavailable; review manually.") from exc


def load_client(client_id: str) -> dict[str, Any]:
    """Load ``clients/<client_id>.json`` from the private bucket."""
    return _read_json(f"clients/{_client_id(client_id)}.json")


def load_transcripts(client_id: str, *, include_uploads: bool = True) -> list[dict[str, Any]]:
    """Load a client's transcript JSON files, ordered by call number then date."""
    client_id = _client_id(client_id)
    prefixes = [f"transcripts/{client_id}/"]
    if include_uploads:
        prefixes.append(f"uploads/{client_id}/")
    transcripts: list[dict[str, Any]] = []
    try:
        s3 = _boto3_client("s3")
        for prefix in prefixes:
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=_bucket(), Prefix=prefix):
                for item in page.get("Contents", []):
                    if item["Key"].endswith(".json"):
                        transcript = _read_json(item["Key"])
                        if transcript.get("client_id") != client_id:
                            raise AWSServiceError("Transcript client ID does not match its S3 folder; review manually.")
                        transcripts.append(transcript)
    except AWSServiceError:
        raise
    except Exception as exc:
        raise AWSServiceError("Transcript history is unavailable; review manually.") from exc
    return sorted(transcripts, key=lambda call: (call.get("call_number", 0), call.get("date", "")))


def save_upload(transcript: dict[str, Any]) -> str:
    """Save one validated demo upload under the private ``uploads/`` prefix."""
    client_id = _client_id(transcript.get("client_id"))
    call_number, text = transcript.get("call_number"), transcript.get("transcript")
    if (isinstance(call_number, bool) or not isinstance(call_number, int) or call_number < 1
            or not isinstance(text, str) or not text.strip()):
        raise ValueError("Upload needs client_id, call_number, and a non-empty transcript.")
    payload = dict(transcript)
    payload["client_id"] = client_id
    payload["call_number"] = call_number
    key = f"uploads/{client_id}/call-{call_number}.json"
    try:
        _boto3_client("s3").put_object(
            Bucket=_bucket(), Key=key, Body=json.dumps(payload, ensure_ascii=False).encode(),
            ContentType="application/json", ServerSideEncryption="AES256",
        )
    except AWSServiceError:
        raise
    except Exception as exc:
        raise AWSServiceError("Upload could not be saved; review manually.") from exc
    return key


def extract_people(text: str) -> list[str]:
    """Return unique Comprehend PERSON entities in their mention order."""
    if not isinstance(text, str) or not text.strip():
        return []
    try:
        response = _boto3_client("comprehend").detect_entities(Text=text, LanguageCode="en")
    except Exception as exc:
        raise AWSServiceError("People extraction is unavailable; review manually.") from exc
    people: list[str] = []
    seen: set[str] = set()
    for entity in response.get("Entities", []):
        name = entity.get("Text", "").strip()
        if entity.get("Type") == "PERSON" and name and name.casefold() not in seen:
            people.append(name)
            seen.add(name.casefold())
    return people


def new_people_in_call(new_call: str, past_calls: list[dict[str, Any]]) -> list[str]:
    """Find people in a new call who were absent from all prior calls."""
    prior = {person.casefold() for call in past_calls for person in extract_people(call.get("transcript", ""))}
    return [person for person in extract_people(new_call) if person.casefold() not in prior]


def _wait_for_bedrock_slot() -> None:
    global _last_bedrock_call
    with _bedrock_lock:
        delay = BEDROCK_PAUSE_SECONDS - (time.monotonic() - _last_bedrock_call)
        if delay > 0:
            time.sleep(delay)
        _last_bedrock_call = time.monotonic()


def converse(model_id: str, prompt: str, *, guardrail: bool = False, max_tokens: int = 1200) -> str:
    """Call Bedrock Converse with global pacing and throttling backoff."""
    if not model_id:
        raise AWSServiceError("Bedrock model ID is not configured; review manually.")
    if guardrail and not GUARDRAIL_ID:
        raise AWSServiceError("Bedrock Guardrail is not configured; review manually.")
    request: dict[str, Any] = {
        "modelId": model_id,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
    }
    if guardrail:
        request["guardrailConfig"] = {"guardrailIdentifier": GUARDRAIL_ID, "guardrailVersion": GUARDRAIL_VERSION}
    for attempt in range(4):
        _wait_for_bedrock_slot()
        try:
            response = _boto3_client("bedrock-runtime").converse(**request)
            answer = "".join(part.get("text", "") for part in response.get("output", {}).get("message", {}).get("content", []))
            if not answer:
                raise AWSServiceError("Bedrock returned no usable analysis; review manually.")
            return answer
        except AWSServiceError:
            raise
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in {"ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException"} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise AWSServiceError("Analysis unavailable, review manually.") from exc
    raise AWSServiceError("Analysis unavailable, review manually.")
