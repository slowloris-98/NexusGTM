"""The guardrails are what make this production rather than a party trick, so
they are what the tests pin down. No network, no key: core.llm.llm_call is
patched to return fixed LLMResult values.
"""

from __future__ import annotations

import json

import pytest

from conftest import result
from core import llm as llm_module
from core import registry
from core.orchestrator import Orchestrator
from core.registry import Discovery


def decision(agent, payload, *, finished=False, outcome=None, cost=0.001):
    return result(
        cost=cost,
        parsed={
            "chosen_agent": agent,
            "rationale": f"picked {agent}",
            "candidates_considered": ["enrichment", "scoring"],
            "input_payload": json.dumps(payload),
            "finished": finished,
            "outcome": outcome,
        },
    )


@pytest.fixture
def patched(monkeypatch, specs):
    """Wire the orchestrator to scripted planner decisions and agent results."""

    async def fake_discover():
        return Discovery(agents=specs, unreachable=[])

    monkeypatch.setattr(registry, "discover", fake_discover)

    def install(decisions, agent_result):
        """`decisions` is either a scripted list or a callable producing them."""
        if callable(decisions):
            fake_llm_call = lambda prompt, **kwargs: decisions()  # noqa: E731
        else:
            it = iter(decisions)

            def fake_llm_call(prompt, **kwargs):
                try:
                    return next(it)
                except StopIteration:
                    return decision(None, {}, finished=True, outcome="qualified")

        monkeypatch.setattr(llm_module, "llm_call", fake_llm_call)
        monkeypatch.setattr(registry, "call_agent", agent_result)

    return install


async def test_budget_halt_stops_the_run(conn, patched):
    """A run that blows its budget halts instead of spending more."""

    async def expensive_agent(spec, oid, payload):
        return {
            "output": {"firmographics": {"industry": "software"}},
            "status": "ok",
            "cost_events": [
                {"department": spec.department, "agent": spec.agent, "amount_usd": 0.40}
            ],
        }

    patched(
        [
            decision("enrichment", {"lead": {"a": 1}}),
            decision("scoring", {"firmographics": {"industry": "software"}}),
        ],
        expensive_agent,
    )

    orchestrator = Orchestrator(conn)
    orchestrator.budget_usd = 0.001

    outcome = await orchestrator.run("ACME-1", {"company": "Acme"})

    assert outcome["status"] == "halted_budget"
    assert outcome["total_cost_usd"] > 0.001
    # It halted after the first agent, not after working through the playbook.
    runs = conn.execute("SELECT * FROM runs").fetchall()
    assert len(runs) == 1


async def test_loop_detection_halts_on_repeated_call(conn, patched):
    """The same agent on the same input is never worth paying for twice."""

    async def cheap_agent(spec, oid, payload):
        return {"output": {}, "status": "ok", "cost_events": []}

    same = {"lead": {"company": "Acme"}}
    patched(
        [decision("enrichment", same), decision("enrichment", same)],
        cheap_agent,
    )

    orchestrator = Orchestrator(conn)
    outcome = await orchestrator.run("ACME-2", {"company": "Acme"})

    assert outcome["status"] == "halted_loop"
    assert len(conn.execute("SELECT * FROM runs").fetchall()) == 1


async def test_invalid_payload_is_replanned_not_crashed(conn, patched):
    """Scoring without firmographics is a re-plan, not an exception."""
    calls = []

    async def tracking_agent(spec, oid, payload):
        calls.append(spec.agent)
        return {
            "output": {"firmographics": {"industry": "software"}},
            "status": "ok",
            "cost_events": [],
        }

    patched(
        [
            # Planner reaches for scoring first; the blackboard cannot satisfy it.
            decision("scoring", {}),
            decision("enrichment", {"lead": {"company": "Acme"}}),
            decision(None, {}, finished=True, outcome="qualified"),
        ],
        tracking_agent,
    )

    orchestrator = Orchestrator(conn)
    outcome = await orchestrator.run("ACME-3", {"company": "Acme"})

    assert outcome["status"] == "completed"
    # The invalid call never reached the department.
    assert calls == ["enrichment"]

    rows = {r["agent"]: r["status"] for r in conn.execute("SELECT * FROM runs").fetchall()}
    assert rows["scoring"] == "invalid_input"
    assert rows["enrichment"] == "ok"


async def test_step_ceiling_halts_a_wandering_planner(conn, patched):
    """A planner that never finishes is stopped by the step ceiling."""
    counter = {"n": 0}

    async def cheap_agent(spec, oid, payload):
        return {"output": {}, "status": "ok", "cost_events": []}

    def endless():
        counter["n"] += 1
        # A fresh payload each time, so loop detection never fires and only the
        # step ceiling can stop this.
        return decision("enrichment", {"lead": {"n": counter["n"]}})

    patched(endless, cheap_agent)

    orchestrator = Orchestrator(conn)
    orchestrator.max_steps = 3
    outcome = await orchestrator.run("ACME-4", {"company": "Acme"})

    assert outcome["status"] == "halted_steps"
    assert outcome["steps"] <= 4


async def test_decision_trail_is_recorded_with_rationale(conn, patched):
    """Every routing decision is logged with its rationale -- paths vary per run."""

    async def cheap_agent(spec, oid, payload):
        return {"output": {"firmographics": {}}, "status": "ok", "cost_events": []}

    patched(
        [
            decision("enrichment", {"lead": {"company": "Acme"}}),
            decision(None, {}, finished=True, outcome="qualified"),
        ],
        cheap_agent,
    )

    orchestrator = Orchestrator(conn)
    outcome = await orchestrator.run("ACME-5", {"company": "Acme"})

    decisions = conn.execute(
        "SELECT * FROM decisions WHERE orchestration_id = ? ORDER BY step_no",
        (outcome["orchestration_id"],),
    ).fetchall()

    assert len(decisions) == 2
    assert decisions[0]["chosen_agent"] == "enrichment"
    assert decisions[0]["rationale"]
    assert json.loads(decisions[0]["candidates_considered"]) == ["enrichment", "scoring"]
