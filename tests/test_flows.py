"""Flows are prompt text and a label, never branch logic.

These pin the two halves of that claim: the planner is shown an index of every
flow but the detail of only one, and a flow the planner names is recorded rather
than enforced -- a bad name costs a label, not a run.
"""

from __future__ import annotations

import json

import pytest
import yaml

from conftest import result
from core import config, planner
from core import llm as llm_module
from core import registry
from core.orchestrator import Orchestrator
from core.registry import Discovery

TWO_FLOWS = {
    "thresholds": {"route_to_sales": 90, "nurture": 65, "disqualify": 30},
    "negative_signals": ["uses_fax"],
    "guardrails": {"max_steps": 12, "budget_usd": 0.50},
    "common": {
        "patterns": [
            {
                "name": "always_true",
                "when": "any run at all",
                "guidance": "A rule that holds whichever flow is in force.",
            }
        ]
    },
    "flows": [
        {
            "id": "alpha",
            "label": "Alpha",
            "when": "the alpha condition holds",
            "default": True,
            "goal": "Reach the alpha outcome.",
            "success_criteria": ["alpha is done"],
            "patterns": [
                {
                    "name": "alpha_pattern",
                    "when": "alpha has just started",
                    "guidance": "Do the alpha thing first.",
                }
            ],
            "trigger": {
                "department": "marketing",
                "label": "Start alpha",
                "description": "Kicks off alpha.",
                "entry_agent": "enrichment",
                "input": {"kind": "none", "seed": {"campaign": "seeded"}},
                "reference_prefix": "ALPHA",
            },
        },
        {
            "id": "beta",
            "label": "Beta",
            "when": "the beta condition holds",
            "goal": "Reach the beta outcome.",
            "success_criteria": ["beta is done"],
            "patterns": [
                {
                    "name": "beta_pattern",
                    "when": "beta has just started",
                    "guidance": "Do the beta thing first.",
                }
            ],
            "guardrails": {"max_steps": 2},
        },
    ],
}


@pytest.fixture
def flows(tmp_path, monkeypatch):
    """Write a flow-bearing playbook to a temp config dir and point the loader at it."""

    def write(pb=None):
        (tmp_path / "playbook.yaml").write_text(
            yaml.safe_dump(pb if pb is not None else TWO_FLOWS), encoding="utf-8"
        )
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        config.load.cache_clear()

    yield write
    config.load.cache_clear()  # do not leak the temp playbook into later tests


def decision(agent, payload, *, flow=None, finished=False, outcome=None, cost=0.001):
    parsed = {
        "chosen_agent": agent,
        "rationale": f"picked {agent}",
        "candidates_considered": ["enrichment", "scoring"],
        "input_payload": json.dumps(payload),
        "finished": finished,
        "outcome": outcome,
    }
    if flow is not None:
        parsed["flow"] = flow
    return result(cost=cost, parsed=parsed)


@pytest.fixture
def patched(monkeypatch, specs):
    """Scripted planner decisions and agent results, capturing every prompt seen."""
    prompts: list[str] = []

    async def fake_discover():
        return Discovery(agents=specs, unreachable=[])

    async def ok_agent(spec, oid, payload):
        return {
            "output": {"firmographics": {"industry": "software"}},
            "status": "ok",
            "cost_events": [],
        }

    monkeypatch.setattr(registry, "discover", fake_discover)
    monkeypatch.setattr(registry, "call_agent", ok_agent)

    def install(decisions):
        it = iter(decisions)

        def fake_llm_call(prompt, **kwargs):
            prompts.append(prompt)
            try:
                return next(it)
            except StopIteration:
                return decision(None, {}, finished=True, outcome="qualified")

        monkeypatch.setattr(llm_module, "llm_call", fake_llm_call)
        return prompts

    return install


# ------------------------------------------------------------------- rendering


def test_the_flow_index_lists_every_flow(flows):
    flows()
    rendered = planner.render_playbook("beta")

    assert "alpha" in rendered and "the alpha condition holds" in rendered
    assert "beta" in rendered and "the beta condition holds" in rendered


def test_only_the_current_flow_renders_in_full(flows):
    flows()
    rendered = planner.render_playbook("beta")

    assert "Reach the beta outcome." in rendered
    assert "Do the beta thing first." in rendered
    # Alpha's one line is in the index; its goal and patterns are not. This is the
    # whole reason the index and the detail are rendered separately.
    assert "Reach the alpha outcome." not in rendered
    assert "Do the alpha thing first." not in rendered


def test_common_patterns_render_under_every_flow(flows):
    flows()

    for flow_id in ("alpha", "beta", None):
        assert "A rule that holds whichever flow is in force." in planner.render_playbook(
            flow_id
        )


def test_a_playbook_with_no_flows_still_renders(flows):
    """The playbook this file shipped with before flows existed must keep working.

    Pinned because test_policy_config's fixture writes exactly such a playbook: a
    renderer that raised on a missing `flows` key would turn that fixture into a
    landmine for every test using it.
    """
    flows({"thresholds": {"route_to_sales": 90}, "negative_signals": ["uses_fax"]})
    rendered = planner.render_playbook()

    assert planner.default_flow_id() is None
    assert "route_to_sales: 90" in rendered
    assert "uses_fax" in rendered


