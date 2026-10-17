"""The webhook, the claim gate, and the reconciler.

These cover the part of the design that is not an agent: a call outlives the step
that placed it, so something outside the graph has to notice it ended and resume
the run. The test that matters most is the redelivery one -- a vendor retrying a
webhook must not resume the same orchestration twice.

No telephony account and no planner: `api.voice.Orchestrator` is patched, so a
resume is observable as a recorded call rather than a real graph invocation.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from store import db, queries


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient on a scratch database, with the reconciler loop disabled.

    The loop is silenced because it would otherwise fire on its own schedule
    mid-test; `reconcile_once` is exercised directly instead.
    """
    monkeypatch.setenv("NEXUSGTM_DB", str(tmp_path / "voice.db"))
    monkeypatch.delenv("VOICE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("VOICE_ENABLED", raising=False)

    import api.main
    import api.voice

    async def never() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(api.voice, "reconcile_loop", never)

    with TestClient(api.main.app) as c:
        yield c


@pytest.fixture
def resumes(monkeypatch):
    """Capture resumes, synchronously, at the moment the claim is won.

    In production the webhook schedules the resume as a detached task and answers
    200 without waiting -- deliberately, because a webhook that waits for a
    planner is one the vendor retries. That makes the resume observable only
    *after* the response, and a task outliving its test lands in the next test's
    list.

    So the seam under test here is the claim gate rather than the task: whoever
    wins the claim is exactly who resumes, and winning is synchronous. The real
    `_claim_and_resume` is replaced outright, which also keeps a scratch-database
    test from ever starting a graph.
    """
    import api.voice

    captured: list[tuple[str, dict]] = []
    real_claim = queries.claim_voice_followup

    def claim_and_capture(conn, call_id, orchestration_id, seed):
        if not orchestration_id:
            return False
        if not real_claim(conn, call_id):
            return False
        captured.append((orchestration_id, seed))
        return True

    monkeypatch.setattr(api.voice, "_claim_and_resume", claim_and_capture)
    return captured


def _orchestration(tmp_path_db: str) -> str:
    conn = db.connect(tmp_path_db)
    try:
        return queries.create_orchestration(conn, "LEAD-1", flow="inbound_lead_qualification")
    finally:
        conn.close()


def _report(call_id: str, orchestration_id: str, **over) -> dict:
    call = {
        "id": call_id,
        "endedReason": over.pop("endedReason", "customer-ended-call"),
        "durationSeconds": over.pop("durationSeconds", 180),
        "cost": over.pop("cost", 0.22),
        "transcript": "assistant: hello\nuser: sure",
        "assistantOverrides": {
            "metadata": {
                "orchestration_id": orchestration_id,
                "crm_reference_id": "LEAD-1",
            }
        },
    }
    call.update(over)
    return {"message": {"type": "end-of-call-report", "call": call}}


# -------------------------------------------------------------------- signatures


def test_unsigned_webhook_is_rejected_and_writes_nothing(client, monkeypatch, tmp_path):
    """A public endpoint that resumes orchestrations verifies before it writes."""
    monkeypatch.setenv("VOICE_WEBHOOK_SECRET", "s3cret")
    oid = _orchestration(str(tmp_path / "voice.db"))

    response = client.post("/voice/webhook", json=_report("c1", oid))
    assert response.status_code == 401

    conn = db.connect(str(tmp_path / "voice.db"))
    try:
        assert queries.get_voice_call(conn, "c1") is None
    finally:
        conn.close()


def test_correctly_signed_webhook_is_accepted(client, resumes, monkeypatch, tmp_path):
    secret = "s3cret"
    monkeypatch.setenv("VOICE_WEBHOOK_SECRET", secret)
    oid = _orchestration(str(tmp_path / "voice.db"))

    body = json.dumps(_report("c1", oid)).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    response = client.post(
        "/voice/webhook",
        content=body,
        headers={"content-type": "application/json", "x-voice-signature": signature},
    )
    assert response.status_code == 200
    assert response.json()["resumed"] is True


def test_a_verbatim_shared_secret_is_accepted(client, resumes, monkeypatch, tmp_path):
    """Vapi echoes the secret back as-is; it does not sign the body.

    Its own support could not confirm a signature scheme, and the documented
    mechanism is a shared secret in `x-vapi-secret` (or a bearer credential). An
    HMAC-only check would 401 every real webhook from it.
    """
    monkeypatch.setenv("VOICE_WEBHOOK_SECRET", "s3cret")
    oid = _orchestration(str(tmp_path / "voice.db"))

    response = client.post(
        "/voice/webhook",
        json=_report("c1", oid),
        headers={"x-vapi-secret": "s3cret"},
    )
    assert response.status_code == 200
    assert response.json()["resumed"] is True


def test_a_bearer_credential_is_accepted(client, resumes, monkeypatch, tmp_path):
    """The current Vapi default: Authorization, with the Bearer prefix on."""
    monkeypatch.setenv("VOICE_WEBHOOK_SECRET", "s3cret")
    oid = _orchestration(str(tmp_path / "voice.db"))

    response = client.post(
        "/voice/webhook",
        json=_report("c1", oid),
        headers={"authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200


def test_a_wrong_shared_secret_is_rejected(client, monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_WEBHOOK_SECRET", "s3cret")
    oid = _orchestration(str(tmp_path / "voice.db"))

    response = client.post(
        "/voice/webhook",
        json=_report("c1", oid),
        headers={"x-vapi-secret": "not-the-secret"},
    )
    assert response.status_code == 401


def test_no_secret_accepts_anything(client, resumes, tmp_path):
    """The documented local-testing posture: unset means open, and warns.

    Worth a test rather than an accident -- `scripts/voice_replay.py` and a
    tunnel-exposed dev API both depend on it, and it is the one configuration
    where the endpoint authenticates nobody.
    """
    oid = _orchestration(str(tmp_path / "voice.db"))
    response = client.post("/voice/webhook", json=_report("c1", oid))
    assert response.status_code == 200
    assert response.json()["resumed"] is True


# ------------------------------------------------------------------ end of call


def test_end_of_call_records_the_row_and_resumes_once(client, resumes, tmp_path):
    oid = _orchestration(str(tmp_path / "voice.db"))

    response = client.post("/voice/webhook", json=_report("c1", oid))
    assert response.status_code == 200
    assert response.json() == {"ok": True, "call_id": "c1", "resumed": True}

    conn = db.connect(str(tmp_path / "voice.db"))
    try:
        row = queries.get_voice_call(conn, "c1")
    finally:
        conn.close()

    assert row["status"] == "completed"
    assert row["orchestration_id"] == oid
    assert row["duration_seconds"] == 180
    assert row["cost_usd"] == pytest.approx(0.22)
    assert row["followup_claimed_at"] is not None

    assert len(resumes) == 1
    assert resumes[0][0] == oid
    assert resumes[0][1]["call_record"]["status"] == "completed"


def test_redelivery_resumes_exactly_once(client, resumes, tmp_path):
    """The test that earns the claim gate.

    Vendors retry on a slow response, and a retry that resumed the run again
    would double every post-call step -- two crm_syncs, two planner decisions,
    one orchestration whose history makes no sense.
    """
    oid = _orchestration(str(tmp_path / "voice.db"))
    report = _report("c1", oid)

    first = client.post("/voice/webhook", json=report)
    second = client.post("/voice/webhook", json=report)

    assert first.json()["resumed"] is True
    assert second.json()["resumed"] is False
    assert len(resumes) == 1


def test_a_transferred_call_is_recorded_as_transferred(client, resumes, tmp_path):
    oid = _orchestration(str(tmp_path / "voice.db"))
    client.post(
        "/voice/webhook",
        json=_report("c1", oid, endedReason="assistant-forwarded-call"),
    )

    conn = db.connect(str(tmp_path / "voice.db"))
    try:
        assert queries.get_voice_call(conn, "c1")["status"] == "transferred"
    finally:
        conn.close()
    assert len(resumes) == 1


def test_a_status_update_records_the_call_without_resuming(client, resumes, tmp_path):
    """First word about a call: enough for the reconciler to find it later."""
    oid = _orchestration(str(tmp_path / "voice.db"))
    response = client.post(
        "/voice/webhook",
        json={
            "message": {
                "type": "status-update",
                "call": {
                    "id": "c1",
                    "assistantOverrides": {
                        "metadata": {"orchestration_id": oid, "crm_reference_id": "LEAD-1"}
                    },
                },
            }
        },
    )
    assert response.status_code == 200

    conn = db.connect(str(tmp_path / "voice.db"))
    try:
        assert queries.get_voice_call(conn, "c1")["status"] == "in_progress"
    finally:
        conn.close()
    assert resumes == []


def test_an_unknown_event_is_acknowledged_not_retried(client, resumes):
    """Anything but 2xx makes the vendor retry, and nothing here improves on retry."""
    response = client.post("/voice/webhook", json={"message": {"type": "speech-end"}})
    assert response.status_code == 200
    assert resumes == []


# ------------------------------------------------- the shape the vendor sends


def _real_report(call_id: str, orchestration_id: str | None) -> dict:
    """The payload Vapi actually posts, verified against a live call.

    Almost nothing sits on `message.call`: durationSeconds, cost, endedReason,
    recordingUrl, transcript and summary are all top-level on `message`, and the
    vendor's own docs elide the call object rather than say so. The older
    `_report` helper above puts them on `message.call`, which is exactly why the
    suite stayed green while a real 99-second call was recorded with no duration
    and no cost.
    """
    metadata = {"assigned_rep": "Jordan Kim"}
    if orchestration_id:
        metadata["orchestration_id"] = orchestration_id
        metadata["crm_reference_id"] = "Vercel"
    return {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "customer-ended-call",
            "durationSeconds": 99.405,
            "durationMs": 99405,
            "cost": 0.1033,
            "startedAt": "2026-10-17T21:09:14.969Z",
            "endedAt": "2026-10-17T21:10:54.374Z",
            "recordingUrl": "https://example.invalid/r.wav",
            "transcript": "AI: hello\nUser: hi",
            "summary": "A short conversation.",
            "call": {"id": call_id, "assistantOverrides": {"metadata": metadata}},
        }
    }


def test_the_real_payload_shape_populates_duration_and_cost(client, resumes, tmp_path):
    """The test that would have caught it.

    A 99-second call costing $0.1033 was stored as `duration_seconds: None,
    cost_usd: 0.0` because the fields were read from `message.call`.
    """
    oid = _orchestration(str(tmp_path / "voice.db"))
    response = client.post("/voice/webhook", json=_real_report("c-real", oid))
    assert response.status_code == 200

    conn = db.connect(str(tmp_path / "voice.db"))
    try:
        row = queries.get_voice_call(conn, "c-real")
    finally:
        conn.close()

    assert row["status"] == "completed"
    assert row["duration_seconds"] == 99
    assert row["cost_usd"] == pytest.approx(0.1033)
    assert row["recording_url"] == "https://example.invalid/r.wav"
    assert "User: hi" in row["transcript"]
    assert row["crm_reference_id"] == "Vercel"


def test_metadata_orchestration_id_drives_the_resume(client, resumes, tmp_path):
    """The id in the metadata is the only thing that says which run to resume."""
    oid = _orchestration(str(tmp_path / "voice.db"))
    client.post("/voice/webhook", json=_real_report("c-real", oid))

    assert len(resumes) == 1
    assert resumes[0][0] == oid


def test_without_an_orchestration_id_nothing_resumes(client, resumes, tmp_path):
    """A call placed before this fix carries no run id. It records, and stops.

    Not an error: the row is still written, so a reconciler that learns to adopt
    orphans later has something to find. It simply cannot be resumed from here.
    """
    _orchestration(str(tmp_path / "voice.db"))
    response = client.post("/voice/webhook", json=_real_report("c-orphan", None))

    assert response.status_code == 200
    assert response.json()["resumed"] is False
    assert resumes == []

    conn = db.connect(str(tmp_path / "voice.db"))
    try:
        assert queries.get_voice_call(conn, "c-orphan")["status"] == "completed"
    finally:
        conn.close()


def test_the_artifact_nesting_still_works(client, resumes, tmp_path):
    """The one nesting the docs do describe must keep contributing."""
    oid = _orchestration(str(tmp_path / "voice.db"))
    payload = _real_report("c-art", oid)
    del payload["message"]["transcript"]
    payload["message"]["artifact"] = {"transcript": "AI: from the artifact"}

    client.post("/voice/webhook", json=payload)

    conn = db.connect(str(tmp_path / "voice.db"))
    try:
        assert queries.get_voice_call(conn, "c-art")["transcript"] == "AI: from the artifact"
    finally:
        conn.close()

# ------------------------------------------------------------------- tool calls


def test_tool_call_answers_in_band_without_the_planner(client, monkeypatch):
    """A live caller is waiting: no LLM, no MCP, inside the blocking budget."""
    from core import llm as llm_module

    def boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("an in-call tool reached the planner")

    monkeypatch.setattr(llm_module, "llm_call", boom)

    response = client.post(
        "/voice/tool-call",
        json={
            "message": {
                "type": "tool-calls",
                "toolCalls": [
                    {
                        "id": "tc-1",
                        "function": {"name": "lookup_availability", "arguments": "{}"},
                    }
                ],
            }
        },
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["toolCallId"] == "tc-1"
    assert results[0]["result"]["ok"] is True
    assert results[0]["result"]["dry_run"] is True
    assert "slots" in results[0]["result"]


def test_an_unknown_tool_comes_back_as_something_to_say(client):
    """Never a 500: raising into a blocking webhook hangs up on the caller."""
    response = client.post(
        "/voice/tool-call",
        json={
            "message": {
                "type": "tool-calls",
                "toolCalls": [{"id": "tc-9", "function": {"name": "nope", "arguments": {}}}],
            }
        },
    )
    assert response.status_code == 200
    result = response.json()["results"][0]["result"]
    assert result["ok"] is False
    assert result["spoken"]


# ------------------------------------------------------------------- reconciler


def _stale(conn, call_id: str, oid: str, status: str) -> None:
    """A call placed long enough ago that the reconciler should pick it up."""
    queries.voice_call_placed(
        conn, call_id, orchestration_id=oid, crm_reference_id="LEAD-1", status=status
    )
    conn.execute(
        "UPDATE voice_calls SET placed_at = '2000-01-01T00:00:00+00:00' WHERE call_id = ?",
        (call_id,),
    )
    conn.commit()


def test_reconciler_resumes_a_call_the_vendor_lost(client, resumes, tmp_path):
    """The guarantee: a call with no retrievable outcome still reaches the planner.

    Vendors are documented to skip the end-of-call report on transferred calls,
    and deliveries get dropped. Without this sweep the CRM write-back would
    silently never happen, and nothing would be watching.
    """
    import api.voice

    path = str(tmp_path / "voice.db")
    oid = _orchestration(path)
    conn = db.connect(path)
    try:
        _stale(conn, "c-lost", oid, "dialing")
        count = asyncio.run(api.voice.reconcile_once(conn))
        row = queries.get_voice_call(conn, "c-lost")
    finally:
        conn.close()

    assert count == 1
    assert row["status"] == "unknown"
    assert row["ended_reason"] == "not-found"
    assert row["reconciled_at"] is not None
    assert len(resumes) == 1


def test_reconciler_resumes_a_terminal_call_that_was_never_claimed(
    client, resumes, tmp_path
):
    """The process died between finalizing a call and resuming its run."""
    import api.voice

    path = str(tmp_path / "voice.db")
    oid = _orchestration(path)
    conn = db.connect(path)
    try:
        _stale(conn, "c-orphan", oid, "dialing")
        queries.voice_call_finalize(
            conn,
            "c-orphan",
            status="completed",
            orchestration_id=oid,
            duration_seconds=60,
            cost_usd=0.08,
        )
        count = asyncio.run(api.voice.reconcile_once(conn))
    finally:
        conn.close()

    assert count == 1
    # Reused the stored record rather than paying for another vendor fetch.
    assert resumes[0][1]["call_record"]["status"] == "completed"
    assert resumes[0][1]["call_record"]["cost_usd"] == pytest.approx(0.08)


def test_reconciler_leaves_a_claimed_call_alone(client, resumes, tmp_path):
    import api.voice

    path = str(tmp_path / "voice.db")
    oid = _orchestration(path)
    conn = db.connect(path)
    try:
        _stale(conn, "c-done", oid, "completed")
        assert queries.claim_voice_followup(conn, "c-done") is True
        count = asyncio.run(api.voice.reconcile_once(conn))
    finally:
        conn.close()

    assert count == 0
    assert resumes == []


def test_reconciler_ignores_a_recent_call(client, resumes, tmp_path):
    """A call still in progress is not stale; the window comes from call_policy."""
    import api.voice

    path = str(tmp_path / "voice.db")
    oid = _orchestration(path)
    conn = db.connect(path)
    try:
        queries.voice_call_placed(
            conn, "c-fresh", orchestration_id=oid, crm_reference_id="LEAD-1"
        )
        count = asyncio.run(api.voice.reconcile_once(conn))
    finally:
        conn.close()

    assert count == 0
    assert resumes == []
