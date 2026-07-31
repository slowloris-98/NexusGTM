from __future__ import annotations

import pytest

from core.llm import LLMResult
from core.registry import AgentSpec
from store import db


@pytest.fixture
def conn(tmp_path):
    connection = db.init_db(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def specs() -> list[AgentSpec]:
    return [
        AgentSpec(
            department="marketing",
            department_name="Marketing",
            agent="enrichment",
            description="raw lead -> firmographics",
            input_schema={
                "type": "object",
                "properties": {"lead": {"type": "object"}},
                "required": ["lead"],
            },
            endpoint="http://127.0.0.1:8101/mcp",
        ),
        AgentSpec(
            department="revops",
            department_name="RevOps",
            agent="scoring",
            description="enriched lead -> fit score",
            input_schema={
                "type": "object",
                "properties": {"firmographics": {"type": "object"}},
                "required": ["firmographics"],
            },
            endpoint="http://127.0.0.1:8102/mcp",
        ),
    ]


def result(cost: float = 0.001, parsed=None) -> LLMResult:
    return LLMResult(
        text="",
        model="test-model",
        input_tokens=100,
        output_tokens=50,
        cost_usd=cost,
        parsed=parsed,
    )
