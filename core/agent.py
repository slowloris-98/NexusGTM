"""Agent contract, and the helper that exposes an agent as an MCP tool.

Agents are single-responsibility and stateless -- all state lives in the store.
They never call each other; the orchestrator is the only caller.
"""

from __future__ import annotations

import contextlib
import os
from abc import ABC, abstractmethod
from typing import Any

from core import llm as llm_module
from core.costmeter import CostEvent
from core.llm import LLMResult


class Spender:
    """Per-invocation LLM handle that accumulates this agent's cost events.

    Cost is collected per call rather than stored on the agent, which is what
    keeps agents stateless and safe under concurrent tool calls.
    """

    def __init__(self, department: str, agent: str):
        self.department = department
        self.agent = agent
        self.events: list[dict] = []

    def __call__(self, prompt: str, **kwargs: Any) -> LLMResult:
        # Module-qualified so tests can patch core.llm.llm_call in one place.
        result = llm_module.llm_call(prompt, **kwargs)
        self.events.append(
            CostEvent(self.department, self.agent, result.cost_usd).to_dict()
        )
        return result


class Agent(ABC):
    department: str
    name: str
    description: str
    input_schema: dict
    output_schema: dict

    @abstractmethod
    def execute(self, payload: dict, llm: Spender) -> dict:
        """Do the work. Raise to signal failure; `run` converts it to a status."""

    def run(self, payload: dict) -> dict:
        """The contract: {output, status, cost_events[]}.

        Cost events are returned rather than written, because agents run in a
        separate process from the orchestrator that owns the store.
        """
        spender = Spender(self.department, self.name)
        try:
            output: Any = self.execute(payload, spender)
            status = "ok"
        except Exception as exc:
            output = {"error": f"{type(exc).__name__}: {exc}"}
            status = "error"
        return {"output": output, "status": status, "cost_events": spender.events}


@contextlib.contextmanager
def _correlate(orchestration_id: str, agent: str):
    """Tag this process's LangSmith trace so it lines up with the orchestrator's.

    Agents run in their own process, so the trace cannot be inherited from
    context; the orchestration_id is what stitches the separate traces together.
    """
    if os.getenv("LANGSMITH_TRACING", "").lower() != "true":
        yield
        return
    try:
        from langsmith.run_helpers import tracing_context

        with tracing_context(
            metadata={"orchestration_id": orchestration_id, "agent": agent}
        ):
            yield
    except Exception:  # tracing must never break the call path
        yield


def register(mcp, agent: Agent) -> None:
    """Expose an agent as a tool on a FastMCP server.

    Every tool in the system has the same signature:
        (orchestration_id: str, payload: dict) -> {output, status, cost_events}
    """

    async def tool(orchestration_id: str, payload: dict) -> dict:
        with _correlate(orchestration_id, agent.name):
            return agent.run(payload)

    tool.__name__ = agent.name
    tool.__doc__ = agent.description

    # The uniform signature means FastMCP derives a generic schema for `payload`,
    # which tells the planner nothing. Publishing the real schema through meta
    # keeps the signature uniform and still gives the planner something to
    # validate against before it dispatches.
    mcp.tool(
        name=agent.name,
        description=agent.description,
        meta={
            "department": agent.department,
            "payload_schema": agent.input_schema,
        },
    )(tool)
