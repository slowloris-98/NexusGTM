# Clay and HubSpot — onboarding

Both live in RevOps, and both are optional. Leave their keys unset and `clay_enrich` and
`crm_sync` return `status="error"`, which the planner treats as an observation and re-plans
around; the rest of the system still runs. This page is what to do when you want them live.

```bash
CLAY_API_KEY=
CLAY_ENRICH_ROUTINE_ID=          # function:t_... from your own Clay workspace
CLAY_CREDIT_USD=                 # your effective per-credit rate
HUBSPOT_ACCESS_TOKEN=
CRM_WRITE_ENABLED=false          # dry-run by default
```

## Retrieved data and inferred data are not the same thing

Clay and HubSpot live in RevOps, because in a real GTM org RevOps owns the data foundation
and the CRM as system of record. Clay is the *retrieval* half of the enrichment waterfall:
`clay_enrich` fetches firmographics from data providers, and `marketing.enrichment` — which
only ever estimated them from a model — is now the fallback for when Clay has no match.

One Clay call returns both halves: the firmographics and the raw signal columns — job
openings, latest funding, recent news, technologies — which `clay_enrich` normalises into
short buying-signal phrases in Python. Not in Clay, whose AI columns hit a token limit once
they reference several enrichment columns at once, and not through a model here either: a
model asked to summarise retrieved data starts guessing again, on the one part of the
payload that is meant to be a fact about the world. The raw blobs stay inside the agent,
because `outreach_draft` serialises its whole payload into a prompt — anything published is
re-tokenised at every later step and charged against the budget guardrail.

Every set of firmographics carries a `source` stamp, and `crm_sync` reads it. Retrieved
values are written to HubSpot under their canonical property names; inferred ones go to
separate `*_inferred` properties. A model's guess at headcount must never enter the system
of record looking like a measurement.

What Clay is deliberately *not* given: scoring and routing. Clay can run both, but the
score bands would then live in a vendor UI as a second source of truth alongside
`config/playbook.yaml`, and the planner — not Clay — is what decides in this system.

## The Clay function

Build **one** Function in your workspace, with a text input named `Domain`. `clay_enrich`
also sends `company` and `email`, which give the waterfall more to match on.

The **firmographic** columns are read through aliases, because Clay's own "Enrich Company"
titles vary between workspaces:

| Column | Contents |
| --- | --- |
| `Company Name` | string — **required**; its absence is what the agent reads as a miss |
| `Domain`, `Industry`, `Annual Revenue`, `HQ Region` | string |
| `Employee Count` | number or text — `"1,200"` and `"500+"` both parse |

The **signal** outputs are not. Clay's output picker emits scalar leaves, so tick exactly
these — each is read by name, once, in [departments/revops/clay.py](../departments/revops/clay.py):

| Clay column | Leaf to tick | Output name |
| --- | --- | --- |
| `job_postings` | Total Jobs Found | `Total Jobs Found` |
| `latest_funding` | Funding Stage / Latest Funding Amount / Last Funding Date | same |
| `recent_news` | News › 0/1/2 › News Title and Publish Date | `News Title`, `News Title 2`, `News Title 3` (+ `Publish Date` …) |
| `technologies` | Product Names | `Product Names` |

Clay names repeated leaves from the second one — `News Title`, then `News Title 2` — and the
reader walks that sequence for as far as you map it, so a fourth pair needs no code change.

Leave everything else unticked. Not `Website` (the agent falls back to the domain it looked
up, already stripped of scheme and `www`; ticking `Website` puts `https://…` into HubSpot),
not `Product Name` singular (one product out of several, silently), not `Jobs`, not
`Description`. Unmapped leaves never leave Clay, which is most of why this shape is cheaper
than returning the whole columns.

Names are matched on letters and digits only, so `Employee Count`, `employee_count` and
`employeeCount` are the same field — but only one name per field is tried. **If a signal
output is named anything else, it reads as no data at all.** That is not hypothetical: it is
how all four columns sat dead behind an empty `buying_signals` until someone compared a real
response against the code. A column with none of its fields present is now reported in
`clay_enrichment.reason` for exactly that reason. What that note *cannot* tell you is a
column whose provider found nothing and returned null leaves — indistinguishable from
unmapped. The tell is not per-run: a mapping fault looks identical on every row, a vendor
miss varies by company.

`job_postings` publishes a count rather than job titles, deliberately — see `_job_phrases`.
If your jobs enrichment exposes a posted date, map it and titles become defensible again.

Do **not** add an AI column that synthesises these into a signals string — that is the token
limit, and the agent does it in Python. (If your workspace already has a hand-written one,
name it `Signals`; it stays shape-tolerant and leads the phrase list.) Copy the
`function:t_...` id from the Function's own page into `CLAY_ENRICH_ROUTINE_ID`.

How far back an event still counts, and how many phrases a category may contribute, are
policy: `signals.recency_days` and `signals.max_per_category` in
[config/playbook.yaml](../config/playbook.yaml). Neither reaches every column — the file's own
comment says which.

Clay bills credits rather than tokens. Set `CLAY_CREDIT_USD` to your effective rate so
vendor spend reaches the same budget guardrail as LLM spend; unset, it prices at zero, the
same way `core.llm.price_of` handles a model missing from the table rather than guessing.

## The HubSpot portal

Get a **developer test account**: sign up at [developers.hubspot.com](https://developers.hubspot.com),
then **Development → Testing → Create developer test account**. It asks for a company name,
not a website, and the account is free and disposable — which matters, because the first
thing you should do to a CRM you are automating is not do it to a real one. Create a private
app inside it (**Settings → Integrations → Private Apps**) and copy the token into
`HUBSPOT_ACCESS_TOKEN`. [.env.example](../.env.example) lists the five scopes.

Then, **once per portal**:

```bash
python scripts/hubspot_setup.py --check   # read-only: token, portal id, what is missing
python scripts/hubspot_setup.py           # define the properties
python scripts/hubspot_setup.py --seed    # optional: records for crm_lookup to match
```

`crm_sync` names twelve properties no portal has by default — the run's verdict, and the
`_inferred` half of the provenance split — and HubSpot rejects a write naming a property it
does not know. So this is not optional setup: without it the first live sync fails, and so
does every one after it. The script reads its list from `FACT_PROPERTIES` and
`VERDICT_PROPERTIES` in [departments/revops/crm.py](../departments/revops/crm.py) rather than
keeping its own, and a test asserts the agent never writes a property outside it.

Two of those properties exist because HubSpot's own would reject the value:

| Field | HubSpot's built-in | Written to instead |
| --- | --- | --- |
| `industry` | enumeration — validated against the portal's option list since 2023 | `nexusgtm_industry` |
| `revenue_band` | `annualrevenue`, a number — a band is not one | `nexusgtm_revenue_band` |

`--seed` covers three of the leads in [scripts/seed.py](../scripts/seed.py), not all of them,
on purpose: a portal where every lookup hits proves less than one where some legitimately
miss, and `crm_lookup` returning `exists: false` is a path worth exercising against a real
portal. Seeded records carry a name, domain and lifecycle stage only — everything else has
to arrive from a real run, or verifying `crm_sync` against them proves nothing.

`CRM_WRITE_ENABLED` is off by default, so `crm_sync` returns the payload it *would* have
written with `dry_run: true`. Compare that payload against what `--check` reported as
present: it catches a bad property name without writing anything. Turn writes on only
against a sandbox portal — a seed pass is fifteen runs — and **restart the RevOps server**
afterwards, since `load_dotenv()` runs at import and an edited `.env` does not reach a
process already running.
