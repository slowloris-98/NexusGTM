"""HubSpot agents. The CRM is the system of record, and RevOps owns it.

`crm_lookup` gives the planner account context before it starts spending;
`crm_sync` writes the run's verdict back at the end.

The rule that shapes `crm_sync`: **inferred data is never written as fact.**
`marketing.enrichment` guesses headcount and revenue band from a model. Writing
those into a real CRM under the same property names as retrieved values would
poison the system of record for every team that reads it afterwards, silently and
permanently. So firmographic properties are written under their canonical names
only when `firmographics.source == "clay"`; a guess goes to `*_inferred`
properties instead, where it stays visibly a guess.

Which property is canonical for each field is `FACT_PROPERTIES` below, and it is
not always HubSpot's same-named built-in -- see the note there. Nothing in this
module creates properties; `scripts/hubspot_setup.py` does that once per portal,
and reads its list from here so the two cannot drift.
"""

from __future__ import annotations

import os

from core.agent import Agent, Spender
from core.llm import strict_schema

from departments.revops.clients import HubspotClient, VendorError

CONTACT_PROPERTIES = [
    "email",
    "firstname",
    "lastname",
    "company",
    "lifecyclestage",
    "hubspot_owner_id",
    "notes_last_updated",
]

COMPANY_PROPERTIES = [
    "name",
    "domain",
    "industry",
    "numberofemployees",
    "annualrevenue",
    "country",
    "lifecyclestage",
    "hubspot_owner_id",
]

# Which HubSpot property each firmographic field is written to. Two of them are
# deliberately not HubSpot's same-named built-in:
#
#   industry      theirs is a validated enumeration -- since July 2023 a value
#                 outside the portal's option list is a VALIDATION_ERROR, and
#                 enrichment returns free text like "Logistics Software".
#   revenue_band  `annualrevenue` is a number. A band ("$25M-$50M") is not one,
#                 and is not a figure we could honestly convert into one.
#
# Both would fail the write outright, so they go to properties this system owns
# and controls the type of. `scripts/hubspot_setup.py` creates every non-built-in
# name here, and reads this map to know what to create.
FACT_PROPERTIES = {
    "industry": "nexusgtm_industry",
    "employee_count": "numberofemployees",
    "revenue_band": "nexusgtm_revenue_band",
    "hq_region": "country",
}

# The run's own verdict: this system's output, not a vendor's data.
VERDICT_PROPERTIES = [
    "nexusgtm_fit_score",
    "nexusgtm_segment",
    "nexusgtm_assigned_rep",
    "nexusgtm_queue",
    "nexusgtm_firmographics_source",
    "nexusgtm_score_reasons",
]


def writes_enabled() -> bool:
    """Off by default. A seed pass is 12 runs, and nobody wants 12 junk records
    in a real portal the first time they try this."""
    return os.getenv("CRM_WRITE_ENABLED", "").strip().lower() in ("1", "true", "yes")


