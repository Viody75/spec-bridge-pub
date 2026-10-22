"""Local LLM judge for SpecBridge quality and approval-gate behavior."""

import threading

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

_local = threading.local()


class _Verdict(BaseModel):
    score: float = Field(ge=0, le=1)
    explanation: str


def _client() -> genai.Client:
    client = getattr(_local, "client", None)
    if client is None:
        client = _local.client = genai.Client()
    return client


def evaluate(instance):
    reference = instance.get("reference")
    rubric = (
        "Grade the SpecBridge analysis from 0.0 to 1.0 for source traceability, "
        "role adherence, rationale quality, ambiguity handling, and preserving human "
        "approval gates. Penalize invented facts, invented thresholds, missing role "
        "findings, hidden dissent, automatic conflict resolution, or publication_ready "
        "being true."
    )
    prompt = (
        f"You are an expert requirements-engineering evaluator. {rubric}\n"
        f"User Prompt: {instance.get('prompt', '')}\n"
        f"Final Response: {instance.get('response', '')}\n"
        f"Expected behavior: {reference}\n"
        f"Full Agent Trace: {instance.get('agent_data', '')}\n"
    )
    response = _client().models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=_Verdict,
        ),
    )
    verdict = response.parsed
    if verdict is None:
        return {"score": 0.0, "explanation": response.text or ""}
    return {"score": verdict.score, "explanation": verdict.explanation}
