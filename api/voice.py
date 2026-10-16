"""Voice call plumbing: the in-call tool endpoint, the webhook, and the reconciler.

Three things live here, and the reason they are in the API rather than in a
department is the same for all three: they are reached by the vendor, not by the
planner.

    POST /voice/tool-call   the agent calling a tool mid-sentence. The vendor
                            blocks the conversation while it waits, with a hard
                            timeout of a few seconds -- so this dispatches to
                            plain functions and never consults the planner.
    POST /voice/webhook     the call ended. Records what happened and resumes the
                            orchestration that placed it.
    reconcile_once          the backstop. A call whose end-of-call webhook never
                            arrives -- a transfer, a dropped delivery -- is swept
                            up here and resumed anyway.

The reconciler is not optional. Without it "the outcome always reaches the
planner" would depend on a vendor webhook that is documented to go missing on
transferred calls, and a lost delivery would mean a call that silently never
reached the CRM. It is what makes the guarantee true rather than likely.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Iterator

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from core.orchestrator import Orchestrator
from store import db, queries

log = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

# Statuses from which nothing further is expected. Anything else is a call still
# in flight, or one that ended without us hearing about it properly.
TERMINAL = {"completed", "transferred", "no_answer", "failed", "unknown"}


def get_conn() -> Iterator[sqlite3.Connection]:
    """One connection per request, for the sync read endpoints only.

    The same reason api.main keeps this sync: FastAPI resolves a sync dependency
    in a threadpool worker, and a SQLite connection cannot cross from there to
    the event loop. The async handlers below open their own.
    """
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _verify(body: bytes, *candidates: str | None) -> None:
    """Reject an unsigned webhook before anything touches the database.

    This endpoint writes rows and starts orchestrations, on a control plane that
    has no other authentication -- so verification is the first statement in
    every handler, not a check somewhere in the middle. An unset secret is
    allowed, for the replay script, and says so loudly.
    """
    from departments.sales.clients import verify_signature

    if not os.getenv("VOICE_WEBHOOK_SECRET", "").strip():
        log.warning(
            "VOICE_WEBHOOK_SECRET is not set; accepting an unverified voice webhook. "
            "Set it before pointing a real vendor at this endpoint."
        )
        return
    # Several headers are offered because vendors disagree on which they send,
    # and Vapi's own differs between a legacy shared secret and a bearer
    # credential. Any one of them proving knowledge of the secret is enough.
    if not any(verify_signature(body, c) for c in candidates if c):
        raise HTTPException(401, "invalid webhook signature")


def _policy() -> tuple[int, int]:
    """(reconcile_after_minutes, reconcile_interval_seconds) from the playbook."""
    from departments.sales.voice import (
        reconcile_after_minutes,
        reconcile_interval_seconds,
    )

    return reconcile_after_minutes(), reconcile_interval_seconds()


# ------------------------------------------------------------------ in-call tools


@router.post("/tool-call")
async def tool_call(
    request: Request,
    x_voice_signature: str | None = Header(None),
    x_vapi_secret: str | None = Header(None),
    x_vapi_signature: str | None = Header(None),
    authorization: str | None = Header(None),
):
    """Run one in-call tool. Must answer within the vendor's blocking budget.

    No planner, no MCP, no database write on the happy path. A live caller is
    waiting on the other end of this request, and the planner alone costs more
    than the whole timeout.
    """
    body = await request.body()
    _verify(body, x_voice_signature, x_vapi_secret, x_vapi_signature, authorization)

    from departments.sales.live_tools import dispatch

    payload = await request.json()
    message = payload.get("message") or payload
    calls = message.get("toolCalls") or message.get("tool_calls") or []

    results = []
    for call in calls:
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("name") or ""
        args = fn.get("arguments") or call.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        outcome = dispatch(name, args)
        results.append(
            {"toolCallId": call.get("id") or call.get("toolCallId"), "result": outcome}
        )

    return {"results": results}


# ----------------------------------------------------------------------- webhook


async def _resume_detached(orchestration_id: str, seed: dict) -> None:
    """Resume an orchestration on its own connection, outside the request.

    Fire-and-forget: the vendor gets its 200 immediately, because a webhook that
    waits for a planner is a webhook the vendor retries -- and a retry past the
    claim gate is a second resume of the same run.
    """
    conn = db.connect()
    try:
        await Orchestrator(conn).resume(orchestration_id, seed=seed)
    except Exception:
        log.exception("resuming orchestration %s after a call failed", orchestration_id)
    finally:
        conn.close()


def _claim_and_resume(
    conn: sqlite3.Connection, call_id: str, orchestration_id: str | None, seed: dict
) -> bool:
    """Claim the call and schedule the resume. True when this caller won the claim.

    The claim is a conditional UPDATE, so the webhook and the reconciler can race
    freely: exactly one of them resumes, and the loser does nothing.
    """
    if not orchestration_id:
        log.warning("call %s has no orchestration to resume", call_id)
        return False
    if not queries.claim_voice_followup(conn, call_id):
        log.info("call %s was already claimed; not resuming again", call_id)
        return False
    asyncio.get_running_loop().create_task(
        _resume_detached(orchestration_id, seed)
    )
    return True


@router.post("/webhook")
async def webhook(
    request: Request,
    x_voice_signature: str | None = Header(None),
    x_vapi_secret: str | None = Header(None),
    x_vapi_signature: str | None = Header(None),
    authorization: str | None = Header(None),
):
    """Vendor call events. The end-of-call report is the one that matters.

    Always answers 200 on a verified request, even for an event it ignores: a
    non-2xx makes the vendor retry, and nothing here becomes more correct the
    second time.
    """
    body = await request.body()
    _verify(body, x_voice_signature, x_vapi_secret, x_vapi_signature, authorization)

    from departments.sales.clients import normalize_call

    payload = await request.json()
    message = payload.get("message") or payload
    event = message.get("type") or ""
    raw_call = message.get("call") or {}
    call_id = str(raw_call.get("id") or message.get("callId") or "").strip()

    if not call_id:
        log.warning("voice webhook %r carried no call id", event)
        return {"ok": True, "ignored": "no call id"}

    metadata = (
        (raw_call.get("assistantOverrides") or {}).get("metadata")
        or raw_call.get("metadata")
        or {}
    )
    orchestration_id = metadata.get("orchestration_id")
    crm_reference_id = metadata.get("crm_reference_id") or ""

    conn = db.connect()
    try:
        if event in ("status-update", "call.started", "speech-update"):
            # First word about this call. Recorded so the reconciler has something
            # to find if we never hear about it again.
            queries.voice_call_placed(
                conn,
                call_id,
                orchestration_id=orchestration_id,
                crm_reference_id=crm_reference_id,
                status="in_progress",
            )
            return {"ok": True, "recorded": event}

        if event not in ("end-of-call-report", "call.ended"):
            return {"ok": True, "ignored": event}

        record = normalize_call({**raw_call, **message.get("artifact", {})})
        if message.get("endedReason") and not record.get("ended_reason"):
            record = normalize_call({**raw_call, "endedReason": message["endedReason"]})

        # The row goes terminal before anything else happens, so a crash below
        # still leaves the reconciler a durable record to pick up.
        queries.voice_call_finalize(
            conn,
            call_id,
            status=record["status"],
            crm_reference_id=crm_reference_id,
            orchestration_id=orchestration_id,
            ended_reason=record.get("ended_reason"),
            duration_seconds=record.get("duration_seconds"),
            cost_usd=record.get("cost_usd"),
            recording_url=record.get("recording_url"),
            transcript=record.get("transcript"),
            raw_report=payload,
        )

        resumed = _claim_and_resume(
            conn,
            call_id,
            orchestration_id,
            {"voice_call": {"call_id": call_id}, "call_record": record},
        )
        return {"ok": True, "call_id": call_id, "resumed": resumed}
    finally:
        conn.close()


@router.get("/calls/{call_id}")
def get_call(call_id: str, conn: Conn):
    found = queries.get_voice_call(conn, call_id)
    if found is None:
        raise HTTPException(404, "no such call")
    return found


# -------------------------------------------------------------------- reconciler


async def reconcile_once(conn: sqlite3.Connection) -> int:
    """Sweep up calls whose outcome never arrived. Returns how many were resumed.

    Two kinds of row qualify, and the second is the subtle one: a call still
    marked in flight long after it was placed, and a call that reached a terminal
    state but was never claimed -- the process died between finalizing it and
    resuming. Both are owed a resume.

    A call the vendor has no record of is finalized `unknown` and resumed anyway.
    That is the whole point: the planner gets to write "we could not reach them"
    rather than nothing at all.
    """
    after_minutes, _ = _policy()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=after_minutes)
    ).isoformat()

    stale = queries.pending_voice_calls(conn, cutoff)
    if not stale:
        return 0

    from departments.sales.clients import VendorError, VoiceClient, calls_enabled

    resumed = 0
    client = None
    for row in stale:
        call_id = row["call_id"]
        record: dict[str, Any] | None = None

        if row["status"] in TERMINAL:
            # Already final; it just never got claimed. Rebuild what the resume
            # needs from the stored row rather than paying for another fetch.
            record = {
                "status": row["status"],
                "ended_reason": row.get("ended_reason"),
                "duration_seconds": row.get("duration_seconds"),
                "cost_usd": row.get("cost_usd"),
                "recording_url": row.get("recording_url"),
                "transcript": row.get("transcript"),
            }
        elif calls_enabled() and not call_id.startswith("dry-"):
            try:
                client = client or VoiceClient()
                record = client.get_call(call_id)
            except VendorError as exc:
                log.warning("reconciling %s: vendor fetch failed: %s", call_id, exc)
                continue

        if record is None:
            # The vendor lost it, or it was never real. Still an outcome.
            record = {
                "status": "unknown",
                "ended_reason": "not-found",
                "duration_seconds": None,
                "cost_usd": 0.0,
            }

        queries.voice_call_finalize(
            conn,
            call_id,
            status=record.get("status") or "unknown",
            crm_reference_id=row.get("crm_reference_id") or "",
            orchestration_id=row.get("orchestration_id"),
            ended_reason=record.get("ended_reason"),
            duration_seconds=record.get("duration_seconds"),
            cost_usd=record.get("cost_usd"),
            recording_url=record.get("recording_url"),
            transcript=record.get("transcript"),
            reconciled=True,
        )

        if _claim_and_resume(
            conn,
            call_id,
            row.get("orchestration_id"),
            {"voice_call": {"call_id": call_id}, "call_record": record},
        ):
            resumed += 1

    if resumed:
        log.info("reconciler resumed %d call(s)", resumed)
    return resumed


async def reconcile_loop() -> None:
    """Run the sweep forever, on its own connection.

    Started from the API's lifespan. With more than one API worker there will be
    one of these per worker, which the claim gate makes safe but wasteful --
    single-worker is the documented deployment.
    """
    _, interval = _policy()
    while True:
        try:
            await asyncio.sleep(interval)
            conn = db.connect()
            try:
                await reconcile_once(conn)
            finally:
                conn.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A reconciler that dies stops the one guarantee it exists to make,
            # so it logs and keeps going rather than exiting the loop.
            log.exception("voice reconciler sweep failed; continuing")
