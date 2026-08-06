"""RevOps department MCP server.

Agents: scoring, routing (judgment) and clay_enrich, clay_source, crm_lookup,
crm_sync (data and systems).

RevOps owns both halves in a real GTM org -- the data foundation and the CRM as
system of record -- but they are kept in separate modules because they answer to
different things. Scoring and routing render their policy from the playbook, so
a threshold lives in config once. The Clay and CRM agents talk to vendors, so
their concerns are auth, timeouts, and not writing a guess into the system of
record. Deliberately *not* delegated to Clay: scoring and routing. Clay can run
them, but the score bands would then live in a vendor UI as a second source of
truth, and the planner -- not Clay -- is what decides in this system.
"""

from __future__ import annotations

import json

from dotenv import load_dotenv
from fastmcp import FastMCP

from core.agent import Agent, Spender, register
from core.config import playbook
from core.llm import strict_schema

from departments.revops.clay import ClayEnrichAgent, ClaySourceAgent
from departments.revops.crm import CrmLookupAgent, CrmSyncAgent

# Its own process, so it does not inherit the API's environment: without this the
# OpenAI client finds no key and every tool call comes back status="error".
load_dotenv()

mcp = FastMCP("revops")


# Policy -- the ICP, the score bands, the signal vocabulary -- is rendered in from
# the playbook rather than written into these prompts, so it cannot drift from what
# the planner is told. Queue and segment names stay here: they are the output
# contract, declared in the schemas below, not tunable policy.
#
# Missing keys raise rather than falling back to a literal; a default would be the
# duplicated constant this indirection exists to remove, and `Agent.run` turns the
# raise into status=failed, which is the right answer for misconfigured policy.


def render_icp() -> str:
    return playbook()["icp"].strip()


def render_negative_signals() -> str:
    return ", ".join(playbook()["negative_signals"])


def render_score_bands() -> str:
    t = playbook()["thresholds"]
    high, mid, low = t["route_to_sales"], t["nurture"], t["disqualify"]
    return (
        f"{high}+ to ae_direct with a 4 hour SLA, "
        f"{mid}-{high - 1} to sdr_nurture with 24 hours, "
        f"below {low} to disqualified"
    )


# The retention axis. A separate key from `thresholds` because the numbers mean
# the opposite thing: 70 there earns a rep because the lead is good, 70 here earns
# one because the account is in trouble.
def render_churn_bands() -> str:
    b = playbook()["churn_risk_bands"]
    save, monitor = b["save_play"], b["monitor"]
    return (
        f"{save}+ is a save play worth ae_direct and a 4 hour SLA, "
        f"{monitor}-{save - 1} is worth sdr_nurture within 24 hours, "
        f"below {monitor} the account is healthy enough to monitor"
    )


