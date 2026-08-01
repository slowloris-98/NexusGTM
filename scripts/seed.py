"""Seed the store with runs so the dashboard has data on first load.

The lead set is deliberate. Every row exists to make the planner take a visibly
different path -- through each score band, through each negative signal, and into
the guardrail halts -- because a dashboard where every run reads
`enrich -> score -> route -> draft` proves nothing about a planner.

`Seed.expect` is documentation and console output only. It is never handed to the
planner: telling it the answer would defeat the point of seeding these at all.

Runs are sequential on purpose. `core.llm.llm_call` is synchronous, and the
planner calls it from inside the graph's async `plan` node, so gathering these
would overlap the department MCP calls while still serializing every planner
decision -- a partial win paid for in scrambled `started_at` ordering.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from core.orchestrator import Orchestrator
from store import db

load_dotenv()


@dataclass
class Seed:
    ref: str
    expect: str
    lead: dict = field(default_factory=dict)
    # Guardrail overrides, for the two rows that exist to trip them. Applied as
    # instance attributes on the Orchestrator rather than written into
    # playbook.yaml -- config is lru_cached per process and shared with the
    # department servers, so staging one run through it would mean restarting
    # all of them.
    budget_usd: float | None = None
    max_steps: int | None = None


SEEDS = [
    Seed(
        ref="ACME-1001",
        expect="standard_qualification -- enrich, score, route, draft",
        lead={
            "name": "Priya Raman",
            "email": "priya.raman@northwindlogistics.com",
            "company": "Northwind Logistics",
            "title": "VP Revenue Operations",
            "source": "webinar",
            "note": "Attended the pipeline-forecasting webinar, asked about API access.",
        },
    ),
    Seed(
        ref="ACME-1002",
        expect="pre_enriched_lead -- skip enrichment, score directly",
        lead={
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
    Seed(
        ref="ACME-1003",
        expect="early_disqualification -- free_email_domain, stop after scoring",
        lead={
            "name": "Alex Doe",
            "email": "alexdoe94@gmail.com",
            "company": "self-employed",
            "title": "Freelancer",
            "source": "content_download",
            # Free email + tiny -> should trigger early_disqualification.
            "note": "Downloaded a template. No company domain.",
        },
    ),
    # ------------------------------------------------------------- score bands
    Seed(
        ref="ACME-1004",
        expect="mid band -- route to sdr_nurture on a 24h SLA",
        lead={
            "name": "Devika Nair",
            "email": "devika.nair@quillstack.com",
            "company": "Quillstack",
            "title": "Operations Manager",
            "source": "newsletter",
            # In ICP but small and signal-free, so it should land between the
            # nurture and route_to_sales thresholds rather than at either end.
            "note": "Subscribed to the monthly newsletter. No stated need, no timeline.",
        },
    ),
    Seed(
        ref="ACME-1009",
        expect="high band -- enterprise segment, ae_direct, 4h SLA",
        lead={
            "name": "Marcus Whitfield",
            "email": "m.whitfield@continentalfreight.com",
            "company": "Continental Freight Systems",
            "title": "Director of Revenue Systems",
            "source": "demo_request",
            "note": (
                "Requested a demo. Replacing an in-house lead router this quarter, "
                "budget approved, wants to be live before the end of Q3. 4,200 "
                "employees across the US and Canada."
            ),
        },
    ),
    # -------------------------------------------------- one per negative signal
    Seed(
        ref="ACME-1005",
        expect="competitor -- disqualify despite strong firmographics",
        lead={
            "name": "Tomas Herrera",
            "email": "therrera@fluxpointrevenue.com",
            "company": "Fluxpoint Revenue Cloud",
            "title": "VP Product",
            "source": "pricing_page",
            # Every firmographic here says qualify. The signal has to outweigh
            # all of them, which is the whole point of scoring them separately.
            "note": (
                "Spent 20 minutes on the pricing page. Fluxpoint sells revenue "
                "orchestration to the same buyer we do; likely a competitive teardown."
            ),
        },
    ),
    Seed(
        ref="ACME-1006",
        expect="out_of_region -- ideal ICP otherwise, still disqualified",
        lead={
            "name": "Yuki Tanaka",
            "email": "y.tanaka@sakuratechpartners.jp",
            "company": "Sakura Tech Partners",
            "title": "Head of Revenue Operations",
            "source": "webinar",
            "note": (
                "600-person B2B SaaS consultancy headquartered in Tokyo, with no "
                "North American or European entity. Asked about data residency."
            ),
        },
    ),
    Seed(
        ref="ACME-1010",
        expect="under_10_employees -- disqualify on a real corporate domain",
        lead={
            "name": "Eleanor Vane",
            "email": "eleanor@harborandvane.com",
            "company": "Harbor & Vane Consulting",
            "title": "Chief Executive Officer",
            "source": "content_download",
            # A senior title on a real domain: size has to carry this one alone,
            # without the free-email tell that gives ACME-1003 away.
            "note": "Three-person GTM advisory practice in Boston. Downloaded the ICP guide.",
        },
    ),
    # ------------------------------------------------------- planner judgment
    Seed(
        ref="ACME-1007",
        expect="pre-enriched but low confidence -- planner should re-enrich",
        lead={
            "name": "Rachel Osei",
            "email": "rosei@meridianhealthsystems.com",
            "company": "Meridian Health Systems",
            "title": "Director of Commercial Operations",
            "source": "webinar",
            # Two patterns apply at once. pre_enriched_lead says score directly;
            # standard_qualification says scoring without firmographics is noise.
            # This payload is technically enriched and practically not, so the
            # planner has to judge rather than pattern-match.
            "firmographics": {
                "company_name": "Meridian Health Systems",
                "domain": "meridianhealthsystems.com",
                "industry": "unknown",
                "employee_count": None,
                "revenue_band": "unknown",
                "hq_region": "North America",
                "buying_signals": [],
                "free_email_domain": False,
                "confidence": 0.25,
            },
        },
    ),
    Seed(
        ref="ACME-1008",
        expect="sparse lead -- little to enrich from, low confidence downstream",
        lead={
            "name": "J. Okonkwo",
            "email": "jokonkwo@lattice-industrial.com",
            "source": "webinar",
        },
    ),
    # ------------------------------------------------------------- guardrails
    # Ordinary qualifying leads: the ceiling is the interesting part, not the
    # lead. Both should halt mid-run and finish with no verdict, which is the
    # honest reading of a run cut off before it reached one.
    Seed(
        ref="ACME-1011",
        expect="halted_budget -- ceiling cut to $0.02",
        budget_usd=0.02,
        lead={
            "name": "Ingrid Halvorsen",
            "email": "ingrid.h@vertexpayments.com",
            "company": "Vertex Payments",
            "title": "VP Revenue Operations",
            "source": "demo_request",
            "note": "1,100-person payments platform, EU and US. Evaluating lead routing.",
        },
    ),
    Seed(
        ref="ACME-1012",
        expect="halted_steps -- ceiling cut to 2 steps",
        max_steps=2,
        lead={
            "name": "Daniel Brecht",
            "email": "d.brecht@aldergatesoftware.com",
            "company": "Aldergate Software",
            "title": "Head of Sales Operations",
            "source": "partner_referral",
            "note": "450-person B2B software vendor in Manchester. Referred by a partner.",
        },
    ),
]


async def main() -> None:
    conn = db.init_db()
    orchestrator = Orchestrator(conn)

    # One Orchestrator serves every seed, so a per-seed override would leak into
    # the following runs if it were not reset on each pass.
    default_budget = orchestrator.budget_usd
    default_steps = orchestrator.max_steps

    results: list[tuple[Seed, dict]] = []

    for seed in SEEDS:
        orchestrator.budget_usd = seed.budget_usd or default_budget
        orchestrator.max_steps = seed.max_steps or default_steps

        print(f"\n=== {seed.ref}  {seed.lead.get('company', '(no company given)')} ===")
        print(f"    expect: {seed.expect}")

        outcome = await orchestrator.run(seed.ref, seed.lead)
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

        results.append((seed, outcome))

    print_summary(results)
    conn.close()


def print_summary(results: list[tuple[Seed, dict]]) -> None:
    """Every row on one screen -- thirteen decision trails are not."""
    print(f"\n\n{'ref':<12} {'status':<16} {'outcome':<14} {'steps':>5} {'cost':>9}")
    print("-" * 60)
    for seed, outcome in results:
        print(
            f"{seed.ref:<12} {outcome['status']:<16} "
            f"{str(outcome.get('outcome')):<14} {outcome.get('steps', 0):>5} "
            f"{outcome.get('total_cost_usd', 0):>9.4f}"
        )
    total = sum(o.get("total_cost_usd", 0) for _, o in results)
    print("-" * 60)
    print(f"{len(results)} orchestrations{'':<31}{total:>9.4f}")


if __name__ == "__main__":
    asyncio.run(main())
