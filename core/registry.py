"""Department registry -- the extension point and the planner's action space.

Departments are config entries; agents are discovered as tools from each
department's MCP server. Core never imports a department.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client

from core.config import departments_config

log = logging.getLogger(__name__)


@dataclass
class AgentSpec:
    department: str
    department_name: str
    agent: str
    description: str
    input_schema: dict
    endpoint: str

    @property
    def qualified_name(self) -> str:
        return f"{self.department}.{self.agent}"


@dataclass
class Discovery:
    agents: list[AgentSpec] = field(default_factory=list)
    unreachable: list[dict] = field(default_factory=list)

    def by_name(self, agent: str) -> AgentSpec | None:
        """Accept either 'scoring' or 'revops.scoring'."""
        for spec in self.agents:
            if agent in (spec.agent, spec.qualified_name):
                return spec
        return None


async def _discover_one(dept: dict[str, Any]) -> tuple[list[AgentSpec], dict | None]:
    endpoint = dept["mcp_endpoint"]
    try:
        async with Client(endpoint) as client:
            tools = await client.list_tools()
    except Exception as exc:
        # A department that is down is not a fatal condition: the planner already
        # has a no-suitable-agent path, so a partial action space is valid. Name
        # the department and endpoint so the cause is obvious.
        log.warning(
            "department %r unreachable at %s: %s", dept["id"], endpoint, exc
        )
        return [], {
            "id": dept["id"],
            "name": dept.get("name", dept["id"]),
            "endpoint": endpoint,
            "error": f"{type(exc).__name__}: {exc}",
        }

    specs = []
    for tool in tools:
        meta = getattr(tool, "meta", None) or {}
        # Agents publish their real payload schema through meta because every
        # tool shares the uniform (orchestration_id, payload) signature.
        schema = meta.get("payload_schema") or getattr(tool, "inputSchema", {}) or {}
        specs.append(
            AgentSpec(
                department=meta.get("department", dept["id"]),
                department_name=dept.get("name", dept["id"]),
                agent=tool.name,
                description=tool.description or "",
                input_schema=schema,
                endpoint=endpoint,
            )
        )
    return specs, None


async def discover() -> Discovery:
    """Discover every agent across every registered department, concurrently."""
    departments = departments_config()
    results = await asyncio.gather(*(_discover_one(d) for d in departments))

    found = Discovery()
    for specs, failure in results:
        found.agents.extend(specs)
        if failure:
            found.unreachable.append(failure)
    return found


async def available_agents() -> list[AgentSpec]:
    return (await discover()).agents


async def call_agent(spec: AgentSpec, orchestration_id: str, payload: dict) -> dict:
    """Invoke an agent as an MCP tool. Returns {output, status, cost_events}."""
    async with Client(spec.endpoint) as client:
        result = await client.call_tool(
            spec.agent,
            {"orchestration_id": orchestration_id, "payload": payload},
        )
    data = result.data
    if not isinstance(data, dict):
        data = result.structured_content or {}
    return data
