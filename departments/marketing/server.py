"""Marketing department MCP server. Agents: enrichment, run_ad_campaigns."""

from __future__ import annotations

import json

from dotenv import load_dotenv
from fastmcp import FastMCP

from core.agent import Agent, Spender, register
from core.llm import strict_schema

# Its own process, so it does not inherit the API's environment: without this the
# OpenAI client finds no key and every tool call comes back status="error".
load_dotenv()

mcp = FastMCP("marketing")


class EnrichmentAgent(Agent):
    department = "marketing"
    name = "enrichment"
    description = (
        "Estimate firmographics for a lead by inference: industry, employee count, "
        "revenue band, HQ region. This is the LAST RESORT of the enrichment "
        "waterfall -- the values are a model's best guess, not retrieved data. "
        "Try 'clay_enrich' first and only come here when it reports "
        "clay_enrichment.matched=false. Never run both when Clay matched. Scoring "
        "still needs firmographics from somewhere, so an estimate beats nothing."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "lead": {
                "type": "object",
                "description": "Raw lead as captured: name, email, company, source.",
            }
        },
        "required": ["lead"],
    }

    # buying_signals is gone: a model cannot know that a company raised a round or
    # that a champion changed jobs. Those are monitored events, and `clay_enrich`
    # owns them now -- it returns them alongside firmographics in one call.
    # Asking for them here only produced plausible fiction, which is also why a
    # fallback enrichment yields no signals at all: that is correct, not a gap.
    output_schema = strict_schema(
        "firmographics",
        {
            "company_name": {"type": "string"},
            "domain": {"type": "string"},
            "industry": {"type": "string"},
            "employee_count": {"type": "integer"},
            "revenue_band": {"type": "string"},
            "hq_region": {"type": "string"},
            "free_email_domain": {"type": "boolean"},
            "confidence": {"type": "number"},
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        lead = payload.get("lead")
        if not lead:
            raise ValueError("payload.lead is required")

        result = llm(
            json.dumps(lead, default=str),
            instructions=(
                "You are a B2B data enrichment service of last resort: a data "
                "provider has already failed to find this company, so everything "
                "you return is an estimate. From the raw lead, infer firmographics "
                "for the company. Set free_email_domain to true when the contact "
                "email uses a consumer provider (gmail, yahoo, outlook, hotmail, "
                "proton). Use confidence 0-1 to express how sure you are; do not "
                "invent precision you do not have."
            ),
            schema=self.output_schema,
        )

        # Stamped here rather than asked of the model, which could only ever get
        # it wrong. Downstream this is load-bearing: `crm_sync` reads it to decide
        # whether these numbers may be written to HubSpot as fact.
        return {"firmographics": {**(result.parsed or {}), "source": "inferred"}}


class RunAdCampaignsAgent(Agent):
    department = "marketing"
    name = "run_ad_campaigns"
    description = (
        "Pull the current state of live ad campaigns and surface their "
        "highest-intent respondents. Starts a paid-media run that has no lead yet: "
        "returns campaign_performance, the respondent list, and the strongest "
        "respondent promoted to 'lead' so the rest of the flow proceeds normally. "
        "STUB -- no ad platform is connected. Every figure is a model's "
        "illustration, stamped source='simulated'; never report these as measured "
        "spend."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "campaign": {
                "type": "string",
                "description": "Campaign or channel name. 'all_active' for everything live.",
            },
            "window_days": {"type": "integer", "description": "Reporting window. Default 14."},
            "objective": {"type": "string", "description": "What the campaign is for."},
        },
        "required": ["campaign"],
    }

    # Every nested object declares its shape: this schema is sent to the provider
    # in strict mode, which cannot express an object of unknown shape.
    output_schema = strict_schema(
        "ad_campaign_pull",
        {
            "campaign_performance": {
                "type": "object",
                "properties": {
                    "impressions": {"type": "integer"},
                    "clicks": {"type": "integer"},
                    "conversions": {"type": "integer"},
                    "cost_usd": {"type": "number"},
                    "cost_per_conversion": {"type": "number"},
                },
            },
            "respondents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                        "company": {"type": "string"},
                        "domain": {"type": "string"},
                        "responded_to": {"type": "string"},
                    },
                },
            },
            "lead": {
                "type": "object",
                "description": "The highest-intent respondent, promoted.",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                    "company": {"type": "string"},
                    "domain": {"type": "string"},
                    "source": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        campaign = payload.get("campaign")
        if not campaign:
            raise ValueError("payload.campaign is required")

        result = llm(
            json.dumps(
                {
                    "campaign": campaign,
                    "window_days": payload.get("window_days") or 14,
                    "objective": payload.get("objective"),
                },
                default=str,
            ),
            instructions=(
                "You are a paid-media analyst producing an illustrative campaign "
                "readout. No ad platform is connected, so nothing you return is "
                "measured. For the named campaign and window give a performance "
                "summary -- impressions, clicks, conversions, cost_usd, "
                "cost_per_conversion -- and three to five respondent records, each "
                "with name, email, company, domain, and what they responded to. "
                "Promote the highest-intent respondent to `lead` with name, email, "
                "company, domain, source='ad_campaign', and a note saying which "
                "campaign and which action. Use business email domains, not "
                "consumer ones, unless the respondent is plainly a consumer."
            ),
            schema=self.output_schema,
        )

        parsed = result.parsed or {}
        return {
            # Stamped here rather than asked of the model, for the same reason
            # enrichment stamps its own source: a readout that cannot say where
            # its numbers came from will be read as measured the first time
            # someone screenshots it.
            "campaign_performance": {
                **(parsed.get("campaign_performance") or {}),
                "campaign": campaign,
                "source": "simulated",
            },
            "respondents": parsed.get("respondents") or [],
            "lead": parsed.get("lead") or {},
        }


register(mcp, EnrichmentAgent())
register(mcp, RunAdCampaignsAgent())

if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8101)