class CrmLookupAgent(Agent):
    department = "revops"
    name = "crm_lookup"
    description = (
        "Read existing HubSpot context for an account: whether we already know "
        "them, their lifecycle stage, owner, and open deals. Worth running early "
        "on an inbound lead -- an existing owner or open deal changes what the "
        "right next step is. Give it an email, a domain, or the account "
        "reference. Returns crm_account.exists=false when we have no record, "
        "which is a normal answer, not a failure."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "crm_reference_id": {
                "type": "string",
                "description": "The ACCOUNT reference shown in your prompt.",
            },
            "email": {"type": "string", "description": "Contact email from the lead."},
            "domain": {"type": "string", "description": "Company domain."},
        },
        # Any one of the three will do, so none is individually required; the
        # check is in execute(), where "at least one of" can actually be said.
        "required": [],
    }

    output_schema = strict_schema(
        "crm_account",
        {
            "exists": {"type": "boolean"},
            "record_id": {"type": "string"},
            "object_type": {"type": "string"},
            "lifecycle_stage": {"type": "string"},
            "owner": {"type": "string"},
            "matched_on": {"type": "string"},
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        email = (payload.get("email") or "").strip()
        domain = (payload.get("domain") or "").strip().lower()
        reference = (payload.get("crm_reference_id") or "").strip()

        if not (email or domain or reference):
            raise ValueError(
                "crm_lookup needs one of email, domain, or crm_reference_id"
            )

        client = HubspotClient()

        # Email first, then domain. The reference id is last on purpose: internal
        # references like ACME-1001 usually exist only in this system, so keying
        # on it first would make this agent miss on accounts HubSpot does have.
        attempts = []
        if email:
            attempts.append(("contacts", "email", email, CONTACT_PROPERTIES))
        if domain:
            attempts.append(("companies", "domain", domain, COMPANY_PROPERTIES))
        if reference:
            attempts.append(("companies", "name", reference, COMPANY_PROPERTIES))

        for object_type, prop, value, properties in attempts:
            record = client.search(object_type, prop, value, properties)
            if record:
                props = record.get("properties") or {}
                return {
                    "crm_account": {
                        "exists": True,
                        "record_id": str(record.get("id", "")),
                        "object_type": object_type,
                        "lifecycle_stage": props.get("lifecyclestage") or "",
                        "owner": props.get("hubspot_owner_id") or "",
                        "matched_on": f"{object_type}.{prop}",
                    }
                }

        return {
            "crm_account": {
                "exists": False,
                "record_id": "",
                "object_type": "",
                "lifecycle_stage": "",
                "owner": "",
                "matched_on": "",
            }
        }


class CrmSyncAgent(Agent):
    department = "revops"
    name = "crm_sync"
    description = (
        "Write the run's result back to HubSpot: firmographics, fit score, "
        "segment, assigned rep, and queue. Run this once the lead has been "
        "scored and routed, before finishing. Requires firmographics. Inferred "
        "firmographics are written to separate _inferred properties so a guess "
        "is never recorded as a retrieved fact."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "firmographics": {"type": "object", "description": "Enrichment output. Required."},
            "fit_score": {"type": "integer"},
            "segment": {"type": "string"},
            "assigned_rep": {"type": "string"},
            "queue": {"type": "string"},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "crm_account": {
                "type": "object",
                "description": "crm_lookup output, when it has already run.",
            },
        },
        "required": ["firmographics"],
    }

    output_schema = strict_schema(
        "crm_sync",
        {
            "record_id": {"type": "string"},
            "url": {"type": "string"},
            "action": {"type": "string", "description": "created | updated | skipped"},
            "written_properties": {"type": "object"},
            "provenance": {"type": "string", "description": "clay | inferred"},
            "dry_run": {"type": "boolean"},
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        firmographics = payload.get("firmographics")
        if not firmographics:
            raise ValueError("payload.firmographics is required; enrich the lead first")

        domain = str(firmographics.get("domain") or "").strip().lower()
        if not domain:
            raise ValueError("firmographics carries no domain to key the CRM record on")

        retrieved = firmographics.get("source") == "clay"
        properties = self._properties(payload, firmographics, retrieved)

        if not writes_enabled():
            # Return exactly what would have been written. A dry run that hides
            # the payload teaches nothing.
            return {
                "crm_sync": {
                    "record_id": "",
                    "url": "",
                    "action": "skipped",
                    "written_properties": properties,
                    "provenance": "clay" if retrieved else "inferred",
                    "dry_run": True,
                }
            }

        client = HubspotClient()
        existing = (payload.get("crm_account") or {}).get("record_id") or ""
        if not existing:
            found = client.search("companies", "domain", domain, COMPANY_PROPERTIES)
            existing = str(found.get("id", "")) if found else ""

        if existing:
            record = client.update("companies", existing, properties)
            action = "updated"
        else:
            record = client.create("companies", properties)
            action = "created"

        record_id = str(record.get("id", "") or existing)
        if not record_id:
            raise VendorError(f"HubSpot {action} returned no record id: {record}")

        return {
            "crm_sync": {
                "record_id": record_id,
                "url": client.record_url(None, "companies", record_id),
                "action": action,
                "written_properties": properties,
                "provenance": "clay" if retrieved else "inferred",
                "dry_run": False,
            }
        }

    def _properties(self, payload: dict, firmographics: dict, retrieved: bool) -> dict:
        """Map the blackboard onto HubSpot properties.

        The retrieved/inferred split is the whole point: a model's guess at
        headcount must not land in `numberofemployees`, where every downstream
        report will read it as measured.
        """
        properties: dict = {
            "name": firmographics.get("company_name") or "",
            "domain": firmographics.get("domain") or "",
        }

        facts = {
            prop: firmographics.get(field) or ""
            for field, prop in FACT_PROPERTIES.items()
        }
        if retrieved:
            properties.update({k: v for k, v in facts.items() if v != ""})
        else:
            properties.update(
                {f"{k}_inferred": v for k, v in facts.items() if v != ""}
            )

        # The run's own verdict is this system's output, not a vendor's data, so
        # it is written as fact regardless of how the firmographics were obtained.
        verdict = {
            "nexusgtm_fit_score": payload.get("fit_score"),
            "nexusgtm_segment": payload.get("segment") or "",
            "nexusgtm_assigned_rep": payload.get("assigned_rep") or "",
            "nexusgtm_queue": payload.get("queue") or "",
            "nexusgtm_firmographics_source": firmographics.get("source") or "inferred",
        }
        properties.update({k: v for k, v in verdict.items() if v not in (None, "")})

        reasons = payload.get("reasons") or []
        if reasons:
            properties["nexusgtm_score_reasons"] = "; ".join(str(r) for r in reasons)
        return properties