class ScoringAgent(Agent):
    department = "revops"
    name = "scoring"
    description = (
        "Score an enriched lead against the ICP. Returns a 0-100 fit score, written "
        "reasons, and any negative signals. Requires firmographics -- do not call this "
        "on a raw lead."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "lead": {"type": "object", "description": "The raw lead."},
            "firmographics": {
                "type": "object",
                "description": "Enrichment output. Required.",
            },
            # clay_enrich publishes these at the top level rather than nested in
            # firmographics, and customer_success.engagement_tracking uses the
            # same key. Without this property they would reach the prompt as
            # nothing at all -- a silent drop, not an error.
            "buying_signals": {
                "type": "array",
                "items": {"type": "string"},
                "description": "clay_enrich or engagement_tracking output, when one has run.",
            },
        },
        "required": ["firmographics"],
    }

    output_schema = strict_schema(
        "fit_score",
        {
            "fit_score": {"type": "integer", "description": "0-100 ICP fit."},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "negative_signals": {"type": "array", "items": {"type": "string"}},
            "icp_match": {"type": "boolean"},
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        firmographics = payload.get("firmographics")
        if not firmographics:
            raise ValueError("payload.firmographics is required; enrich the lead first")

        result = llm(
            json.dumps(
                {
                    "lead": payload.get("lead"),
                    "firmographics": firmographics,
                    "buying_signals": payload.get("buying_signals") or [],
                },
                default=str,
            ),
            instructions=(
                "You are a RevOps scoring engine. Score ICP fit 0-100. Our ICP is "
                f"{render_icp()} Deduct heavily for negative signals: "
                f"{render_negative_signals()}. List each negative signal you find by "
                "that exact name. Give concrete reasons tied to the firmographics, not "
                "generic praise. When firmographics.source is 'inferred' the numbers "
                "were estimated by a model rather than retrieved from a data provider: "
                "stay closer to the middle of the range and say so in your reasons. "
                "Buying signals are monitored events -- a funding round, a hiring "
                "burst, a named technology -- retrieved rather than guessed, and the "
                "ICP asks for an identifiable one: an account with fresh relevant "
                "signals scores above an otherwise identical one without, and you "
                "must name the specific signals you used among your reasons. An "
                "empty list is the absence of a bonus, not a negative signal -- "
                "never list it among negative_signals."
            ),
            schema=self.output_schema,
        )
        return result.parsed


class RoutingAgent(Agent):
    department = "revops"
    name = "routing"
    description = (
        "Assign an assessed account to a segment, a rep, and a follow-up SLA. "
        "Requires either a fit_score from scoring or a churn_risk from "
        "churn_detection -- do not call this before one of them has run, or on a "
        "disqualified lead. The two run in opposite directions: a high fit_score "
        "earns a rep, a high churn_risk earns one urgently."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "fit_score": {"type": "integer", "description": "Scoring output."},
            "churn_risk": {
                "type": "integer",
                "description": "churn_detection output, for a retention run.",
            },
            "risk_reasons": {"type": "array", "items": {"type": "string"}},
            "renewal_window_days": {"type": "integer"},
            "firmographics": {"type": "object"},
            "negative_signals": {"type": "array", "items": {"type": "string"}},
        },
        # Either score will do, so neither is individually required; the check is
        # in execute(), where "at least one of" can actually be said -- the same
        # reason crm_lookup declares no required properties.
        "required": [],
    }

    output_schema = strict_schema(
        "routing_decision",
        {
            "segment": {
                "type": "string",
                "description": "enterprise | mid_market | smb",
            },
            "assigned_rep": {"type": "string"},
            "queue": {
                "type": "string",
                "description": "ae_direct | sdr_nurture | disqualified",
            },
            "sla_hours": {"type": "integer"},
            "rationale": {"type": "string"},
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        if payload.get("fit_score") is None and payload.get("churn_risk") is None:
            raise ValueError(
                "payload needs a fit_score or a churn_risk; score or assess first"
            )

        # A retention run is routed on the opposite axis, so it gets the opposite
        # band. Rendering both would tell the model to apply two contradictory
        # rules to one number.
        if payload.get("fit_score") is not None:
            banding = f"Route by fit score: {render_score_bands()}."
        else:
            banding = (
                f"This is a retention run routed on churn_risk, where "
                f"{render_churn_bands()}. High risk earns a fast SLA and the "
                "ae_direct queue, not a disqualification -- only send an account to "
                "the disqualified queue when there is nothing left to save."
            )

        result = llm(
            json.dumps(payload, default=str),
            instructions=(
                "You are a RevOps routing engine. Segment by employee count: "
                f"enterprise 1000+, mid_market 100-999, smb under 100. {banding} "
                "Assign a plausible rep name for the segment. State the rationale in "
                "one sentence."
            ),
            schema=self.output_schema,
        )
        return result.parsed


register(mcp, ScoringAgent())
register(mcp, RoutingAgent())
register(mcp, ClayEnrichAgent())
register(mcp, ClaySourceAgent())
register(mcp, CrmLookupAgent())
register(mcp, CrmSyncAgent())

if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8102)
