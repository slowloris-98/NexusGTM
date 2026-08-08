"""The four agents that stand in for integrations nobody has built yet.

A stub's danger is not that it is wrong -- everyone knows it is -- but that its
output stops looking like a stub two hops downstream. So these pin the stamps:
simulated campaign figures say so, inferred firmographics say so, and the agent
named `send_outreach` cannot send anything.

They also pin the two fields that make the churn and expansion flows executable
at all. `crm_sync` raises without a firmographics domain, so a stub that dropped
it would strand a run one step from the finish.
"""

from __future__ import annotations

import inspect

import pytest

from conftest import result
from core import llm as llm_module
from departments.customer_success import server as cs
from departments.marketing.server import RunAdCampaignsAgent
from departments.sales import server as sales
from departments.sales.server import SendOutreachAgent

ACCOUNT = {"name": "Northwind Logistics", "domain": "northwind.com", "plan": "growth"}

DRAFT = {"subject": "Quick thought on your rollout", "body": "..."}

# Every agent whose output_schema is actually sent to the provider. The others in
# this repo assemble their output in Python and never pass a schema to `llm`.
SCHEMA_SENDING = [
    RunAdCampaignsAgent(),
    cs.ChurnDetectionAgent(),
    cs.EngagementTrackingAgent(),
    SendOutreachAgent(),
]


def _objects(node):
    """Every object node in a schema, at any depth."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _objects(item)


@pytest.mark.parametrize("agent", SCHEMA_SENDING, ids=lambda a: a.name)
def test_output_schemas_are_valid_under_strict_mode(agent):
    """Strict mode's object rules apply at every depth, not just the top.

    A nested object missing `additionalProperties: false` -- or declaring no
    properties at all, which strict mode cannot express -- is rejected with a 400
    naming a JSON path rather than an agent. Caught here it costs nothing; caught
    in a run it costs every call that got the run that far.
    """
    for node in _objects(agent.output_schema["schema"]):
        assert node.get("properties"), f"{agent.name}: object with no declared shape"
        assert node.get("additionalProperties") is False, f"{agent.name}: open object"
        assert set(node["required"]) == set(node["properties"]), (
            f"{agent.name}: strict mode requires every property to be required"
        )


@pytest.fixture
def parsed(monkeypatch):
    """Script what the model returns, and count the calls it took."""
    calls: list[dict] = []

    def install(payload):
        def fake_llm_call(prompt, **kwargs):
            calls.append({"prompt": prompt})
            return result(parsed=payload)

        monkeypatch.setattr(llm_module, "llm_call", fake_llm_call)
        return calls

    return install


# ------------------------------------------------------------ the envelope


@pytest.mark.parametrize(
    "agent, payload, keys",
    [
        (
            RunAdCampaignsAgent(),
            {"campaign": "q3_retargeting"},
            ("campaign_performance", "respondents", "lead"),
        ),
        (
            cs.ChurnDetectionAgent(),
            {"account": ACCOUNT},
            ("churn_risk", "risk_reasons", "renewal_window_days", "firmographics"),
        ),
        (
            cs.EngagementTrackingAgent(),
            {"account": ACCOUNT},
            ("engagement_summary", "expansion_signals", "buying_signals", "firmographics"),
        ),
        (
            SendOutreachAgent(),
            {"draft": DRAFT, "assigned_rep": "Dana Whitlock"},
            ("outreach_delivery",),
        ),
    ],
)
def test_a_valid_payload_produces_the_documented_keys(agent, payload, keys, parsed):
    parsed({})
    envelope = agent.run(payload)

    assert envelope["status"] == "ok"
    for key in keys:
        assert key in envelope["output"]


@pytest.mark.parametrize(
    "agent, payload",
    [
        (RunAdCampaignsAgent(), {}),
        (cs.ChurnDetectionAgent(), {}),
        (cs.EngagementTrackingAgent(), {}),
        (SendOutreachAgent(), {"draft": DRAFT}),  # no assigned_rep
        (SendOutreachAgent(), {"assigned_rep": "Dana"}),  # no draft
    ],
)
def test_a_missing_required_field_becomes_an_error_status(agent, payload, parsed):
    """Agent.run converts the raise; a stub must not be the thing that crashes a run."""
    parsed({})
    envelope = agent.run(payload)

    assert envelope["status"] == "error"


# --------------------------------------------------------------- provenance


def test_campaign_figures_are_stamped_simulated(parsed):
    parsed({"campaign_performance": {"impressions": 120_000, "conversions": 38}})

    output = RunAdCampaignsAgent().run({"campaign": "q3_retargeting"})["output"]

    assert output["campaign_performance"]["source"] == "simulated"
    assert output["campaign_performance"]["campaign"] == "q3_retargeting"


@pytest.mark.parametrize("agent", [cs.ChurnDetectionAgent(), cs.EngagementTrackingAgent()])
def test_customer_success_firmographics_are_stamped_inferred(agent, parsed):
    """crm_sync reads `source` to decide what may be written to HubSpot as fact."""
    parsed({"firmographics": {"industry": "Logistics", "employee_count": 420}})

    firmographics = agent.run({"account": ACCOUNT})["output"]["firmographics"]

    assert firmographics["source"] == "inferred"
    assert firmographics["industry"] == "Logistics"


@pytest.mark.parametrize("agent", [cs.ChurnDetectionAgent(), cs.EngagementTrackingAgent()])
def test_the_account_domain_survives_a_model_that_omits_it(agent, parsed):
    """Without a domain crm_sync raises, and the whole flow dies one step from done."""
    parsed({"firmographics": {"industry": "Logistics"}})  # no domain at all

    firmographics = agent.run({"account": ACCOUNT})["output"]["firmographics"]

    assert firmographics["domain"] == "northwind.com"


# ------------------------------------------------------- send_outreach only


def test_send_outreach_reports_a_dry_run(parsed):
    parsed({"channel": "email", "status": "queued"})

    delivery = SendOutreachAgent().run(
        {
            "draft": DRAFT,
            "assigned_rep": "Dana Whitlock",
            "lead": {"email": "ops@northwind.com"},
        }
    )["output"]["outreach_delivery"]

    assert delivery["dry_run"] is True
    assert delivery["owner"] == "Dana Whitlock"
    # From the payload, never from the model. An address is the one field a
    # delivery record must not contain a plausible guess of.
    assert delivery["recipient"] == "ops@northwind.com"


def test_send_outreach_ignores_a_recipient_the_model_invents(parsed):
    parsed({"channel": "email", "recipient": "ceo@northwind.com", "status": "queued"})

    delivery = SendOutreachAgent().run(
        {
            "draft": DRAFT,
            "assigned_rep": "Dana Whitlock",
            "lead": {"email": "ops@northwind.com"},
        }
    )["output"]["outreach_delivery"]

    assert delivery["recipient"] == "ops@northwind.com"


def test_send_outreach_makes_no_network_call(parsed):
    """The test that keeps 'it must not actually send' true a year from now.

    Nothing in the sales module may acquire a transport, and the agent's only
    spend may be the single planning call -- a second cost event would mean
    something started billing.
    """
    source = inspect.getsource(sales)
    for forbidden in ("httpx", "requests", "smtplib", "urllib", "Client("):
        assert forbidden not in source, f"sales server acquired {forbidden}"

    calls = parsed({"channel": "email", "status": "queued"})
    envelope = SendOutreachAgent().run(
        {"draft": DRAFT, "assigned_rep": "Dana Whitlock", "lead": {"email": "a@b.com"}}
    )

    assert len(calls) == 1
    assert len(envelope["cost_events"]) == 1
