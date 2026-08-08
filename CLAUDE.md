# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv pip install -e . && uv pip install --group dev   # `.[dev]` fails -- dev is a PEP 735 group

pytest
pytest tests/test_orchestrator_guardrails.py::test_budget_halt   # single test
pytest -k budget

cd dashboard && npm run dev     # :5173, proxies /api -> :8000;  npm run build to typecheck
```

Six processes, department servers **before** the API or the registry starts with a partial
action space. One venv serves all five Python ones.

```bash
python -m departments.marketing.server   # :8101   (revops :8102, sales :8103,
python -m uvicorn api.main:app --port 8000  #         customer_success :8104)
python scripts/seed.py
```

Config is `lru_cache`d **per process** — editing `config/playbook.yaml` means restarting all
four department servers too, not just the API, including ones that only read `icp`. No linter
or formatter is configured.

## Architecture

A goal-directed planner picks the next agent at runtime from a config-driven registry. The
Marketing → RevOps → Sales path is the common one, not a sequence encoded anywhere. The loop
is a two-node LangGraph in [core/orchestrator.py](core/orchestrator.py): `plan → invoke → plan`.

Invariants that are easy to break:

- **`/core` never imports a department.** Departments are entries in `config/departments.yaml`,
  discovered as MCP tools. Adding one = a FastMCP server + config; core, api, and dashboard
  are untouched (the dashboard taxonomy comes from the registry).
- **The playbook is prompt text, never branch logic.** Parsing `playbook.yaml` into a graph
  or `if` statements recreates the fixed sequence this design avoids. Its `guardrails:` block
  is the sole exception, and the only part of the file that reaches code.
- **A flow is a label, not a gate.** `flows:` in the playbook names the scenarios the system
  runs — inbound, outbound, campaign, expansion, churn. The planner is told which one is in
  force and restates it every step, and the orchestrator records it on `orchestrations.flow`
  and `decisions.flow`. But the action space stays every registered agent under every flow:
  filtering `render_agents` by flow would be exactly the branch logic above, and would make a
  mid-run switch unreachable. Only the *prompt* narrows — an index line for every flow, full
  detail for the current one.
- **Every LLM call goes through `core.llm.llm_call`** — the only provider SDK site and the
  single test patch point. Call it via the module so monkeypatching works.
- **Cost is computed in-process** from `response.usage` priced against `config/llm.yaml`.
  LangSmith ingests asynchronously, so it cannot feed the mid-run budget guardrail; it is a
  debug surface only. Unknown models price at 0.0 rather than guessing.
- **Cost crosses the process boundary in the tool result.** Agents hold no DB handle; every
  tool returns `{output, status, cost_events[]}` and the orchestrator records them. The
  planner is metered too, as `orchestrator/planner` — which is why
  `orchestrations.total_cost_usd` sums `cost_events` rather than rolling up `runs.cost_usd`.
- **Schema failures and tool errors feed back to the planner as an observation**, not an
  exception — the action space is every agent, so bad picks are a re-plan, not a crash.
  Same for an unreachable department: partial action space, not fatal.

### Agent contract

Subclass `core.agent.Agent`, expose with `core.agent.register`. Stateless; agents never call
each other. Every tool shares one signature —
`(orchestration_id, payload) -> {output, status, cost_events}` — so the real input schema is
published through MCP `meta.payload_schema`. `execute()` gets a `Spender` that wraps
`llm_call` and accumulates cost events; raising becomes `status="error"`.

Policy (ICP, score bands, negative signals, churn bands, signal recency and phrase caps) is
rendered in from the playbook so it cannot drift from what the planner is told; missing keys
raise deliberately. `clay_enrich` reads its block *before* the vendor call, so a missing key
costs no Clay credits.
Output-contract values (queue names, segments) stay in the agent's schema.

**Policy keys stay top-level, never per-flow.** These renderers run in the department's own
process, where the current flow is unknowable — LangGraph state does not cross the MCP
boundary. A flow needing different bands declares a *new* top-level key
(`churn_risk_bands` beside `thresholds`) rather than shadowing an existing one. Note the two
run in opposite directions: 70 in `thresholds` earns a rep because the lead is good, 70 in
`churn_risk_bands` earns one because the account is in trouble.

Four agents are stubs with no integration behind them — `run_ad_campaigns`,
`churn_detection`, `engagement_tracking`, `send_outreach`. Each stamps its own provenance in
Python (`source: "simulated"`, `source: "inferred"`, `dry_run: True`) rather than asking the
model for it, because two hops downstream nothing remembers the output was invented.
`send_outreach` sends nothing and has no flag that would let it; a test asserts the module
acquires no transport.

### Guardrails and store

The halt table is `Orchestrator._guardrail_status`, applied inside the `invoke` node — a
status assigned in an edge function would not survive into the graph's final state. The
ceilings come from `State`, not `self`, resolved once per run: **flow override > instance
attribute > the playbook's global `guardrails:` block**. A mid-run flow switch does not
re-resolve them, or a planner could buy itself headroom by changing a label.

SQLite, WAL mandatory (orchestrator writes while API reads). One connection per request via
`Depends`, since FastAPI runs sync endpoints in a threadpool and connections cannot cross
threads. All SQL lives in `store/queries.py`. `decisions` logs the planner's rationale per
step, required because the path varies per run.

`CREATE TABLE IF NOT EXISTS` cannot add a column to a table that already exists, and a live
`nexusgtm.db` is checked in — so new columns go in `schema.sql` *and* in `db._ADDED_COLUMNS`,
which `init_db` applies idempotently. Every entry there must stay nullable: adding a nullable
column is the one schema change SQLite makes without rebuilding the table.

Strict structured output cannot express a free-form object, so the planner's `input_payload`
travels as a JSON string and is parsed in `planner.decide()`.
