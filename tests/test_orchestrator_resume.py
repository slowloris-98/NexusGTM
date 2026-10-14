"""`Orchestrator.resume`: one orchestration record, continued across a phone call.

A voice call outlives the step that placed it, so the graph exits while the
conversation is still going and the outcome arrives minutes later. Rather than
spawn a second orchestration for the tail of the same job, the run is re-entered
on the original row.

What these tests pin down is that "one record" is true in every respect that
matters: the same id, the flow it ended on, step numbers that carry on, a cost
total that includes the telephony, and a step ceiling granted relative to where
it stopped rather than from zero.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import result
from core import llm as llm_module
from core import registry
from core.orchestrator import Orchestrator
from core.registry import AgentSpec, Discovery
from store import queries


def decision(agent, payload, *, finished=False, outcome=None, flow=None, cost=0.001):
    return result(
        cost=cost,
        parsed={
            "chosen_agent": agent,
            "rationale": f"picked {agent}",
            "candidates_considered": ["voice_call_outcome", "crm_sync"],
            "input_payload": json.dumps(payload),
            "finished": finished,
            "outcome": outcome,
            "flow": flow,
        },
    )


@pytest.fixture
def voice_specs() -> list[AgentSpec]:
    """The two agents a post-call resume actually chooses between."""
    return [
        AgentSpec(
            department="sales",
            department_name="Sales",
            agent="voice_call_outcome",
            description="read back a finished call",
            input_schema={"type": "object", "properties": {}, "required": []},
            endpoint="http://127.0.0.1:8103/mcp",
        ),
        AgentSpec(
            department="revops",
            department_name="RevOps",
            agent="crm_sync",
            description="write the verdict back",
            input_schema={
                "type": "object",
                "properties": {"firmographics": {"type": "object"}},
                "required": ["firmographics"],
            },
            endpoint="http://127.0.0.1:8102/mcp",
        ),
    ]


@pytest.fixture
def parked(conn, monkeypatch, voice_specs):
    """An orchestration finalized mid-job, the way a placed call leaves one.

    Built by hand rather than by running a graph, because that is exactly the
    state the webhook finds: five steps on the record, a blackboard carrying the
    firmographics, and a call id with no outcome yet.
    """

    async def fake_discover():
        return Discovery(agents=voice_specs, unreachable=[])

    monkeypatch.setattr(registry, "discover", fake_discover)

    oid = queries.create_orchestration(conn, "LEAD-7", flow="inbound_lead_qualification")
    for step, (dept, agent) in enumerate(
        [
            ("revops", "crm_lookup"),
            ("revops", "clay_enrich"),
            ("revops", "scoring"),
            ("revops", "routing"),
            ("sales", "voice_outreach"),
        ],
        start=1,
    ):
        run_id = queries.start_run(
            conn, oid, step_no=step, department=dept, agent=agent, input={}
        )
        queries.finish_run(conn, run_id, status="ok", output={})
        queries.record_decision(
            conn,
            oid,
            step_no=step,
            chosen_agent=agent,
            rationale="scripted",
            candidates_considered=[],
            flow="inbound_lead_qualification",
        )

    # The pre-call spend, so the resumed total is provably additive.
    queries.record_cost_event(
        conn, oid, department="orchestrator", agent="planner", amount_usd=0.04
    )
    queries.finalize_orchestration(
        conn,
        oid,
        status="running",
        outcome=None,
        final_result={
            "lead": {"phone": "+15551230000"},
            "firmographics": {"domain": "acme.com", "source": "clay"},
            "assigned_rep": "Alex Chen",
            "fit_score": 88,
            "voice_call": {"call_id": "call-abc", "status": "dialing"},
        },
    )
    return oid


def _agent_result(per_agent=None, output=None):
    """Scripted agent results. `per_agent` maps an agent name to its cost events.

    Keyed by agent because telephony is billed by voice_call_outcome alone --
    having every agent return the same event would make the total meaningless.
    """

    async def call_agent(spec, oid, payload):
        return {
            "output": output if output is not None else {spec.agent: "done"},
            "status": "ok",
            "cost_events": (per_agent or {}).get(spec.agent, []),
        }

    return call_agent


def test_resume_continues_the_same_record(conn, parked, monkeypatch):
    """Same id, continued step numbers, original flow, additive cost."""
    telephony = {
        "voice_call_outcome": [
            {
                "department": "sales",
                "agent": "voice_call_outcome",
                "amount_usd": 0.31,
                "timestamp": "2026-10-13T00:00:00+00:00",
            }
        ]
    }
    scripted = iter(
        [
            decision("voice_call_outcome", {"voice_call": {"call_id": "call-abc"}}),
            decision("crm_sync", {"firmographics": {"domain": "acme.com"}}),
        ]
    )
    monkeypatch.setattr(
        llm_module,
        "llm_call",
        lambda prompt, **kwargs: next(
            scripted, decision(None, {}, finished=True, outcome="qualified")
        ),
    )
    monkeypatch.setattr(registry, "call_agent", _agent_result(telephony))

    out = asyncio.run(
        Orchestrator(conn).resume(
            parked, seed={"call_record": {"status": "completed", "cost_usd": 0.31}}
        )
    )

    assert out["orchestration_id"] == parked          # one record, not a second
    assert out["resumed"] is True
    assert out["status"] == "completed"
    assert out["flow"] == "inbound_lead_qualification"

    stored = queries.get_orchestration(conn, parked)
    steps = [r["step_no"] for r in stored["runs"]]
    assert steps == [1, 2, 3, 4, 5, 6, 7]             # carried on, did not restart
    assert [r["agent"] for r in stored["runs"]][-2:] == [
        "voice_call_outcome",
        "crm_sync",
    ]

    # The telephony event landed under the original id, so the single row's total
    # includes it -- no parent/child rollup needed. 0.04 pre-call + 0.31 of
    # minutes + three planner calls on the resume at 0.001 each.
    assert stored["total_cost_usd"] == pytest.approx(0.04 + 0.31 + 0.003, abs=1e-6)
    assert stored["status"] == "completed"
    assert stored["finished_at"] is not None


def test_resume_restores_the_blackboard(conn, parked, monkeypatch):
    """The firmographics crm_sync requires come from the stored final_result.

    This is what makes extending crm_sync viable rather than relaxing it: the
    pre-call steps already put firmographics on the blackboard, so the post-call
    write needs no re-enrichment and no vendor spend.
    """
    seen: list[dict] = []

    def capture(prompt, **kwargs):
        seen.append({"prompt": prompt})
        return decision(None, {}, finished=True, outcome="qualified")

    monkeypatch.setattr(llm_module, "llm_call", capture)
    monkeypatch.setattr(registry, "call_agent", _agent_result())

    asyncio.run(Orchestrator(conn).resume(parked))

    prompt = seen[0]["prompt"]
    assert "acme.com" in prompt
    assert "Alex Chen" in prompt
    assert "call-abc" in prompt


def test_resume_merges_the_seed_over_the_blackboard(conn, parked, monkeypatch):
    """The webhook hands in the call record it already fetched."""
    seen: list[str] = []

    def capture(prompt, **kwargs):
        seen.append(prompt)
        return decision(None, {}, finished=True, outcome="qualified")

    monkeypatch.setattr(llm_module, "llm_call", capture)
    monkeypatch.setattr(registry, "call_agent", _agent_result())

    asyncio.run(
        Orchestrator(conn).resume(
            parked, seed={"call_record": {"status": "no_answer"}}
        )
    )
    assert "no_answer" in seen[0]


def test_resume_keeps_a_mid_run_flow_switch(conn, monkeypatch, voice_specs):
    """A run that switched flows before the call resumes under the flow it ended on.

    Reading `orchestrations.flow` would tell the planner it is working under a
    flow the run already left, so the last flow recorded on a decision wins.
    """

    async def fake_discover():
        return Discovery(agents=voice_specs, unreachable=[])

    monkeypatch.setattr(registry, "discover", fake_discover)

    oid = queries.create_orchestration(conn, "ACC-9", flow="account_expansion")
    run_id = queries.start_run(
        conn, oid, step_no=1, department="sales", agent="voice_outreach", input={}
    )
    queries.finish_run(conn, run_id, status="ok", output={})
    queries.record_decision(
        conn, oid, step_no=1, chosen_agent="voice_outreach", rationale="r",
        candidates_considered=[], flow="churn_save",   # switched before the call
    )
    queries.finalize_orchestration(
        conn, oid, status="running", outcome=None, final_result={"firmographics": {}}
    )

    monkeypatch.setattr(
        llm_module,
        "llm_call",
        lambda prompt, **kwargs: decision(None, {}, finished=True, outcome="qualified"),
    )
    monkeypatch.setattr(registry, "call_agent", _agent_result())

    out = asyncio.run(Orchestrator(conn).resume(oid))
    assert out["flow"] == "churn_save"


def test_resume_grants_step_headroom_from_where_it_stopped(conn, parked, monkeypatch):
    """A run parked at step 5 of 12 must not resume with its ceiling spent.

    Resolving max_steps from zero would halt the resume before it wrote anything
    back, which is the one thing the whole design exists to guarantee.
    """
    monkeypatch.setattr(
        llm_module,
        "llm_call",
        lambda prompt, **kwargs: decision("crm_sync", {"firmographics": {"d": 1}}),
    )
    monkeypatch.setattr(registry, "call_agent", _agent_result())

    out = asyncio.run(Orchestrator(conn).resume(parked))

    # It ran rather than halting immediately on the step ceiling.
    assert out["steps"] > 5
    assert out["status"] != "halted_steps"


def test_resume_of_an_unknown_orchestration_raises(conn):
    with pytest.raises(ValueError, match="unknown orchestration"):
        asyncio.run(Orchestrator(conn).resume("no-such-id"))
