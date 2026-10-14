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

-- A voice call is long-lived and bidirectional while an agent call is one-shot, so
-- the call outlives the step that placed it. This table is the join. The placing
-- agent writes nothing -- agents hold no DB handle -- so every row here is owned by
-- the voice webhook and the reconciler, and `call_id` (the vendor's own id) is the
-- idempotency key that keeps a redelivered webhook from resuming a run twice.
--
-- The reconciler is why this table exists rather than a column somewhere: a call
-- whose end-of-call webhook never arrives (a transfer, a dropped delivery) still
-- has to reach the planner, and a pending row older than call_policy's
-- reconcile_after_minutes is the only durable record that it is owed.
CREATE TABLE IF NOT EXISTS voice_calls (
    call_id              TEXT PRIMARY KEY,       -- the vendor's id; the idempotency key
    orchestration_id     TEXT REFERENCES orchestrations(id),
    crm_reference_id     TEXT NOT NULL,
    status               TEXT NOT NULL,          -- dialing | in_progress | completed
                                                 -- transferred | no_answer | failed | unknown
    ended_reason         TEXT,
    duration_seconds     INTEGER,
    cost_usd             REAL,
    recording_url        TEXT,
    transcript           TEXT,
    raw_report           TEXT,                   -- JSON, the vendor payload as received
    placed_at            TEXT NOT NULL,
    ended_at             TEXT,
    reconciled_at        TEXT,
    -- Set once, by whichever of the webhook or the reconciler claims the call
    -- first. The conditional UPDATE on this column is what makes resuming exactly-once.
    followup_claimed_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_orch        ON runs(orchestration_id);
CREATE INDEX IF NOT EXISTS idx_cost_orch        ON cost_events(orchestration_id);
CREATE INDEX IF NOT EXISTS idx_cost_agent       ON cost_events(department, agent);
CREATE INDEX IF NOT EXISTS idx_decisions_orch   ON decisions(orchestration_id, step_no);
CREATE INDEX IF NOT EXISTS idx_orch_started     ON orchestrations(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_voice_pending    ON voice_calls(status, placed_at);
CREATE INDEX IF NOT EXISTS idx_voice_orch       ON voice_calls(orchestration_id);
