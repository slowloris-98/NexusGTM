"""Marketing department MCP server. Agents: enrichment."""

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
        "Enrich a raw inbound lead into firmographics: industry, employee count, "
        "revenue band, HQ region, and buying signals. Run this before scoring -- "
        "scoring without firmographics produces noise."
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

    output_schema = strict_schema(
        "firmographics",
        {
            "company_name": {"type": "string"},
            "domain": {"type": "string"},
            "industry": {"type": "string"},
            "employee_count": {"type": "integer"},
            "revenue_band": {"type": "string"},
            "hq_region": {"type": "string"},
            "buying_signals": {"type": "array", "items": {"type": "string"}},
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
                "You are a B2B data enrichment service. From the raw lead, infer "
                "firmographics for the company. Set free_email_domain to true when the "
                "contact email uses a consumer provider (gmail, yahoo, outlook, hotmail, "
                "proton). Use confidence 0-1 to express how sure you are; do not invent "
                "precision you do not have."
            ),
            schema=self.output_schema,
        )
        return {"firmographics": result.parsed}


register(mcp, EnrichmentAgent())

if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8101)
