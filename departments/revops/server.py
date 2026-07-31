"""RevOps department MCP server. Agents: scoring, routing."""

from __future__ import annotations

import json

from dotenv import load_dotenv
from fastmcp import FastMCP

from core.agent import Agent, Spender, register
from core.config import playbook
from core.llm import strict_schema

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
                {"lead": payload.get("lead"), "firmographics": firmographics}, default=str
            ),
            instructions=(
                "You are a RevOps scoring engine. Score ICP fit 0-100. Our ICP is "
                f"{render_icp()} Deduct heavily for negative signals: "
                f"{render_negative_signals()}. List each negative signal you find by "
                "that exact name. Give concrete reasons tied to the firmographics, not "
                "generic praise."
            ),
            schema=self.output_schema,
        )
        return result.parsed


class RoutingAgent(Agent):
    department = "revops"
    name = "routing"
    description = (
        "Assign a scored lead to a segment, a rep, and a follow-up SLA. Requires a "
        "fit_score -- do not call this before scoring, or on a disqualified lead."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "fit_score": {"type": "integer", "description": "Scoring output. Required."},
            "firmographics": {"type": "object"},
            "negative_signals": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["fit_score"],
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
        if payload.get("fit_score") is None:
            raise ValueError("payload.fit_score is required; score the lead first")

        result = llm(
            json.dumps(payload, default=str),
            instructions=(
                "You are a RevOps routing engine. Segment by employee count: "
                "enterprise 1000+, mid_market 100-999, smb under 100. Route by score: "
                f"{render_score_bands()}. Assign a plausible rep name for the segment. "
                "State the rationale in one sentence."
            ),
            schema=self.output_schema,
        )
        return result.parsed


register(mcp, ScoringAgent())
register(mcp, RoutingAgent())

if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8102)
