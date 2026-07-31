"""Attributes LLM cost to the agent that spent it, and rolls it up.

Rollups:
    runs.cost_usd                 = sum of that run's cost_events
    orchestrations.total_cost_usd = sum of its cost_events

The orchestration total sums cost_events rather than runs.cost_usd because the
planner's spend belongs to no run; rolling up the runs would drop it.

Agents execute in separate department processes, so they cannot write here
directly -- they return `cost_events[]` in the MCP tool result and the
orchestrator records them through this class.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass

from store import queries


@dataclass
class CostEvent:
    department: str
    agent: str
    amount_usd: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CostEvent":
        return cls(
            department=d["department"],
            agent=d["agent"],
            amount_usd=float(d["amount_usd"]),
        )


class CostMeter:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record(
        self,
        orchestration_id: str,
        department: str,
        agent: str,
        amount_usd: float,
        *,
        run_id: str | None = None,
    ) -> None:
        queries.record_cost_event(
            self.conn,
            orchestration_id,
            department=department,
            agent=agent,
            amount_usd=amount_usd,
            run_id=run_id,
        )

    def record_events(
        self,
        orchestration_id: str,
        events: list[dict],
        *,
        run_id: str | None = None,
    ) -> float:
        """Record the cost_events an MCP tool returned. Returns their total."""
        total = 0.0
        for raw in events or []:
            event = CostEvent.from_dict(raw)
            self.record(
                orchestration_id,
                event.department,
                event.agent,
                event.amount_usd,
                run_id=run_id,
            )
            total += event.amount_usd
        return total

    def total_for(self, orchestration_id: str) -> float:
        return queries.total_cost(self.conn, orchestration_id)
