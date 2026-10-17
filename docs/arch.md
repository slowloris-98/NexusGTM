# NexusGTM — Architecture

## High-level design

```mermaid
flowchart LR
    subgraph CFG["/config"]
        D["departments.yaml"]
        P["playbook.yaml"]
        L["llm.yaml — price table"]
    end

    subgraph CORE["/core — never imports a department"]
        REG["registry"]
        PLAN["planner"]
        ORCH["orchestrator (LangGraph)"]
        CM["CostMeter"]
        LLM["llm_call — text + usage"]
    end

    subgraph DEPTS["/departments — one FastMCP server each"]
        M["Marketing :8101<br/>enrichment"]
        R["RevOps :8102<br/>scoring · routing"]
        S["Sales :8103<br/>outreach_draft"]
    end

    STORE[("SQLite WAL<br/>orchestrations · runs<br/>cost_events · decisions")]
    API["FastAPI :8000"]
    UI["React control plane :5173"]
    OAI(["OpenAI Responses API"])
    LS(["LangSmith — tracing only"])

    D --> REG
    P --> PLAN
    L --> LLM

    REG -->|"action space + input schemas"| PLAN
    PLAN --> ORCH
    ORCH -->|"MCP call_tool"| M
    ORCH --> R
    ORCH --> S
    M -.->|"output, status, cost_events"| ORCH
    R -.-> ORCH
    S -.-> ORCH

    ORCH --> CM
    CM --> STORE
    ORCH --> STORE

    PLAN --> LLM
    M --> LLM
    R --> LLM
    S --> LLM
    LLM --> OAI
    LLM -.-> LS

    STORE --> API
    API --> UI
    REG -->|"taxonomy"| API
```

Three properties the diagram is meant to make obvious:

- **`/core` has no arrow into `/departments`.** It works off the registry and MCP
  endpoints, so adding a department is a server plus a `departments.yaml` entry.
- **Cost returns on the dashed arrows.** Agents run in their own processes and hold no
  database handle, so `cost_events` ride back in the tool result.
- **LangSmith hangs off `llm_call` on a dashed edge.** Nothing reads back from it —
  cost is computed in-process from the usage the Responses API returns inline.

## The run loop

```mermaid
flowchart TD
    START(["raw lead"]) --> PLAN["plan — choose next agent,<br/>log decision + rationale"]
    PLAN -->|"agent chosen"| VAL{"payload valid for<br/>the agent's schema?"}
    VAL -->|"no"| OBS["feed the error back<br/>as an observation"]
    OBS --> PLAN
    VAL -->|"yes"| INV["invoke over MCP —<br/>record run + cost_events"]
    INV --> G{"guardrails"}
    G -->|"spend over budget"| HB(["halted_budget"])
    G -->|"step ceiling"| HS(["halted_steps"])
    G -->|"same agent, same input"| HL(["halted_loop"])
    G -->|"continue"| PLAN
    PLAN -->|"goal met / disqualified"| DONE(["completed"])
    PLAN -->|"nothing fits"| NA(["no_agent"])
```

The path is chosen per run, not fixed. An invalid payload is a re-plan rather than a
crash, because the planner's action space is every registered agent — it will sometimes
pick one the blackboard cannot yet satisfy.

## At a glance

```mermaid
---
config:
  look: handDrawn
  theme: neutral
---
flowchart TD
    DB[("db")]
    LEADS["Leads"]
    CRM["CRM"]
    ORCH["Orchestrator<br/>plan ⇄ invoke"]
    CFG["departments.yaml"]

    LEADS --> ORCH
    DB <--> ORCH
    ORCH <--> CRM
    CFG -.->|"registry: discover agents"| ORCH

    ORCH <-->|"MCP · output + cost_events"| M["Department:<br/>Marketing<br/>(MCP)"]
    ORCH <--> S["Department:<br/>Sales<br/>(MCP)"]
    ORCH <--> R["Department:<br/>RevOps<br/>(MCP)"]
    ORCH <--> CS["Department:<br/>Customer Success<br/>(MCP)"]

    OAI(["OpenAI"])
    ORCH --> OAI
    M --> OAI
    S --> OAI
    R --> OAI
    CS --> OAI
    TEL(["Telephony vendor"])
    S -->|"place call · read it back"| TEL
    DB --> API["API + UI"]
```

