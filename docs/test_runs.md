# Test runs

Five demo runs, each started from a department pane in the console.

Before any of them: four department servers (`:8101`–`:8104`), the API (`:8000`),
and `cd dashboard && npm run dev` (`:5173`). Each run blocks for its whole
duration and the console opens the finished run when it lands.

The seeded leads carry no phone number, so `python scripts/seed.py` never places a
call however the planner is feeling — only run 5 can.

## 1. Marketing — inbound lead

Marketing tab → **Qualify an inbound lead** → Start.

- Name: `Priya Raman`
- Work email: `priya.raman@northwindlogistics.com`
- Company: `Northwind Logistics`

Shows the common path end to end: `crm_lookup → clay_enrich → scoring →
routing → outreach_draft → send_outreach → crm_sync`, crossing all four
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

## 5. Sales — voice outreach

The same Marketing trigger as run 1, with a phone number on the lead.

Marketing tab → **Qualify an inbound lead** → Start.

- Name: `Priya Raman`
- Work email: `priya.raman@vercel.com`
- Company: `Vercel`
- Phone: `+14155550123` — yours, in E.164

Four things decide whether this run actually dials, and the first three are easy to
get wrong:

- **`VOICE_ENABLED=1`.** Off (the default), `voice_outreach` returns a synthetic
  `dry-…` id, builds no HTTP client, and nothing rings. Everything else about the
  run is identical, which makes the dry pass the right way to rehearse this.
- **E.164, with the country code.** `voice_outreach` refuses a lead with no phone,
  and the vendor rejects `4155550123` for the missing `+1`.
- **The lead must reach the high band.** The planner prefers voice only for a
  high-band account with a number, so a score under 80 lands on `send_outreach`
  instead. A real corporate domain gets you there; a free-email one is a negative
  signal and the run terminates before outreach.
- **A reason to call rather than email** is what tips a borderline account. The
  console form has no free-text field, so the variant that says *"asked to be
  phoned today rather than emailed"* is the `curl` in the README's Voice section.

What it shows: `… → routing → voice_outreach`, and then the run **parks** — status
`running`, six steps, no outcome yet. The graph has exited while the call is still
up. When it ends, the vendor's webhook resumes the *same* orchestration and
`voice_call_outcome` and `crm_sync` appear as steps 7 and 8 on that same row.

Without a `serverUrl` configured on the vendor's assistant no webhook arrives, so
drive the second half by hand:

```bash
python scripts/voice_replay.py --list        # parked runs awaiting a call
python scripts/voice_replay.py <orchestration_id>
```

If the planner picks `send_outreach` anyway, that is the design working, not a
failure — read the rationale in the Decision trail and it will tell you which band
the lead landed in.

---

**Sales has no trigger.** No flow in the playbook declares one for it, by design —
sales is reached mid-run. After any run above, open the Sales tab:
`outreach_draft` and `send_outreach` appear under Recent runs.

A fifth trigger exists if needed: Customer Success → **Work an expansion signal**
(account domain, e.g. `brightpath.io`).
