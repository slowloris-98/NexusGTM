"""Every SQL statement in the system lives here. The API layer writes none of its own."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)


def _loads(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _row(r: sqlite3.Row, json_fields: tuple[str, ...] = ()) -> dict:
    d = dict(r)
    for f in json_fields:
        if f in d:
            d[f] = _loads(d[f])
    return d


# --------------------------------------------------------------------------- writes

def create_orchestration(conn: sqlite3.Connection, crm_reference_id: str) -> str:
    oid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO orchestrations (id, crm_reference_id, status, started_at) "
        "VALUES (?, ?, 'running', ?)",
        (oid, crm_reference_id, _now()),
    )
    conn.commit()
    return oid


def finalize_orchestration(
    conn: sqlite3.Connection,
    orchestration_id: str,
    *,
    status: str,
    outcome: str | None,
    final_result: Any,
) -> None:
    # Sums cost_events, not runs.cost_usd. The planner is an LLM call too, and its
    # spend is attributed to the orchestration rather than to any single agent's
    # run -- rolling up the runs would silently drop it and under-report every
    # orchestration. This also keeps the stored total equal to what the budget
    # guardrail enforces mid-run.
    conn.execute(
        "UPDATE orchestrations SET status = ?, outcome = ?, final_result = ?, "
        "finished_at = ?, total_cost_usd = "
        "(SELECT COALESCE(SUM(amount_usd), 0) FROM cost_events WHERE orchestration_id = ?) "
        "WHERE id = ?",
        (status, outcome, _dumps(final_result), _now(), orchestration_id, orchestration_id),
    )
    conn.commit()


def start_run(
    conn: sqlite3.Connection,
    orchestration_id: str,
    *,
    step_no: int,
    department: str,
    agent: str,
    input: Any,
) -> str:
    rid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO runs (id, orchestration_id, step_no, department, agent, status, "
        "input, started_at) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
        (rid, orchestration_id, step_no, department, agent, _dumps(input), _now()),
    )
    conn.commit()
    return rid


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    output: Any,
) -> None:
    """Close a run and roll its cost_events up into runs.cost_usd."""
    conn.execute(
        "UPDATE runs SET status = ?, output = ?, finished_at = ?, cost_usd = "
        "(SELECT COALESCE(SUM(amount_usd), 0) FROM cost_events WHERE run_id = ?) "
        "WHERE id = ?",
        (status, _dumps(output), _now(), run_id, run_id),
    )
    conn.commit()


def record_cost_event(
    conn: sqlite3.Connection,
    orchestration_id: str,
    *,
    department: str,
    agent: str,
    amount_usd: float,
    run_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO cost_events (orchestration_id, run_id, department, agent, "
        "amount_usd, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (orchestration_id, run_id, department, agent, amount_usd, _now()),
    )
    conn.commit()


def record_decision(
    conn: sqlite3.Connection,
    orchestration_id: str,
    *,
    step_no: int,
    chosen_agent: str | None,
    rationale: str,
    candidates_considered: Any,
) -> None:
    conn.execute(
        "INSERT INTO decisions (orchestration_id, step_no, chosen_agent, rationale, "
        "candidates_considered, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (
            orchestration_id,
            step_no,
            chosen_agent,
            rationale,
            _dumps(candidates_considered),
            _now(),
        ),
    )
    conn.commit()


# --------------------------------------------------------------------------- reads

def total_cost(conn: sqlite3.Connection, orchestration_id: str) -> float:
    r = conn.execute(
        "SELECT COALESCE(SUM(amount_usd), 0) AS t FROM cost_events WHERE orchestration_id = ?",
        (orchestration_id,),
    ).fetchone()
    return float(r["t"])


def list_orchestrations(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT o.*, (SELECT COUNT(*) FROM runs r WHERE r.orchestration_id = o.id) AS step_count, "
        "(SELECT r.agent FROM runs r WHERE r.orchestration_id = o.id "
        " ORDER BY r.step_no DESC LIMIT 1) AS current_stage "
        "FROM orchestrations o ORDER BY o.started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    result = [_row(r, ("final_result",)) for r in rows]

    # The ordered agent path per orchestration, so a reader can see the route the
    # planner actually took -- the control plane draws its agent graph from the
    # handoffs these paths imply, rather than from a declared topology that does
    # not exist (agents never call each other).
    #
    # One extra statement for the whole page rather than one per orchestration,
    # and ordering comes from ORDER BY step_no rather than from GROUP_CONCAT,
    # whose ordering is not guaranteed. The department travels with each step so
    # two departments exposing the same bare agent name stay distinguishable.
    ids = [row["id"] for row in result]
    paths: dict[str, list[dict]] = {}
    if ids:
        runs = conn.execute(
            "SELECT orchestration_id, department, agent, status FROM runs "
            f"WHERE orchestration_id IN ({','.join('?' * len(ids))}) "
            "ORDER BY orchestration_id, step_no",
            ids,
        ).fetchall()
        for r in runs:
            paths.setdefault(r["orchestration_id"], []).append(
                {"department": r["department"], "agent": r["agent"], "status": r["status"]}
            )
    for row in result:
        row["agent_path"] = paths.get(row["id"], [])

    return result


def get_orchestration(conn: sqlite3.Connection, orchestration_id: str) -> dict | None:
    o = conn.execute(
        "SELECT * FROM orchestrations WHERE id = ?", (orchestration_id,)
    ).fetchone()
    if o is None:
        return None

    runs = conn.execute(
        "SELECT * FROM runs WHERE orchestration_id = ? ORDER BY step_no", (orchestration_id,)
    ).fetchall()
    decisions = conn.execute(
        "SELECT * FROM decisions WHERE orchestration_id = ? ORDER BY step_no", (orchestration_id,)
    ).fetchall()

    return {
        **_row(o, ("final_result",)),
        "runs": [_row(r, ("input", "output")) for r in runs],
        "decisions": [_row(d, ("candidates_considered",)) for d in decisions],
    }


def cost_by_agent(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT department, agent, SUM(amount_usd) AS total_cost_usd, "
        "COUNT(DISTINCT orchestration_id) AS orchestrations, COUNT(*) AS calls "
        "FROM cost_events GROUP BY department, agent ORDER BY total_cost_usd DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def cost_by_department(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT department, SUM(amount_usd) AS total_cost_usd, COUNT(*) AS calls "
        "FROM cost_events GROUP BY department ORDER BY total_cost_usd DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def cost_per_outcome(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT COALESCE(outcome, 'unknown') AS outcome, COUNT(*) AS count, "
        "SUM(total_cost_usd) AS total_cost_usd, AVG(total_cost_usd) AS avg_cost_usd "
        "FROM orchestrations WHERE finished_at IS NOT NULL "
        "GROUP BY COALESCE(outcome, 'unknown')"
    ).fetchall()
    return [dict(r) for r in rows]


def cost_trend(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT DATE(started_at) AS day, COUNT(*) AS orchestrations, "
        "SUM(total_cost_usd) AS total_cost_usd "
        "FROM orchestrations GROUP BY DATE(started_at) ORDER BY day"
    ).fetchall()
    return [dict(r) for r in rows]


def search_runs(conn: sqlite3.Connection, q: str, limit: int = 50) -> list[dict]:
    """Plain-text search across past runs -- the knowledge base."""
    like = f"%{q}%"
    rows = conn.execute(
        "SELECT r.id, r.orchestration_id, r.department, r.agent, r.status, "
        "r.input, r.output, r.started_at, o.crm_reference_id, o.outcome "
        "FROM runs r JOIN orchestrations o ON o.id = r.orchestration_id "
        "WHERE r.input LIKE ? OR r.output LIKE ? "
        "ORDER BY r.started_at DESC LIMIT ?",
        (like, like, limit),
    ).fetchall()
    return [_row(r, ("input", "output")) for r in rows]