RevOps writes the verdict back through `crm_sync`, gated by `CRM_WRITE_ENABLED` — off by
default, so a seed pass reports the payload it *would* have sent rather than filling a real
portal with junk. Firmographics a model guessed are written to separate `*_inferred`
properties, never under the canonical names, so a guess can never be read back as a
retrieved fact by whoever looks at the record next.

Sales is the only department that talks to a telephony vendor, and only from
`departments/sales/clients.py` — `server.py` itself acquires no transport, which is what
keeps `send_outreach` unable to send.

## The graph as built

The run loop above is the intent; this is the actual LangGraph — two nodes, both
outbound edges conditional.

```mermaid
---
config:
  look: handDrawn
  theme: neutral
---
flowchart TD
    START(["raw lead"]) --> PLAN["plan<br/>choose next agent<br/>log decision + rationale"]
    PLAN -->|"unknown agent name"| PLAN
    PLAN -->|"agent chosen"| INV["invoke<br/>validate · MCP call<br/>record run + cost_events"]
    INV -->|"running"| PLAN
    PLAN --> DONE(["completed / no_agent"])
    INV --> HALT(["halted_budget<br/>halted_steps<br/>halted_loop<br/>failed"])
```

Guardrails are not a node. `_guardrail_status` runs inside `invoke`, because a status
assigned in an edge function would not survive into the graph's final state.

## A call does not fit the loop

Every agent above is one-shot: `invoke` calls it, it returns, the graph moves on. A phone
call is not — it lasts minutes and talks back. So `voice_outreach` does not model the call
as a function call at all. It dials, returns in about a second with a `call_id` and no
outcome, and the graph exits with the orchestration still marked `running`.

```mermaid
---
config:
  look: handDrawn
  theme: neutral
---
flowchart TD
    V["step 5 · voice_outreach<br/>dials, returns on 'queued'"] --> EXIT(["graph exits<br/>orchestration parked `running`"])
    EXIT -.->|"minutes, on the vendor's side"| ENDED{{"the call ends"}}
    ENDED -->|"POST /voice/webhook"| CLAIM["claim_voice_followup<br/>conditional UPDATE"]
    ENDED -->|"webhook never arrived"| SWEEP["reconciler sweep<br/>re-fetches, or finalizes `unknown`"]
    SWEEP --> CLAIM
    CLAIM -->|"won the claim"| RES["Orchestrator.resume(same id)"]
    CLAIM -->|"lost it"| NOOP(["do nothing"])
    RES --> O["step 6 · voice_call_outcome<br/>bills the minutes"]
    O --> C["step 7 · crm_sync"]
```

The run is re-entered on the original row, not forked into a second one: same
`orchestration_id`, same flow, `step_no` carrying on at 6. Cost events land under that one
id, so `orchestrations.total_cost_usd` stays additive and no parent/child rollup is needed.

Two things in that diagram are load-bearing. **The reconciler is not optional** — vendors
are documented to skip the end-of-call webhook on transferred calls, and deliveries get
dropped, so a sweep over unclaimed `voice_calls` rows is what makes "the outcome always
reaches the planner" true rather than likely; a call the vendor cannot account for is
finalized `unknown` and resumed anyway, because *we could not reach them* is still worth
recording. **The claim is a conditional UPDATE**, which is what lets the webhook and the
reconciler race freely: exactly one of them resumes, so a vendor retry cannot run the
post-call steps twice.

`_guardrail_status` still runs inside `invoke` on a resumed run, and `max_steps` is granted
relative to where the run parked rather than from zero. But `budget_usd` is `inf`: the
telephony cost arrives after the conversation already happened, so no ceiling could have
prevented it, and halting there would mean never recording the call that incurred it. The
spend is metered and reported, never enforced.

In-call tools — calendar, SMS, email — are not agents and never reach the planner. The
vendor's tool webhook blocks the conversation with a timeout of a few seconds, which a
planner call alone exceeds, so they are plain functions in
`departments/sales/live_tools.py`.
