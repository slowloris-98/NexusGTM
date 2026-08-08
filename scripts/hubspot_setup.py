"""Prepare a HubSpot portal for `crm_sync`, and check that it is prepared.

`crm_sync` writes properties that no HubSpot portal has out of the box -- the
run's verdict, and the `*_inferred` half of the provenance split. HubSpot rejects
a write naming a property it does not know, so without this script the first live
sync fails on its first call and every run after it.

The property list is derived from `FACT_PROPERTIES` and `VERDICT_PROPERTIES` in
`departments/revops/crm.py` rather than repeated here. A property added to the
agent and forgotten here is exactly the failure this script exists to prevent, so
there is no second list to forget to update.

Three modes, in the order you want them:

    --check   read-only. Token, portal, and which properties are missing.
    (none)    create the group and any missing property. Idempotent.
    --seed    put a few companies and contacts in an empty portal, so `crm_lookup`
              has something to match. Idempotent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from departments.revops.clients import HubspotClient, VendorError
from departments.revops.crm import (
    COMPANY_PROPERTIES,
    FACT_PROPERTIES,
    VERDICT_PROPERTIES,
)

load_dotenv()

OBJECT_TYPE = "companies"
GROUP_NAME = "nexusgtm"
GROUP_LABEL = "NexusGTM"

# Properties HubSpot ships with. `crm_sync` writes these under their own names
# and they must not be created -- a create would 409, and worse, a create that
# succeeded would mean the name was never the built-in in the first place.
BUILT_IN = {"name", "domain", "numberofemployees", "country"}

# Only these leads are seeded. The rest of `SEEDS` is left out on purpose: a
# portal where every lookup hits proves less than one where some legitimately
# miss, and `crm_lookup` returning exists=false is a path worth exercising live.
SEED_REFS = ("ACME-1001", "ACME-1004", "ACME-1009")

_LABELS = {
    "nexusgtm_fit_score": "Fit Score",
    "nexusgtm_segment": "Segment",
    "nexusgtm_assigned_rep": "Assigned Rep",
    "nexusgtm_queue": "Queue",
    "nexusgtm_firmographics_source": "Firmographics Source",
    "nexusgtm_score_reasons": "Score Reasons",
    "nexusgtm_industry": "Industry (NexusGTM)",
    "nexusgtm_revenue_band": "Revenue Band",
}


def _label(name: str) -> str:
    if name.endswith("_inferred"):
        return f"{_label(name[: -len('_inferred')])} (inferred)"
    return _LABELS.get(name, name.replace("_", " ").title())


def _spec(name: str, *, number: bool = False, long_text: bool = False) -> dict:
    return {
        "name": name,
        "label": _label(name),
        "groupName": GROUP_NAME,
        "type": "number" if number else "string",
        "fieldType": "number" if number else ("textarea" if long_text else "text"),
        "description": "Written by NexusGTM. See departments/revops/crm.py.",
    }


def property_specs() -> list[dict]:
    """Every property this portal needs that it does not already have by default."""
    specs: list[dict] = []

    for prop in FACT_PROPERTIES.values():
        if prop not in BUILT_IN:
            specs.append(_spec(prop))
        # The inferred half is always custom, and always text -- a model's guess
        # at headcount may well be "500+", which no number property accepts.
        specs.append(_spec(f"{prop}_inferred"))

    for prop in VERDICT_PROPERTIES:
        specs.append(
            _spec(
                prop,
                number=prop == "nexusgtm_fit_score",
                long_text=prop == "nexusgtm_score_reasons",
            )
        )
    return specs


# --------------------------------------------------------------------- check


def cmd_check(client: HubspotClient) -> int:
    info = client.account_info()
    portal = info.get("portalId")
    print(f"token   ok -- portal {portal} ({info.get('accountType', 'unknown type')})")
    print(f"         set HUBSPOT_PORTAL_ID={portal} in .env for clickable record URLs")

    specs = property_specs()
    missing = [s["name"] for s in specs if client.get_property(OBJECT_TYPE, s["name"]) is None]
    present = len(specs) - len(missing)
    print(f"\nproperties  {present}/{len(specs)} present on `{OBJECT_TYPE}`")
    for name in missing:
        print(f"    missing  {name}")

    seeded = [
        lead
        for lead in seed_leads()
        if client.search(OBJECT_TYPE, "domain", lead["domain"], COMPANY_PROPERTIES)
    ]
    print(f"\nseed        {len(seeded)}/{len(seed_leads())} companies present")

    if missing:
        print("\nRun `python scripts/hubspot_setup.py` to create the missing properties.")
        return 1
    print("\nPortal is ready. crm_sync can write every property it names.")
    return 0


# ----------------------------------------------------------------- provision


def cmd_provision(client: HubspotClient) -> int:
    if client.get_property_group(OBJECT_TYPE, GROUP_NAME) is None:
        client.create_property_group(
            OBJECT_TYPE,
            {"name": GROUP_NAME, "label": GROUP_LABEL, "displayOrder": -1},
        )
        print(f"created  group {GROUP_NAME}")
    else:
        print(f"exists   group {GROUP_NAME}")

    for spec in property_specs():
        if client.get_property(OBJECT_TYPE, spec["name"]) is not None:
            print(f"exists   {spec['name']}")
            continue
        client.create_property(OBJECT_TYPE, spec)
        print(f"created  {spec['name']}  ({spec['type']}/{spec['fieldType']})")

    print(f"\nDone. Portal UI: Settings -> Properties -> Companies -> {GROUP_LABEL}")
    return 0


# ---------------------------------------------------------------------- seed


def seed_leads() -> list[dict]:
    """The subset of `scripts/seed.py`'s leads that gets a CRM record.

    Imported rather than restated so the two cannot drift: a lead whose address
    changes in seed.py must not keep matching a stale company here.
    """
    from scripts.seed import SEEDS

    by_ref = {s.ref: s for s in SEEDS}
    leads = []
    for ref in SEED_REFS:
        seed = by_ref.get(ref)
        if seed is None:
            raise SystemExit(f"{ref} is no longer in scripts/seed.py -- update SEED_REFS")
        email = seed.lead["email"]
        first, _, last = seed.lead["name"].partition(" ")
        leads.append(
            {
                "ref": ref,
                "email": email,
                "domain": email.split("@", 1)[1].lower(),
                "company": seed.lead["company"],
                "firstname": first,
                "lastname": last or first,
            }
        )
    return leads


def cmd_seed(client: HubspotClient) -> int:
    for lead in seed_leads():
        # Search before create, the same way crm_sync does, so running this twice
        # leaves one record rather than two.
        company = client.search(OBJECT_TYPE, "domain", lead["domain"], COMPANY_PROPERTIES)
        if company:
            print(f"exists   company {lead['domain']}  (id {company['id']})")
        else:
            # Name, domain and lifecycle stage only. No firmographics and none of
            # the nexusgtm_* properties: those have to arrive from a real run, or
            # verifying crm_sync against this portal proves nothing.
            created = client.create(
                OBJECT_TYPE,
                {
                    "name": lead["company"],
                    "domain": lead["domain"],
                    "lifecyclestage": "lead",
                },
            )
            print(f"created  company {lead['domain']}  (id {created.get('id')})")

        contact = client.search("contacts", "email", lead["email"], ["email"])
        if contact:
            print(f"exists   contact {lead['email']}  (id {contact['id']})")
        else:
            created = client.create(
                "contacts",
                {
                    "email": lead["email"],
                    "firstname": lead["firstname"],
                    "lastname": lead["lastname"],
                    "company": lead["company"],
                    "lifecyclestage": "lead",
                },
            )
            print(f"created  contact {lead['email']}  (id {created.get('id')})")

    print(f"\nSeeded {len(SEED_REFS)} of the leads in scripts/seed.py, on purpose.")
    print("Leads outside that set should make crm_lookup return exists=false.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="read-only: token, portal, missing properties"
    )
    mode.add_argument(
        "--seed", action="store_true", help="create a few companies and contacts to match on"
    )
    args = parser.parse_args()

    try:
        client = HubspotClient()
        if args.check:
            return cmd_check(client)
        if args.seed:
            return cmd_seed(client)
        return cmd_provision(client)
    except VendorError as exc:
        # The whole point of this script is a legible setup, so a vendor failure
        # gets the message rather than a traceback through httpx.
        print(f"\nHubSpot: {exc}", file=sys.stderr)
        if "credentials" in str(exc):
            print(
                "\nCheck HUBSPOT_ACCESS_TOKEN in .env, and that the private app has "
                "crm.schemas.companies.write (properties), crm.objects.companies.write "
                "and crm.objects.contacts.write (seeding).",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
