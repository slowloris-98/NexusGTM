# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary: GTM leadership** — a RevOps, marketing, or sales lead who owns the inbound
funnel. They open the control plane to answer two questions about an agent pipeline they
do not operate themselves: *did it work*, and *was it worth it*. They are not the person
who starts the department servers, reads the LangGraph code, or debugs a stack trace.

That gap is the central design fact of this product. The system's internals — agent
names, MCP endpoints, JSON blackboard payloads, halt codes like `halted_budget` — are
engineering vocabulary currently surfaced directly to an audience that thinks in leads,
reps, qualification, and cost per qualified lead. Future work must carry the meaning
across that gap rather than assume the reader shares the implementation's vocabulary.

No other audience was confirmed. Do not design for an evaluator, a demo reviewer, or the
operating engineer as a primary reader.

## Product Purpose

NexusGTM coordinates GTM agents across three departments — Marketing (enrichment),
RevOps (scoring, routing), and Sales (outreach drafting) — to take a raw inbound lead and
either get a personalized first-touch draft in front of the right rep, or terminate early
with a recorded reason.

The control plane exists so leadership has visibility into every orchestration, its
result, and its LLM cost per agent.

Success means a leader can see, without asking anyone: what ran, what it decided and why,
what it produced, and what it cost per outcome.

## Positioning

Three claims a neighboring product could not truthfully copy. All three were confirmed as
durable; future work must not blur them.

**1. Runtime planning, not a fixed pipeline.** The path is chosen per run by a
goal-directed planner over a config-driven registry — not a hardcoded sequence. A
pre-enriched lead skips enrichment. A lead scoring below the disqualify threshold
terminates after scoring and never reaches Sales. `config/playbook.yaml` is rendered into
the planner prompt as *text*, never parsed into a graph, precisely so it cannot degenerate
into the fixed sequence this design exists to avoid.

**2. Cost attributed per agent, computed in-process.** `llm_call` reads
`response.usage` — returned inline and synchronously by the Responses API — and prices it
from `config/llm.yaml`. Cost is not read back from a tracing service, because asynchronous
trace ingestion cannot supply numbers a mid-run budget guardrail needs. Cost crosses the
MCP process boundary inside the tool result (`{output, status, cost_events[]}`);
departments hold no database handle. The planner's own spend is metered as
`orchestrator/planner`, which is why `orchestrations.total_cost_usd` sums `cost_events`
rather than `runs.cost_usd` — rolling up runs would silently under-report.
LangSmith remains attached for tracing only and is never the system of record for cost.

**3. Every run is bounded and explainable.** Budget, step-ceiling, and loop guardrails
bound every run, and each planning step records the chosen agent, the rationale, and the
candidates considered. An invalid payload is fed back to the planner as an observation and
re-planned, not raised — because the planner's action space is every registered agent, so
it will sometimes pick one the blackboard cannot yet satisfy.

## Operating Context

A run begins with a raw inbound lead carrying a CRM reference. The orchestrator loops:
plan → invoke over MCP → guardrails → plan, until the goal is met, the lead is
disqualified, no agent fits, or a guardrail halts it.

The system runs as five processes — three FastMCP department servers (:8101 Marketing,
:8102 RevOps, :8103 Sales), the FastAPI control-plane API (:8000), and the Vite dev server
(:5173). Department servers must be up before the API, or the registry starts with a
partial action space; the dashboard surfaces unreachable departments as a banner and the
planner continues with whoever answered. Partial availability is a normal operating state,
not an error state.

The control plane is read-mostly. Its only write is launching a run.

Live data polls on intervals: orchestrations every 4s, registry every 15s, costs every 10s.

## Capabilities and Constraints

**Confirmed capabilities**

- Five surfaces exist today: Live, Results, Cost, Decision trail, Knowledge base.
- Live: in-flight runs, started-today and spend-today counts, registered departments
  (built from the registry, so new departments appear automatically), recent runs.
- Cost: total spend, cost per qualified lead, average cost per orchestration, spend by
  agent, spend over time, cost per outcome, department rollup.
- Decision trail: per-step agent, rationale, and candidates considered.
- Knowledge base: plain-text search across past runs (`GET /search`).
- Launching a run: `POST /orchestrations`.

