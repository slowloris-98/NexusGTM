from core.costmeter import CostMeter
from core.llm import price_of
from store import queries


def test_rollup_run_and_orchestration(conn):
    meter = CostMeter(conn)
    oid = queries.create_orchestration(conn, "ACME-1")

    run_a = queries.start_run(
        conn, oid, step_no=1, department="marketing", agent="enrichment", input={}
    )
    meter.record(oid, "marketing", "enrichment", 0.01, run_id=run_a)
    meter.record(oid, "marketing", "enrichment", 0.02, run_id=run_a)
    queries.finish_run(conn, run_a, status="ok", output={})

    run_b = queries.start_run(
        conn, oid, step_no=2, department="revops", agent="scoring", input={}
    )
    meter.record(oid, "revops", "scoring", 0.04, run_id=run_b)
    queries.finish_run(conn, run_b, status="ok", output={})

    runs = {r["agent"]: r for r in conn.execute("SELECT * FROM runs").fetchall()}
    assert runs["enrichment"]["cost_usd"] == 0.03
    assert runs["scoring"]["cost_usd"] == 0.04

    queries.finalize_orchestration(
        conn, oid, status="completed", outcome="qualified", final_result={}
    )
    row = conn.execute("SELECT * FROM orchestrations WHERE id = ?", (oid,)).fetchone()
    assert row["total_cost_usd"] == 0.07
    assert meter.total_for(oid) == 0.07


def test_orchestration_total_includes_unattached_planner_spend(conn):
    """The planner's own LLM cost belongs to no run, but is still real money.

    Rolling the orchestration total up from runs.cost_usd would drop it and
    under-report every orchestration.
    """
    meter = CostMeter(conn)
    oid = queries.create_orchestration(conn, "ACME-3")

    run = queries.start_run(
        conn, oid, step_no=1, department="marketing", agent="enrichment", input={}
    )
    meter.record(oid, "marketing", "enrichment", 0.05, run_id=run)
    queries.finish_run(conn, run, status="ok", output={})

    # Planner spend: attributed to the orchestration, attached to no run.
    meter.record(oid, "orchestrator", "planner", 0.02)

    queries.finalize_orchestration(
        conn, oid, status="completed", outcome="qualified", final_result={}
    )
    row = conn.execute("SELECT * FROM orchestrations WHERE id = ?", (oid,)).fetchone()

    assert row["total_cost_usd"] == 0.07
    # And the stored total matches what the budget guardrail enforces mid-run.
    assert row["total_cost_usd"] == meter.total_for(oid)


def test_record_events_from_tool_result(conn):
    """Cost crosses the process boundary in the tool payload, not the database."""
    meter = CostMeter(conn)
    oid = queries.create_orchestration(conn, "ACME-2")

    total = meter.record_events(
        oid,
        [
            {"department": "sales", "agent": "outreach_draft", "amount_usd": 0.005},
            {"department": "sales", "agent": "outreach_draft", "amount_usd": 0.003},
        ],
    )
    assert total == 0.008
    assert meter.total_for(oid) == 0.008


def test_price_of_uses_table_and_tolerates_unknown_model():
    # 1000 in / 1000 out at $1.25 / $10.00 per 1M
    assert price_of("gpt-5.1", 1000, 1000) == (1250 + 10000) / 1_000_000
    # An unknown model must not guess a price and must not crash the run.
    assert price_of("nonexistent-model", 1000, 1000) == 0.0
