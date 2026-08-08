"""Sales department MCP server. Agents: outreach_draft, send_outreach."""

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


class SendOutreachAgent(Agent):
    department = "sales"
    name = "send_outreach"
    description = (
        "Queue an approved outreach draft into the assigned rep's sequence. This "
        "does NOT send email: no delivery provider is connected and this agent "
        "makes no network call at all. It records the delivery record that WOULD "
        "have been created -- channel, recipient, subject, when it would go out, "
        "and which rep owns it -- and returns dry_run=true, the same way crm_sync "
        "reports a skipped write. Requires a draft and an assigned rep; never call "
        "it before outreach_draft has run."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "draft": {"type": "object", "description": "outreach_draft output. Required."},
            "assigned_rep": {"type": "string", "description": "Routing output. Required."},
            "lead": {"type": "object", "description": "Carries the recipient address."},
            "firmographics": {"type": "object"},
            "queue": {"type": "string", "description": "ae_direct | sdr_nurture."},
            "sla_hours": {"type": "integer", "description": "Routing output."},
        },
        "required": ["draft", "assigned_rep"],
    }

    output_schema = strict_schema(
        "outreach_delivery",
        {
            "channel": {"type": "string", "description": "email | linkedin | call_task"},
            "subject": {"type": "string"},
            "scheduled_at": {
                "type": "string",
                "description": "ISO 8601, inside the SLA window.",
            },
            "status": {"type": "string", "description": "queued | held"},
            "hold_reason": {"type": "string", "description": "Empty when queued."},
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        draft = payload.get("draft")
        if not draft:
            raise ValueError("payload.draft is required; draft the outreach first")
        rep = payload.get("assigned_rep")
        if not rep:
            raise ValueError("payload.assigned_rep is required; route the lead first")

        # Resolved here rather than asked of the model. An address is the one field
        # a delivery record must never contain a plausible guess of, and the model
        # would happily produce one.
        lead = payload.get("lead") or {}
        recipient = str(lead.get("email") or "").strip()
        if not recipient:
            domain = str((payload.get("firmographics") or {}).get("domain") or "").strip()
            recipient = f"unknown@{domain}" if domain else ""

        result = llm(
            json.dumps(
                {
                    "draft": draft,
                    "assigned_rep": rep,
                    "queue": payload.get("queue"),
                    "sla_hours": payload.get("sla_hours"),
                    "recipient": recipient,
                },
                default=str,
            ),
            instructions=(
                "You are a sales sequencing engine deciding when an approved draft "
                "goes out and whether it should go out at all. Pick the channel and "
                "a scheduled_at inside the SLA window, in business hours. Set status "
                "to 'held' with a one-line hold_reason when the recipient address is "
                "missing or uses a consumer email provider, when the draft names a "
                "competitor, or when the SLA window has already passed. Otherwise "
                "status is 'queued' and hold_reason is empty."
            ),
            schema=self.output_schema,
        )

        # dry_run, recipient and owner are stamped here, unconditionally. Unlike
        # crm_sync's CRM_WRITE_ENABLED there is no flag to flip: nothing in this
        # module can send, so reporting anything but a dry run would be a lie.
        return {
            "outreach_delivery": {
                **(result.parsed or {}),
                "recipient": recipient,
                "owner": rep,
                "dry_run": True,
            }
        }


register(mcp, OutreachDraftAgent())
register(mcp, SendOutreachAgent())

if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8103)