**Terminology that is product truth**

- *Orchestration* — one lead's full journey. *Run* — one agent invocation within it.
- Outcomes: `completed`, `no_agent`. Halts: `halted_budget`, `halted_steps`, `halted_loop`.
- Score bands from `config/playbook.yaml`: route to sales ≥ 80, nurture ≥ 60,
  disqualify < 40.
- Negative signals: free email domain, competitor, out of region, under 10 employees.
- Default guardrails: `max_steps: 12`, `budget_usd: 0.50`.

**Technical constraints**

- Stack is fixed by the existing codebase: Python 3.12 (FastMCP, LangGraph, FastAPI,
  OpenAI Responses API) and a React 19 + TypeScript + Vite dashboard using Recharts.
- Store is SQLite in WAL mode; one connection per request.
- `/core` never imports a department. Adding a department is a FastMCP server plus three
  lines in `config/departments.yaml` — nothing under `/core` or `/dashboard` changes. Any
  design that hardcodes the current three departments or their agent names breaks this.
- Costs are sub-cent. Formatting must stay legible at that magnitude.

**Open / undecided**

- Accessibility standard: none required (see below).
- The current dashboard visual treatment is **not** settled and carries no authority as a
  commitment — see Brand Commitments.

## Brand Commitments

Only the naming is binding:

- The product name **NexusGTM**, and the department names **Marketing**, **RevOps**, and
  **Sales**.

Nothing else is committed. There is no logo, wordmark, required palette, required
typography, or binding voice. The existing token set in `dashboard/src/styles.css` is the
incumbent implementation, not an approved identity — treat it as evidence of the current
state, not as a constraint on future work.

## Evidence on Hand

- **Real, in-repo:** the working system itself — orchestrator, planner, registry, cost
  meter, three department servers, the API, and the dashboard. `arch.md` holds two Mermaid
  diagrams (system topology, run loop). `README.md` documents the guardrail table and the
  extension path. The test suite covers cost rollups, the budget halt, loop detection, the
  step ceiling, invalid-payload re-planning, and the decision trail, with `llm_call`
  monkeypatched.
- **Seeded data:** `scripts/seed.py` produces three deliberately distinct leads — a
  standard one, a pre-enriched one, and a free-email-domain one — so three different
  decision trails come from the same code. `nexusgtm.db` is committed with seeded runs.
- **Data horizon:** seeded now, real volume later. Design must be honest at three rows
  *and* hold up at hundreds of orchestrations without a rethink. Empty and sparse states
  are first-class, not afterthoughts.
- **Does not exist — never fabricate:** customers, logos, testimonials, case studies,
  benchmark numbers, pricing, uptime or SLA claims, team members, press, funding, or
  deployment/production claims. No real inbound lead data exists. Any number shown must
  come from the store.

## Product Principles

1. **Speak leadership's language, keep engineering's truth.** Translate agent names, halt
   codes, and payloads into funnel outcomes without softening what actually happened. A
   halted run is not a successful one.
2. **Never imply a fixed pipeline.** Marketing → RevOps → Sales is the *common* path, not
   the sequence. Anything that renders the flow must be able to show a skipped stage and an
   early termination as first-class, not as exceptions.
3. **Every number is traceable to a run.** Cost, counts, and outcomes come from the store
   and lead back to the orchestration that produced them. No estimated, illustrative, or
   placeholder figures.
4. **A decision is not complete without its rationale.** The reason an agent was chosen —
   and what else was considered — is the product, not metadata.
5. **Extensibility is visible, not just architectural.** The registry defines the taxonomy.
   A fourth department must appear without a single hand-authored change.
6. **Honest at three rows and at three hundred.** Sparse data reads as sparse, never as
   broken or as inflated.

## Accessibility & Inclusion

No formal standard was established as a product requirement. Sensible defaults apply, but
no conformance target is recorded as product truth.

One product-specific note independent of any standard: status is the first thing this
audience scans for, so how status renders is a product concern, not just a compliance one.
Status chips carry a text label alongside the color dot, so meaning does not depend on
color. As of the console rebuild, every status color also clears 4.5:1 on its own ground in
both themes, and `:focus-visible` is styled globally. No formal conformance pass has been
run — these are build-quality floors, not a verified standard.
