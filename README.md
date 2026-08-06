# NexusGTM — Observable GTM Agent Orchestration

NexusGTM is a full GTM solution built on an agent orchestration layer. Departments
(Marketing, RevOps, Sales, etc) run agents that hand work across department boundaries, and
a goal-directed planner sits at the center of that layer, picking the next agent at runtime
from a config-driven registry — bringing real-time flexibility and decision making to every
orchestration.

Because the path is decided at runtime, the work needs to be observable. That is what the
dashboard is for: live runs as they execute, every agent across every department, each
cross-departmental hand-off, and the LLM cost attached to it — per agent, per run, per
orchestration. Built for leadership: what the agents are doing, how work moves between
departments, and what it costs.

![Dashboard SS1](docs/dashboard_ss3.png)
![Dashboard SS2](docs/dashboard_ss4.png)

## Architecture

![System Architecture](docs/sys_arch.png)

| Piece | What it does |
|---|---|
| `core/llm.py` | The only place a provider SDK is touched. Returns text **and** token usage. |
| `core/costmeter.py` | Attributes LLM cost to the agent that spent it; rolls it up. |
| `core/agent.py` | Agent contract + the helper that exposes an agent as an MCP tool. |
| `core/registry.py` | Loads `departments.yaml`, discovers tools — the planner's action space. |
| `core/planner.py` | Renders the playbook into the prompt; returns the next routing decision. |
| `core/orchestrator.py` | LangGraph graph: plan → invoke → guardrails → plan. |
| `store/` | SQLite (WAL). Orchestrations, runs, cost events, decisions. Also the knowledge base. |
| `api/` + `dashboard/` | FastAPI JSON API, React control plane. |

New departments plug in without touching `/core`.

Example path:

```
RevOps                      RevOps                   Sales                          RevOps
  crm_lookup → clay_enrich → scoring → routing   →   outreach_draft → send_outreach → crm_sync
                    ↓ no match
              Marketing: enrichment
```

