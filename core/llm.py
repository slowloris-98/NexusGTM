"""The single LLM interface. No provider SDK is touched at any call site.

Cost is computed here, in-process, from the usage the Responses API returns
inline. LangSmith ingests traces asynchronously, so its numbers are not available
when the orchestrator's budget guardrail needs them -- tracing is a debug surface,
not the system of record for cost.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from core.config import llm_config


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    parsed: Any = None
    raw: dict = field(default_factory=dict, repr=False)


class LLMError(RuntimeError):
    """Raised when the provider call fails or structured output cannot be parsed."""


@lru_cache(maxsize=1)
def _client():
    """OpenAI client, wrapped for LangSmith tracing when it is configured."""
    from openai import OpenAI

    client = OpenAI()
    if os.getenv("LANGSMITH_TRACING", "").lower() == "true":
        try:
            from langsmith.wrappers import wrap_openai

            return wrap_openai(client)
        except Exception:  # tracing must never break the call path
            pass
    return client


def price_of(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD for a call, from the price table in config/llm.yaml (per 1M tokens)."""
    prices = llm_config().get("prices", {})
    entry = prices.get(model)
    if entry is None:
        # Unknown model: don't guess a price and don't fail the run. A zero here
        # shows up as drift against the LangSmith trace, which is the signal to
        # update the table.
        return 0.0
    return (
        input_tokens * float(entry["input"]) + output_tokens * float(entry["output"])
    ) / 1_000_000


def strict_schema(name: str, properties: dict, required: list[str] | None = None) -> dict:
    """Build a Responses API json_schema format block.

    Strict mode requires every property to be listed in `required` and
    additionalProperties to be false; getting this wrong fails at request time
    with an opaque error, so it is enforced here rather than at each call site.
    """
    return {
        "type": "json_schema",
        "name": name,
        "strict": True,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": required if required is not None else list(properties),
            "additionalProperties": False,
        },
    }


def llm_call(
    prompt: str,
    *,
    instructions: str | None = None,
    model: str | None = None,
    schema: dict | None = None,
    **opts: Any,
) -> LLMResult:
    """Every LLM call in the system goes through here.

    `schema` is a format block from `strict_schema()`; when given, the response is
    parsed into `LLMResult.parsed`.
    """
    model = model or llm_config().get("default_model", "gpt-5.1")

    kwargs: dict[str, Any] = {"model": model, "input": prompt, **opts}
    if instructions:
        kwargs["instructions"] = instructions
    if schema:
        kwargs["text"] = {"format": schema}

    try:
        response = _client().responses.create(**kwargs)
    except Exception as exc:
        raise LLMError(f"{model} call failed: {exc}") from exc

    text = response.output_text or ""
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0

    parsed = None
    if schema:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"{model} returned unparseable structured output: {text[:200]}") from exc

    return LLMResult(
        text=text,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=price_of(model, input_tokens, output_tokens),
        parsed=parsed,
    )
