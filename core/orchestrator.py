"""Goal-directed orchestrator. LangGraph planner + agent invocation + guardrails.

Not a fixed pipeline: the next agent is chosen at runtime from whatever the
registry currently exposes. Every routing decision is logged with its rationale,
and every run is bounded by a cost budget, a step ceiling, and loop detection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from typing import TypedDict

import jsonschema
from langgraph.graph import END, StateGraph

from core import planner, registry
from core.config import playbook
from core.costmeter import CostMeter
from core.registry import AgentSpec
from store import queries

log = logging.getLogger(__name__)

PLANNER_DEPARTMENT = "orchestrator"
PLANNER_AGENT = "planner"


class State(TypedDict, total=False):
    orchestration_id: str
    crm_reference_id: str
    blackboard: dict
    step_no: int
    history: list[dict]
    total_cost_usd: float
    status: str
    outcome: str | None
    observation: str | None
    seen: list[str]
    retries: int
    pending: dict | None
    agents: list[AgentSpec]
    unreachable: list[dict]


def _fingerprint(agent: str, payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(f"{agent}:{blob}".encode()).hexdigest()


class Orchestrator:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.meter = CostMeter(conn)
        guardrails = playbook().get("guardrails", {})
        self.max_steps = int(guardrails.get("max_steps", 12))
        self.budget_usd = float(guardrails.get("budget_usd", 0.50))
        self.graph = self._build_graph()

    # ------------------------------------------------------------------ nodes

    async def _plan(self, state: State) -> dict:
        oid = state["orchestration_id"]
        step_no = state["step_no"] + 1

        prompt = planner.build_prompt(
            crm_reference_id=state["crm_reference_id"],
            blackboard=state["blackboard"],
            agents=state["agents"],
            history=state["history"],
            step_no=step_no,
            total_cost_usd=state["total_cost_usd"],
            budget_usd=self.budget_usd,
            observation=state.get("observation"),
        )

        try:
            decision, result = planner.decide(prompt)
        except Exception as exc:
            queries.record_decision(
                self.conn,
                oid,
                step_no=step_no,
                chosen_agent=None,
                rationale=f"planner failed: {exc}",
                candidates_considered=[s.agent for s in state["agents"]],
            )
            return {"step_no": step_no, "status": "failed", "pending": None}

        # The planner is an LLM call too. Its spend is real and counts toward the
        # budget, so it is metered like any other agent.
        self.meter.record(
            oid, PLANNER_DEPARTMENT, PLANNER_AGENT, result.cost_usd
        )

        queries.record_decision(
            self.conn,
            oid,
            step_no=step_no,
            chosen_agent=decision.get("chosen_agent"),
            rationale=decision.get("rationale", ""),
            candidates_considered=decision.get("candidates_considered", []),
        )

        total = self.meter.total_for(oid)

        if decision.get("finished") or not decision.get("chosen_agent"):
            finished = bool(decision.get("finished"))
            return {
                "step_no": step_no,
                "total_cost_usd": total,
                "status": "completed" if finished else "no_agent",
                "outcome": decision.get("outcome") if finished else None,
                "pending": None,
                "observation": None,
            }

        spec = registry_lookup(state["agents"], decision["chosen_agent"])
        if spec is None:
            return {
                "step_no": step_no,
                "total_cost_usd": total,
                "status": "running",
                "observation": (
                    f"{decision['chosen_agent']!r} is not a registered agent. "
                    f"Choose from: {', '.join(s.agent for s in state['agents'])}."
                ),
                "pending": None,
                "retries": state.get("retries", 0) + 1,
            }

        return {
            "step_no": step_no,
            "total_cost_usd": total,
            "status": "running",
            "observation": None,
            "pending": {"spec": spec, "payload": decision.get("input_payload") or {}},
        }

    async def _invoke(self, state: State) -> dict:
        oid = state["orchestration_id"]
        pending = state["pending"]
        spec: AgentSpec = pending["spec"]
        payload: dict = pending["payload"]

        # Loop detection: the same agent on the same input is never worth paying
        # for twice.
        fingerprint = _fingerprint(spec.agent, payload)
        if fingerprint in state.get("seen", []):
            return {"status": "halted_loop", "pending": None}

        # Validate before dispatch. The planner's action space is every
        # registered agent, so it can and will pick one the blackboard cannot
        # satisfy; that is a re-plan, not a crash.
        try:
            jsonschema.validate(payload, spec.input_schema)
        except jsonschema.ValidationError as exc:
            run_id = queries.start_run(
                self.conn,
                oid,
                step_no=state["step_no"],
                department=spec.department,
                agent=spec.agent,
                input=payload,
            )
            queries.finish_run(
                self.conn, run_id, status="invalid_input", output={"error": exc.message}
            )
            retries = state.get("retries", 0) + 1
            return {
                "status": self._guardrail_status(
                    total_cost_usd=state["total_cost_usd"],
                    step_no=state["step_no"],
                    retries=retries,
                ),
                "pending": None,
                "retries": retries,
                "observation": (
                    f"{spec.agent} rejected the payload: {exc.message}. "
                    "Either assemble a valid payload from the blackboard, or run the "
                    "agent that produces the missing data first."
                ),
                "history": state["history"]
                + [
                    {
                        "step_no": state["step_no"],
                        "agent": spec.agent,
                        "status": "invalid_input",
                        "note": exc.message,
                    }
                ],
            }

        run_id = queries.start_run(
            self.conn,
            oid,
            step_no=state["step_no"],
            department=spec.department,
            agent=spec.agent,
            input=payload,
        )

        try:
            result = await registry.call_agent(spec, oid, payload)
        except Exception as exc:
            queries.finish_run(
                self.conn, run_id, status="error", output={"error": str(exc)}
            )
            retries = state.get("retries", 0) + 1
            return {
                "status": self._guardrail_status(
                    total_cost_usd=state["total_cost_usd"],
                    step_no=state["step_no"],
                    retries=retries,
                ),
                "pending": None,
                "retries": retries,
                "observation": f"{spec.agent} failed to execute: {exc}",
                "history": state["history"]
                + [
                    {
                        "step_no": state["step_no"],
                        "agent": spec.agent,
                        "status": "error",
                        "note": str(exc)[:200],
                    }
                ],
            }

        # Cost crosses the process boundary in the tool result -- agents hold no
        # database handle of their own.
        self.meter.record_events(oid, result.get("cost_events", []), run_id=run_id)

        status = result.get("status", "ok")
        output = result.get("output", {})
        queries.finish_run(self.conn, run_id, status=status, output=output)

        blackboard = dict(state["blackboard"])
        if status == "ok" and isinstance(output, dict):
            blackboard.update(output)

        note = None if status == "ok" else str(output.get("error", ""))[:200]
        total = self.meter.total_for(oid)
        retries = 0 if status == "ok" else state.get("retries", 0) + 1

        return {
            "status": self._guardrail_status(
                total_cost_usd=total, step_no=state["step_no"], retries=retries
            ),
            "pending": None,
            "blackboard": blackboard,
            "total_cost_usd": total,
            "seen": state.get("seen", []) + [fingerprint],
            "retries": retries,
            "observation": None if status == "ok" else f"{spec.agent} errored: {note}",
            "history": state["history"]
            + [
                {
                    "step_no": state["step_no"],
                    "agent": spec.agent,
                    "status": status,
                    "note": note,
                }
            ],
        }

    # ------------------------------------------------------------------ edges

    def _guardrail_status(self, *, total_cost_usd: float, step_no: int, retries: int) -> str:
        """The halt table. Returns 'running' when the orchestration may continue."""
        if total_cost_usd > self.budget_usd:
            return "halted_budget"
        if step_no >= self.max_steps:
            return "halted_steps"
        if retries > 2:
            return "failed"
        return "running"

    def _after_plan(self, state: State) -> str:
        if state["status"] in ("completed", "no_agent", "failed"):
            return END
        if state.get("pending"):
            return "invoke"
        # Planner named an unknown agent; re-plan unless it keeps happening.
        return END if state.get("retries", 0) > 2 else "plan"

    def _after_invoke(self, state: State) -> str:
        # Pure read: the invoke node already applied the guardrail table, because
        # a status assigned inside an edge function would not survive into the
        # graph's final state.
        return "plan" if state["status"] == "running" else END

    def _build_graph(self):
        graph = StateGraph(State)
        graph.add_node("plan", self._plan)
        graph.add_node("invoke", self._invoke)
        graph.set_entry_point("plan")
        graph.add_conditional_edges("plan", self._after_plan, {"invoke": "invoke", "plan": "plan", END: END})
        graph.add_conditional_edges("invoke", self._after_invoke, {"plan": "plan", END: END})
        return graph.compile()

    # ------------------------------------------------------------------- run

    async def run(self, crm_reference_id: str, lead: dict) -> dict:
        discovery = await registry.discover()
        if discovery.unreachable:
            log.warning(
                "starting with a partial action space; unreachable: %s",
                [d["id"] for d in discovery.unreachable],
            )

        oid = queries.create_orchestration(self.conn, crm_reference_id)

        initial: State = {
            "orchestration_id": oid,
            "crm_reference_id": crm_reference_id,
            "blackboard": {"lead": lead},
            "step_no": 0,
            "history": [],
            "total_cost_usd": 0.0,
            "status": "running",
            "outcome": None,
            "observation": None,
            "seen": [],
            "retries": 0,
            "pending": None,
            "agents": discovery.agents,
            "unreachable": discovery.unreachable,
        }

        try:
            final = await self.graph.ainvoke(
                initial, config={"recursion_limit": self.max_steps * 4}
            )
        except Exception as exc:
            log.exception("orchestration %s crashed", oid)
            queries.finalize_orchestration(
                self.conn, oid, status="failed", outcome="failed",
                final_result={"error": str(exc)},
            )
            return {"orchestration_id": oid, "status": "failed", "error": str(exc)}

        status = final.get("status", "completed")
        outcome = final.get("outcome") or _infer_outcome(status, final.get("blackboard", {}))

        queries.finalize_orchestration(
            self.conn,
            oid,
            status=status,
            outcome=outcome,
            final_result=final.get("blackboard", {}),
        )

        return {
            "orchestration_id": oid,
            "status": status,
            "outcome": outcome,
            "steps": final.get("step_no", 0),
            "total_cost_usd": self.meter.total_for(oid),
        }


def registry_lookup(agents: list[AgentSpec], name: str) -> AgentSpec | None:
    for spec in agents:
        if name in (spec.agent, spec.qualified_name):
            return spec
    return None


def _infer_outcome(status: str, blackboard: dict) -> str | None:
    """Fall back to the scoring verdict when the planner did not state an outcome."""
    if status != "completed":
        return "failed" if status in ("failed", "no_agent") else None
    if blackboard.get("draft") or blackboard.get("assigned_rep"):
        return "qualified"
    if blackboard.get("fit_score") is not None:
        return "disqualified"
    return None
