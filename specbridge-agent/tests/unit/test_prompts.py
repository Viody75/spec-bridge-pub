from __future__ import annotations

from google.adk.flows.llm_flows.prompt._instructions_utils import (
    _TEMPLATE_VAR_PATTERN,
    _is_valid_state_name,
)

from app.workflow import prompts


def _all_prompts() -> dict[str, str]:
    found = {
        name: value
        for name, value in vars(prompts).items()
        if name.endswith("_PROMPT") and isinstance(value, str)
    }
    for role in prompts.ROLE_CRITERIA:
        found[f"initial_{role}"] = prompts.initial_review_prompt(role)
        found[f"rebuttal_{role}"] = prompts.rebuttal_prompt(role)
    return found


def test_prompts_have_no_adk_state_placeholders() -> None:
    # ADK replaces {name} in an instruction with session state: a missing name
    # fails the run, and {name?} is silently removed from the prompt.
    for name, text in _all_prompts().items():
        placeholders = [
            match.group()
            for match in _TEMPLATE_VAR_PATTERN.finditer(text)
            if _is_valid_state_name(
                match.group().lstrip("{").rstrip("}").strip().removesuffix("?")
            )
        ]
        assert not placeholders, f"{name} contains {placeholders}"
