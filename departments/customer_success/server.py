"""Customer Success MCP server. Agents: churn_detection, engagement_tracking.

Both agents start a run rather than continue one: retention and expansion begin
from an account we already serve, not from a lead somebody captured. Both are
stubs -- no product-telemetry source is connected -- and both are careful about
saying so, because everything downstream of them treats their output as fact.
"""

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

mcp = FastMCP("customer_success")

# Declared rather than left as a bare {"type": "object"}: these schemas are sent to
# the provider in strict mode, which cannot express an object of unknown shape.
# The shape mirrors marketing.enrichment's output, since crm_sync reads both.
FIRMOGRAPHICS = {
    "type": "object",
    "properties": {
        "company_name": {"type": "string"},
        "domain": {"type": "string"},
        "industry": {"type": "string"},
        "employee_count": {"type": "integer"},
        "revenue_band": {"type": "string"},
        "hq_region": {"type": "string"},
    },
}


# Renewal-risk policy comes from the playbook for the same reason the revops score
# bands do -- so a band cannot drift from what the planner is told. A missing key
# raises rather than falling back to a literal; `Agent.run` turns that into
# status="error", which is the right answer for misconfigured policy.
def render_churn_bands() -> str:
    bands = playbook()["churn_risk_bands"]
    save, monitor, healthy = bands["save_play"], bands["monitor"], bands["healthy"]
    return (
        f"{save}+ is a save play worth a rep's time today, "
        f"{monitor}-{save - 1} is worth monitoring, "
        f"below {healthy} is a healthy account"
    )


def _stamp_inferred(account: dict, firmographics: dict | None) -> dict:
    """Everything these agents know about a company is a reconstruction.

    `source` is stamped here rather than asked of the model because crm_sync reads
    it to decide whether these numbers may be written to HubSpot as fact. `domain`
    is carried through from the input for a harder reason: crm_sync raises without
    one, so a model that omitted it would strand the whole flow one step from the
    finish.
    """
    return {
        **(firmographics or {}),
        "company_name": (firmographics or {}).get("company_name")
        or account.get("name")
        or "",
        "domain": (account.get("domain") or (firmographics or {}).get("domain") or ""),
        "source": "inferred",
    }