The path above is the *common* one, not a hardcoded sequence. A pre-enriched lead skips
enrichment; a disqualified lead terminates after scoring and never reaches Sales; an
outbound run starts at `clay_source` with no lead at all; a churn run starts in Customer
Success and routes on renewal risk rather than ICP fit. See [Flows](#flows).

### Retrieved data and inferred data are not the same thing

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

### Cost is computed in-process, not read back from a tracing service

The source design sourced all cost from LangSmith. That cannot work: LangSmith ingests
traces asynchronously, so the numbers are not available when the mid-run budget guardrail
needs them. Langfuse is worse for this — it computes cost server-side at ingestion and
exposes nothing on the local span.

So `llm_call` reads `response.usage` — which the Responses API returns inline and
synchronously — and prices it from the table in `config/llm.yaml`. LangSmith stays on as
the trace/debug surface via `wrap_openai`. It is not the system of record for cost.

Cost crosses the MCP process boundary in the tool result: every tool returns
`{output, status, cost_events[]}` and the orchestrator records them. Departments hold no
database handle.

The planner is an LLM call too, so its spend is metered as `orchestrator/planner`. It
belongs to no single run, which is why `orchestrations.total_cost_usd` sums `cost_events`
rather than `runs.cost_usd` — rolling up the runs would silently under-report every
orchestration.

## Setup

```bash
uv venv --python 3.12
uv pip install -e .
uv pip install --group dev                    # pytest et al

# note: `.[dev]` does not work -- dev is a PEP 735 dependency-group, not an extra

cp .env.example .env                          # add your OPENAI_API_KEY

cd dashboard && npm install && cd ..
```

To run the Clay and HubSpot agents live, also set `CLAY_API_KEY`, `CLAY_ENRICH_ROUTINE_ID`,
and `HUBSPOT_ACCESS_TOKEN` in `.env`. Routine ids come from your own Clay workspace —
routines are workspace-defined, which is also why `_pluck` in
[departments/revops/clay.py](departments/revops/clay.py) reads several plausible column
names. Leave them unset and those agents return `status="error"`, which the planner treats
as an observation and re-plans around; the rest of the system still runs.

### The Clay function

Build **one** Function in your workspace, with a text input named `Domain`. `clay_enrich`
also sends `company` and `email`, which give the waterfall more to match on. Its output
columns:

| Column | Contents |
| --- | --- |
| `Company Name` | string — **required**; its absence is what the agent reads as a miss |
| `Domain`, `Industry`, `Revenue Band`, `HQ Region` | string |
| `Employee Count` | number or text — `"1,200"` and `"500+"` both parse |
| `Job Openings` | list of `{title, posted_at}`, list of titles, or a plain count |
| `Latest Funding` | `{amount, round, date}`, or a list of rounds |
| `Recent News` | list of `{title, date}` or list of headlines |
| `Technologies` | list of names, list of `{name}`, or a comma-joined string |

Column names are matched on letters and digits only, so `Employee Count`, `employee_count`,
and `employeeCount` are the same column. Do **not** add an AI column that synthesises those
into a signals string — that is the token limit, and the agent does it in Python. (If your
workspace already has one, name it `Signals` and its contents lead the phrase list.) Copy
the `function:t_...` id from the Function's own page into `CLAY_ENRICH_ROUTINE_ID`.

How far back an event still counts, and how many phrases a category may contribute, are
policy: `signals.recency_days` and `signals.max_per_category` in
[config/playbook.yaml](config/playbook.yaml).

`CRM_WRITE_ENABLED` is off by default, so `crm_sync` returns the payload it *would* have
written with `dry_run: true`. Turn it on only against a HubSpot sandbox portal first — a
seed pass is fifteen runs.

Clay bills credits rather than tokens. Set `CLAY_CREDIT_USD` to your effective rate so
vendor spend reaches the same budget guardrail as LLM spend; unset, it prices at zero, the
same way `core.llm.price_of` handles a model missing from the table rather than guessing.

## Running

Six processes, each in its own terminal. The department servers must be up before the
API, or the registry starts with a partial action space (it will name what is missing).

One venv serves all five Python processes — activate it in each terminal:

```powershell
.venv\Scripts\Activate.ps1        # PowerShell
source .venv/Scripts/activate     # Git Bash
```

```bash
python -m departments.marketing.server         # :8101
python -m departments.revops.server            # :8102
python -m departments.sales.server             # :8103
python -m departments.customer_success.server  # :8104

python -m uvicorn api.main:app --port 8000     # :8000
cd dashboard && npm run dev                    # :5173  (Node — no venv needed)
```

Or skip activation and call the interpreter directly:
`.venv/Scripts/python.exe -m departments.marketing.server`.

> Config is cached per process. Editing `config/playbook.yaml` — the flows, the ICP, the
> score bands, the negative signals — means restarting all four department servers too,
> not just the API, since their agents render those values into their prompts.

Then seed some runs and open <http://localhost:5173>:

```bash
python scripts/seed.py
```

The three seeded leads are deliberate — a standard one, a pre-enriched one, and a
free-email-domain one — so the dashboard shows three different decision trails from the
same code.

> The Vite dev server binds to `localhost`, not `127.0.0.1`. Use the former.

## Extending

Adding a department is a server plus three lines of config. Nothing under `/core` or
`/dashboard` changes; the dashboard taxonomy is built from the registry.

1. Stand up a FastMCP server whose agents subclass `core.agent.Agent` and are exposed
   with `core.agent.register`.
2. Add it to `config/departments.yaml`:

```yaml
  - id: partnerships
    name: Partnerships
    mcp_endpoint: http://127.0.0.1:8105/mcp
```

Its tools enter the planner's action space on the next discovery, and it gets a tab in the
console with no frontend edit at all.

Changing *how* the planner works is `config/playbook.yaml` — flows, patterns described as
guidance, and thresholds. It is rendered into the prompt as text, never parsed into a graph;
parsing it into branch logic would make it the fixed sequence this design exists to avoid.

### Flows

`flows:` names the scenarios the system runs. Each declares a `when` line, a goal, success
criteria, and a few patterns; `common.patterns` holds the rules that hold whichever flow is
in force.

| Flow | Starts from |
|---|---|
| `inbound_lead_qualification` | a raw lead — a form fill, a webinar, a referral |
| `outbound_sourcing` | an ICP brief, with `clay_source` producing the lead |
| `ad_campaign_response` | a live campaign's respondents, with no account yet |
| `account_expansion` | an existing customer showing an upsell signal |
| `churn_save` | an existing customer at renewal risk |

A flow is guidance and a label, never a gate: the planner sees a one-line index of every
flow plus the full detail of the current one, restates which it is working under each step,
and may switch when the state changes shape — an inbound lead that `crm_lookup` reveals to
be an existing customer. The action space stays every registered agent throughout.

A flow with a `trigger:` block gets a button on that department's tab in the console. The
trigger is also how the planner knows which scenario it is in at step one, which is why
starting a run needs no classification call.

## Guardrails

Every run is bounded. The table is in `core/orchestrator.py`:

| Condition | Result |
|---|---|
| spend exceeds `budget_usd` | `halted_budget` |
| step count exceeds `max_steps` | `halted_steps` |
| same agent, same input, twice | `halted_loop` |
| payload fails the agent's schema | fed back to the planner as an observation |
| tool error | retry, then re-plan, then halt |
| no suitable agent | `no_agent` |

Schema failures are fed back rather than raised on purpose: the planner's action space is
every registered agent, so it *will* sometimes pick one the blackboard cannot satisfy.
That is a re-plan, not a crash.

## Tests

```bash
pytest
```

Covers the cost rollups, the budget halt, loop detection, the step ceiling, invalid-payload
re-planning, and the decision trail. `core.llm.llm_call` is monkeypatched — no network, no
API key.
