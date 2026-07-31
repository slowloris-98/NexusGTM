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

    OAI(["OpenAI"])
    ORCH --> OAI
    M --> OAI
    S --> OAI
    R --> OAI
    DB --> API["API + UI"]
```

CRM is a future addition — nothing writes back to it yet.

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