class ChurnDetectionAgent(Agent):
    department = "customer_success"
    name = "churn_detection"
    description = (
        "Assess renewal risk for an existing customer account from what we know of "
        "usage, support history, and stakeholder engagement. Returns a 0-100 "
        "churn_risk with written reasons, the renewal window, and the account's "
        "firmographics so downstream agents have something to work with. STUB -- no "
        "product-telemetry source is connected; the signals are a model's "
        "reconstruction and the firmographics are stamped source='inferred'. Start "
        "a retention run here."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "account": {
                "type": "object",
                "description": (
                    "What we know: name, domain, plan, renewal date, owner. Required."
                ),
            },
            "crm_reference_id": {
                "type": "string",
                "description": "The account reference shown in your prompt.",
            },
            "window_days": {"type": "integer", "description": "Lookback. Default 90."},
        },
        "required": ["account"],
    }

    output_schema = strict_schema(
        "churn_assessment",
        {
            "churn_risk": {"type": "integer", "description": "0-100. Higher is worse."},
            "risk_reasons": {"type": "array", "items": {"type": "string"}},
            "renewal_window_days": {"type": "integer"},
            "firmographics": FIRMOGRAPHICS,
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        account = payload.get("account")
        if not account:
            raise ValueError("payload.account is required; churn runs start from an account")

        result = llm(
            json.dumps(
                {
                    "account": account,
                    "crm_reference_id": payload.get("crm_reference_id"),
                    "window_days": payload.get("window_days") or 90,
                },
                default=str,
            ),
            instructions=(
                "You are a customer success analyst assessing renewal risk. No "
                "telemetry source is connected, so your signals are a plausible "
                "reconstruction, not measurements -- keep the risk near the middle "
                "of the range unless the account itself gives you a reason not to. "
                f"Score churn_risk 0-100, where {render_churn_bands()}. Give "
                "concrete risk_reasons tied to what the account record actually "
                "says -- plan, seats, renewal date, owner -- not generic churn "
                "theory. Estimate renewal_window_days from the renewal date when "
                "one is given. Also return firmographics for the company: industry, "
                "employee_count, revenue_band, hq_region."
            ),
            schema=self.output_schema,
        )

        parsed = result.parsed or {}
        return {
            "churn_risk": parsed.get("churn_risk"),
            "risk_reasons": parsed.get("risk_reasons") or [],
            "renewal_window_days": parsed.get("renewal_window_days"),
            "firmographics": _stamp_inferred(account, parsed.get("firmographics")),
        }


class EngagementTrackingAgent(Agent):
    department = "customer_success"
    name = "engagement_tracking"
    description = (
        "Summarise how an existing account is engaging: product usage trend, "
        "stakeholder activity, and any expansion signal worth acting on. Returns "
        "engagement_summary, expansion_signals, buying_signals, and the account's "
        "firmographics. STUB -- no telemetry source is connected; figures are "
        "inferred and stamped source='inferred'. Start an account-based marketing "
        "or expansion run here."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "account": {
                "type": "object",
                "description": "Name, domain, plan, seats, owner. Required.",
            },
            "crm_reference_id": {"type": "string"},
            "window_days": {"type": "integer", "description": "Lookback. Default 30."},
        },
        "required": ["account"],
    }

    # buying_signals is published at the TOP level, not nested, because that is
    # exactly where scoring declares it (revops ScoringAgent.input_schema) for
    # clay_enrich output. Matching the existing property name is what lets an
    # expansion run feed scoring with no new plumbing on either side.
    output_schema = strict_schema(
        "engagement_summary",
        {
            "engagement_summary": {
                "type": "object",
                "properties": {
                    "usage_trend": {
                        "type": "string",
                        "description": "growing | flat | declining, with a clause of why.",
                    },
                    "active_seats": {"type": "integer"},
                    "licensed_seats": {"type": "integer"},
                    "active_stakeholders": {"type": "array", "items": {"type": "string"}},
                    "support_volume": {"type": "string"},
                },
            },
            "expansion_signals": {"type": "array", "items": {"type": "string"}},
            "buying_signals": {"type": "array", "items": {"type": "string"}},
            "firmographics": FIRMOGRAPHICS,
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        account = payload.get("account")
        if not account:
            raise ValueError(
                "payload.account is required; engagement runs start from an account"
            )

        result = llm(
            json.dumps(
                {
                    "account": account,
                    "crm_reference_id": payload.get("crm_reference_id"),
                    "window_days": payload.get("window_days") or 30,
                },
                default=str,
            ),
            instructions=(
                "You are a customer success analyst reading how an existing account "
                "engages with us. No telemetry source is connected, so everything "
                "you return is an estimate -- do not invent precision you do not "
                "have. In engagement_summary give the usage trend, active seats "
                "against licensed seats, the stakeholders active in the window, and "
                "recent support volume. In expansion_signals list only what would "
                "genuinely justify an upsell conversation -- seat saturation, a new "
                "team adopting, a stakeholder in an adjacent function. In "
                "buying_signals list outside events worth opening an email with. "
                "Also return firmographics: industry, employee_count, revenue_band, "
                "hq_region."
            ),
            schema=self.output_schema,
        )

        parsed = result.parsed or {}
        return {
            "engagement_summary": parsed.get("engagement_summary") or {},
            "expansion_signals": parsed.get("expansion_signals") or [],
            "buying_signals": parsed.get("buying_signals") or [],
            # Stamped in Python for the same reason firmographics are: the
            # expansion flow may also run clay_enrich, and without a stamp here
            # the board can end up carrying these guessed signals under the
            # `signal_source: "clay"` a later step left behind.
            "signal_source": "inferred",
            "firmographics": _stamp_inferred(account, parsed.get("firmographics")),
        }


register(mcp, ChurnDetectionAgent())
register(mcp, EngagementTrackingAgent())

if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8104)
