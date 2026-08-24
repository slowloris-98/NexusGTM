# Test runs

Four demo runs, each started from a different department pane in the console.

Before any of them: four department servers (`:8101`–`:8104`), the API (`:8000`),
and `cd dashboard && npm run dev` (`:5173`). Each run blocks for its whole
duration and the console opens the finished run when it lands.

## 1. Marketing — inbound lead

Marketing tab → **Qualify an inbound lead** → Start.

- Name: `Priya Raman`
- Work email: `priya.raman@northwindlogistics.com`
- Company: `Northwind Logistics`

Shows the common path end to end: `crm_lookup → clay_enrich → lead_scoring →
lead_routing → outreach_draft → send_outreach → crm_sync`, crossing all four
departments.

## 2. RevOps — outbound sourcing

RevOps tab → **Source and qualify net-new accounts** → Start.

- Brief: `B2B SaaS companies, 200-1000 employees, North America, hiring RevOps roles.`

Starts with no lead at all. `clay_source` promotes a candidate and the run
continues as an ordinary qualification — the same agents, a different entry point.

## 3. Customer Success — churn save

Customer Success tab → **Run a churn save** → Start.

- Account domain: `brightpath.io`

An existing customer, not a new lead: `churn_detection` scores renewal risk
against `churn_risk_bands`, routes to the owner, and drafts a save play. Shows
the planner reading policy that runs opposite to the lead thresholds.

## 4. Marketing — ad campaign response

Marketing tab → **Work an ad campaign's respondents** → Start (no input).

Starts from a seed only (`campaign: all_active`, 14 days). `run_ad_campaigns`
produces the respondents, the strongest one is promoted, and qualification takes
over — proving the path is chosen at runtime, not encoded.

---

**Sales has no trigger.** No flow in the playbook declares one for it, by design —
sales is reached mid-run. After any run above, open the Sales tab: `lead_routing`,
`outreach_draft`, and `send_outreach` appear under Recent runs.

A fifth trigger exists if needed: Customer Success → **Work an expansion signal**
(account domain, e.g. `brightpath.io`).
