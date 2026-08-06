-- JSON-bearing columns are TEXT + json.dumps; SQLite has no JSON type.

CREATE TABLE IF NOT EXISTS orchestrations (
    id               TEXT PRIMARY KEY,
    crm_reference_id TEXT NOT NULL,
    status           TEXT NOT NULL,          -- running | completed | halted_budget
                                             -- halted_steps | halted_loop | no_agent | failed
    outcome          TEXT,                   -- qualified | disqualified | failed
    flow             TEXT,                   -- the playbook flow it started under
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    final_result     TEXT,                   -- JSON
    total_cost_usd   REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id               TEXT PRIMARY KEY,
    orchestration_id TEXT NOT NULL REFERENCES orchestrations(id),
    step_no          INTEGER NOT NULL,
    department       TEXT NOT NULL,
    agent            TEXT NOT NULL,
    status           TEXT NOT NULL,          -- ok | error | invalid_input
    input            TEXT,                   -- JSON
    output           TEXT,                   -- JSON
    cost_usd         REAL NOT NULL DEFAULT 0,
    started_at       TEXT NOT NULL,
    finished_at      TEXT
);

CREATE TABLE IF NOT EXISTS cost_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    orchestration_id TEXT NOT NULL REFERENCES orchestrations(id),
    run_id           TEXT REFERENCES runs(id),
    department       TEXT NOT NULL,
    agent            TEXT NOT NULL,
    amount_usd       REAL NOT NULL,
    timestamp        TEXT NOT NULL
);

-- The planner's routing log. Required because paths vary per run.
CREATE TABLE IF NOT EXISTS decisions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    orchestration_id      TEXT NOT NULL REFERENCES orchestrations(id),
    step_no               INTEGER NOT NULL,
    chosen_agent          TEXT,
    rationale             TEXT NOT NULL,
    candidates_considered TEXT,              -- JSON
    flow                  TEXT,              -- the flow in force at this step
    timestamp             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_orch        ON runs(orchestration_id);
CREATE INDEX IF NOT EXISTS idx_cost_orch        ON cost_events(orchestration_id);
CREATE INDEX IF NOT EXISTS idx_cost_agent       ON cost_events(department, agent);
CREATE INDEX IF NOT EXISTS idx_decisions_orch   ON decisions(orchestration_id, step_no);
CREATE INDEX IF NOT EXISTS idx_orch_started     ON orchestrations(started_at DESC);
