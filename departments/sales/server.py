"""Sales department MCP server. Agents: outreach_draft."""

from __future__ import annotations

import json

from dotenv import load_dotenv
from fastmcp import FastMCP

from core.agent import Agent, Spender, register
from core.llm import strict_schema

# Its own process, so it does not inherit the API's environment: without this the
# OpenAI client finds no key and every tool call comes back status="error".
load_dotenv()

mcp = FastMCP("sales")


class OutreachDraftAgent(Agent):
    department = "sales"
    name = "outreach_draft"
    description = (
        "Draft a personalized first-touch email for an assigned rep. Requires an "
        "assigned rep and firmographics. Never call this for a disqualified lead."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "lead": {"type": "object"},
            "firmographics": {"type": "object", "description": "Required."},
            "assigned_rep": {"type": "string", "description": "Routing output. Required."},
            "segment": {"type": "string"},
            "reasons": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["firmographics", "assigned_rep"],
    }

    output_schema = strict_schema(
        "outreach_draft",
        {
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "personalization_notes": {"type": "array", "items": {"type": "string"}},
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        if not payload.get("assigned_rep"):
            raise ValueError("payload.assigned_rep is required; route the lead first")
        if not payload.get("firmographics"):
            raise ValueError("payload.firmographics is required")

        result = llm(
            json.dumps(payload, default=str),
            instructions=(
                "You are an SDR writing a first-touch email. Under 120 words, from the "
                "assigned rep, to the lead. Open with something specific to their company "
                "drawn from the firmographics or scoring reasons -- no generic flattery, "
                "no 'I hope this finds you well'. One clear ask. In "
                "personalization_notes, list which specific data points you used."
            ),
            schema=self.output_schema,
        )
        return {"draft": result.parsed}


register(mcp, OutreachDraftAgent())

if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8103)