def test_the_shipped_playbook_declares_one_default_and_unique_ids():
    """A YAML typo should fail here, not three LLM calls into a real run."""
    config.load.cache_clear()
    declared = planner.flows()

    assert declared, "the shipped playbook declares no flows"
    ids = [f["id"] for f in declared]
    assert len(ids) == len(set(ids)), f"duplicate flow ids: {ids}"
    assert len([f for f in declared if f.get("default")]) == 1
    for flow in declared:
        assert flow.get("when"), f"{flow['id']} has no `when` line for the index"
        assert flow.get("goal"), f"{flow['id']} has no goal"


# ----------------------------------------------------------------- flow in run


async def test_the_planner_can_switch_flow_mid_run(conn, flows, patched):
    flows()
    prompts = patched(
        [
            decision("enrichment", {"lead": {"a": 1}}, flow="alpha"),
            decision("scoring", {"firmographics": {"x": 1}}, flow="beta"),
        ]
    )

    await Orchestrator(conn).run("ACME-1", {"company": "Acme"}, flow="alpha")

    # The third prompt is planned after the switch, so it carries beta's detail.
    assert "Reach the beta outcome." in prompts[2]
    assert "Reach the alpha outcome." not in prompts[2]

    recorded = [
        row["flow"]
        for row in conn.execute("SELECT flow FROM decisions ORDER BY step_no")
    ]
    assert recorded[:2] == ["alpha", "beta"]


async def test_an_unknown_flow_name_is_ignored_not_fatal(conn, flows, patched):
    """A mislabelled decision is not worth a second planner call to correct."""
    flows()
    patched([decision("enrichment", {"lead": {"a": 1}}, flow="nope")])

    outcome = await Orchestrator(conn).run("ACME-1", {"company": "Acme"}, flow="alpha")

    assert outcome["status"] == "completed"
    # The agent it chose still ran -- only the label was rejected.
    runs = conn.execute("SELECT agent FROM runs").fetchall()
    assert [r["agent"] for r in runs] == ["enrichment"]

    first = conn.execute(
        "SELECT * FROM decisions WHERE step_no = 1"
    ).fetchall()
    assert len(first) == 1, "an unknown flow must not cost an extra planning step"
    assert first[0]["flow"] == "alpha"
    assert "ignored unknown flow" in first[0]["rationale"]


async def test_the_run_records_the_flow_it_started_under(conn, flows, patched):
    flows()
    patched([decision(None, {}, finished=True, outcome="qualified")])

    outcome = await Orchestrator(conn).run("ACME-1", {"company": "Acme"}, flow="beta")

    stored = conn.execute(
        "SELECT flow FROM orchestrations WHERE id = ?", (outcome["orchestration_id"],)
    ).fetchone()
    assert stored["flow"] == "beta"


# ------------------------------------------------------------------ guardrails


async def test_flow_guardrails_override_the_global_ones(conn, flows, patched):
    """Beta declares max_steps 2 against a global 12."""
    flows()
    patched(
        [
            decision("enrichment", {"lead": {"a": 1}}),
            decision("scoring", {"firmographics": {"x": 1}}),
            decision("enrichment", {"lead": {"b": 2}}),
        ]
    )

    outcome = await Orchestrator(conn).run("ACME-1", {"company": "Acme"}, flow="beta")

    assert outcome["status"] == "halted_steps"


async def test_an_instance_guardrail_still_wins_for_a_flowless_run(conn, flows, patched):
    """scripts/seed.py and the guardrail tests stage runs this way.

    They assign the attribute and declare no flow, so nothing in the playbook may
    quietly outrank them.
    """
    flows()
    patched([decision("enrichment", {"lead": {"a": 1}})] * 4)

    orchestrator = Orchestrator(conn)
    orchestrator.max_steps = 1

    outcome = await orchestrator.run("ACME-1", {"company": "Acme"})

    assert outcome["status"] == "halted_steps"


# ----------------------------------------------------------------- run entry


async def test_an_unknown_flow_at_launch_raises(conn, flows, patched):
    """What makes the API's 422 real."""
    flows()
    patched([])

    with pytest.raises(ValueError, match="unknown flow"):
        await Orchestrator(conn).run("ACME-1", {"company": "Acme"}, flow="nope")


async def test_a_flow_with_no_lead_and_no_brief_starts_from_its_seed(conn, flows, patched):
    """Alpha's trigger declares kind 'none' and a seed; that has to be enough."""
    flows()
    prompts = patched([decision(None, {}, finished=True, outcome="qualified")])

    await Orchestrator(conn).run(flow="alpha")

    assert "seeded" in prompts[0]


async def test_a_run_with_nothing_at_all_still_raises(conn, flows, patched):
    """Beta declares no trigger and no seed, so there is nothing to reason about."""
    flows()
    patched([])

    with pytest.raises(ValueError, match="requires a lead"):
        await Orchestrator(conn).run(flow="beta")


async def test_a_run_with_no_account_gets_a_readable_placeholder(conn, flows, patched):
    """crm_reference_id is NOT NULL, and a campaign run has no account yet."""
    flows()
    patched([decision(None, {}, finished=True, outcome="qualified")])

    outcome = await Orchestrator(conn).run(flow="alpha")

    stored = conn.execute(
        "SELECT crm_reference_id FROM orchestrations WHERE id = ?",
        (outcome["orchestration_id"],),
    ).fetchone()
    assert stored["crm_reference_id"].startswith("ALPHA-")


async def test_the_caller_seed_beats_the_flows_own(conn, flows, patched):
    flows()
    prompts = patched([decision(None, {}, finished=True, outcome="qualified")])

    await Orchestrator(conn).run(flow="alpha", seed={"campaign": "from_the_caller"})

    assert "from_the_caller" in prompts[0]
    assert "seeded" not in prompts[0]
