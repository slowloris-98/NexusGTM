"""Seed the store with runs so the dashboard has data on first load.

The lead set is deliberate: a standard lead, a pre-enriched one, and a
free-email-domain one. The last two exercise the non-default playbook patterns,
which is what shows the planner is planning rather than replaying a sequence.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from core.orchestrator import Orchestrator
from store import db

load_dotenv()

LEADS = [
    (
        "ACME-1001",
        {
            "name": "Priya Raman",
            "email": "priya.raman@northwindlogistics.com",
            "company": "Northwind Logistics",
            "title": "VP Revenue Operations",
            "source": "webinar",
            "note": "Attended the pipeline-forecasting webinar, asked about API access.",
        },
    ),
    (
        "ACME-1002",
        {
            "name": "Sam Okafor",
            "email": "sam@brightpath.io",
            "company": "Brightpath",
            "title": "Head of Growth",
            "source": "partner_referral",
            # Already enriched -> should trigger pre_enriched_lead and skip enrichment.
            "firmographics": {
                "company_name": "Brightpath",
                "domain": "brightpath.io",
                "industry": "B2B SaaS",
                "employee_count": 320,
                "revenue_band": "$25M-$50M",
                "hq_region": "North America",
                "buying_signals": ["hiring RevOps", "posted about CRM migration"],
                "free_email_domain": False,
                "confidence": 0.9,
            },
        },
    ),
    (
        "ACME-1003",
        {
            "name": "Alex Doe",
            "email": "alexdoe94@gmail.com",
            "company": "self-employed",
            "title": "Freelancer",
            "source": "content_download",
            # Free email + tiny -> should trigger early_disqualification.
            "note": "Downloaded a template. No company domain.",
        },
    ),
]


async def main() -> None:
    conn = db.init_db()
    orchestrator = Orchestrator(conn)

    for crm_reference_id, lead in LEADS:
        print(f"\n=== {crm_reference_id}  {lead['company']} ===")
        outcome = await orchestrator.run(crm_reference_id, lead)
        print(
            f"    status={outcome['status']}  outcome={outcome.get('outcome')}  "
            f"steps={outcome.get('steps')}  cost=${outcome.get('total_cost_usd', 0):.4f}"
        )

        trail = conn.execute(
            "SELECT step_no, chosen_agent, rationale FROM decisions "
            "WHERE orchestration_id = ? ORDER BY step_no",
            (outcome["orchestration_id"],),
        ).fetchall()
        for row in trail:
            agent = row["chosen_agent"] or "(finish)"
            print(f"      {row['step_no']}. {agent}: {row['rationale']}")

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
