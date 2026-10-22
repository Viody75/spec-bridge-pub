"""Deterministic structural checks for a SpecBridge analysis response."""

import json

EXPECTED_ROLES = {
    "business_analyst",
    "project_manager",
    "developer",
    "quality_assurance",
    "designer",
}


def _response_payload(instance):
    response = instance.get("response") or {}
    for part in reversed(response.get("parts") or []):
        if part.get("text"):
            return json.loads(part["text"])
    raise ValueError("response has no JSON text part")


def _prompt_payload(instance):
    prompt = instance.get("prompt") or {}
    for part in prompt.get("parts") or []:
        if part.get("text"):
            return json.loads(part["text"])
    raise ValueError("prompt has no JSON text part")


def _evidence_items(value):
    if isinstance(value, dict):
        yield from value.get("evidence_refs", [])
        for nested in value.values():
            yield from _evidence_items(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _evidence_items(nested)


def _evidence_is_grounded(evidence, sources):
    source = sources.get(evidence.get("source_id"))
    if not source or evidence.get("quote") not in source.get("content", ""):
        return False
    source_locator = source.get("locator") or {}
    for key, value in (evidence.get("locator") or {}).items():
        if value is None:
            continue
        if source_locator.get(key) == value:
            continue
        if key in {"timestamp_start", "timestamp_end", "speaker", "section"}:
            if str(value) in source.get("content", ""):
                continue
        return False
    return True


def evaluate(instance):
    try:
        payload = _response_payload(instance)
        workflow_input = _prompt_payload(instance)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"score": 0.0, "explanation": f"invalid JSON result: {exc}"}

    criteria = {}
    sources = {
        item["source_id"]: item
        for item in workflow_input.get("request", {}).get("sources", [])
    }
    evidence = list(_evidence_items(payload))
    criteria["evidence_and_locators_grounded"] = bool(evidence) and all(
        _evidence_is_grounded(item, sources) for item in evidence
    )
    requirements = payload.get("extraction", {}).get("requirements", [])
    criteria["requirements_grounded"] = bool(requirements) and all(
        item.get("evidence_refs") or item.get("assumptions") for item in requirements
    )
    candidate_ids = {item.get("candidate_id") for item in requirements}

    for key, round_number in (("initial_reviews", 1), ("rebuttals", 2)):
        reviews = payload.get(key, [])
        criteria[f"{key}_all_roles"] = {
            item.get("role") for item in reviews
        } == EXPECTED_ROLES and all(
            item.get("round") == round_number for item in reviews
        )
        criteria[f"{key}_all_candidates"] = all(
            {finding.get("candidate_id") for finding in item.get("findings", [])}
            == candidate_ids
            for item in reviews
        )

    criteria["bounded_to_two_rounds"] = all(
        item.get("round") in {1, 2}
        for key in ("initial_reviews", "rebuttals")
        for item in payload.get(key, [])
    )
    criteria["draft_only"] = (
        payload.get("document", {}).get("publication_ready") is False
    )
    if payload.get("conflicts", {}).get("conflicts"):
        criteria["conflicts_require_human"] = (
            payload.get("status") == "human_context_required"
        )
    else:
        criteria["conflicts_require_human"] = True

    passed = sum(criteria.values())
    failed = [name for name, ok in criteria.items() if not ok]
    return {
        "score": passed / len(criteria),
        "explanation": "passed" if not failed else f"failed: {', '.join(failed)}",
    }
