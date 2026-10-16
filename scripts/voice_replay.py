"""Drive the voice webhook with a fabricated end-of-call report. No vendor needed.

What this is for: watching a real planner take a parked run through its post-call
steps, in the console, without a telephony account. The tests prove the webhook
and the claim gate in isolation; this is how you see the whole seam work --
`voice_call_outcome` reading the call back, then `crm_sync` writing it to the CRM,
appended to the orchestration that placed the call.

    # 1. department servers and the API running, then place a call:
    python scripts/voice_replay.py --list            # parked runs awaiting a call
    python scripts/voice_replay.py <orchestration_id>
    python scripts/voice_replay.py <orchestration_id> --ended-reason customer-did-not-answer

With VOICE_WEBHOOK_SECRET set it signs the payload the way the vendor would, so
the signature path is exercised too. Without it, the endpoint accepts the request
and logs a warning -- which is the behaviour to notice, not to ignore.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv

from store import db, queries

load_dotenv()

API = os.getenv("NEXUSGTM_API", "http://127.0.0.1:8000").rstrip("/")

TRANSCRIPT = [
    {"role": "assistant", "message": "Hi Dana, it's Alex from NexusGTM -- is now a good time?"},
    {"role": "user", "message": "Sure, I have a couple of minutes."},
    {"role": "assistant", "message": "You raised a Series B in March -- are you investing in GTM tooling?"},
    {"role": "user", "message": "We are, actually. Can you send some times?"},
    {"role": "assistant", "message": "I have Thursday at 3pm or Friday at 3pm -- would either work?"},
    {"role": "user", "message": "Thursday works."},
    {"role": "assistant", "message": "Booked. You'll get an invite shortly."},
]


def parked_runs() -> list[dict]:
    """Orchestrations whose blackboard holds a call with no outcome yet."""
    conn = db.connect()
    try:
        out = []
        for row in queries.list_orchestrations(conn, 200):
            final = row.get("final_result") or {}
            if isinstance(final, dict) and final.get("voice_call"):
                if not final.get("voice_call_outcome"):
                    out.append(
                        {
                            "id": row["id"],
                            "crm_reference_id": row["crm_reference_id"],
                            "status": row["status"],
                            "call_id": final["voice_call"].get("call_id"),
                        }
                    )
        return out
    finally:
        conn.close()


def report(orchestration_id: str, call_id: str, crm_reference_id: str, args) -> dict:
    """The payload shape the vendor posts when a call ends."""
    return {
        "message": {
            "type": "end-of-call-report",
            "call": {
                "id": call_id,
                "endedReason": args.ended_reason,
                "durationSeconds": args.duration,
                # Deliberately zero by default: the vendor's cost field is
                # documented as stale and often arrives empty, so this exercises
                # the minutes-times-rate fallback rather than the happy path.
                "cost": args.cost,
                "recordingUrl": f"https://example.invalid/recordings/{call_id}.wav",
                "transcript": [] if args.no_answer else TRANSCRIPT,
                "summary": (
                    "" if args.no_answer else "Agreed to meet Thursday at 3pm."
                ),
                "assistantOverrides": {
                    "metadata": {
                        "orchestration_id": orchestration_id,
                        "crm_reference_id": crm_reference_id,
                    }
                },
            },
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("orchestration_id", nargs="?", help="the run that placed a call")
    parser.add_argument("--list", action="store_true", help="show parked runs and exit")
    parser.add_argument("--call-id", help="override the call id from the blackboard")
    parser.add_argument("--ended-reason", default="customer-ended-call")
    parser.add_argument("--duration", type=int, default=214)
    parser.add_argument(
        "--cost",
        type=float,
        default=0.0,
        help="vendor cost. 0 exercises the per-minute fallback (the realistic case).",
    )
    parser.add_argument(
        "--no-answer",
        action="store_true",
        help="an empty transcript -- the call that never connected.",
    )
    args = parser.parse_args()

    if args.list or not args.orchestration_id:
        rows = parked_runs()
        if not rows:
            print("No parked runs. Launch one that calls voice_outreach first.")
            return 1
        print(f"{'orchestration':38}  {'account':16}  {'status':10}  call")
        for row in rows:
            print(
                f"{row['id']:38}  {row['crm_reference_id']:16}  "
                f"{row['status']:10}  {row['call_id']}"
            )
        return 0

    conn = db.connect()
    try:
        stored = queries.get_orchestration(conn, args.orchestration_id)
    finally:
        conn.close()
    if stored is None:
        print(f"No such orchestration: {args.orchestration_id}", file=sys.stderr)
        return 1

    final = stored.get("final_result") or {}
    call_id = (
        args.call_id
        or (final.get("voice_call") or {}).get("call_id")
        or f"replay-{uuid.uuid4().hex[:12]}"
    )

    if args.no_answer and args.ended_reason == "customer-ended-call":
        args.ended_reason = "customer-did-not-answer"

    payload = report(
        args.orchestration_id, call_id, stored["crm_reference_id"], args
    )
    body = json.dumps(payload).encode()

    headers = {"content-type": "application/json"}
    secret = os.getenv("VOICE_WEBHOOK_SECRET", "").strip()
    if secret:
        headers["x-voice-signature"] = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
    else:
        print("VOICE_WEBHOOK_SECRET unset -- the endpoint will accept this unsigned.")

    print(f"POST {API}/voice/webhook  call={call_id}  reason={args.ended_reason}")
    try:
        response = httpx.post(
            f"{API}/voice/webhook", content=body, headers=headers, timeout=30.0
        )
    except httpx.HTTPError as exc:
        print(f"Could not reach the API at {API}: {exc}", file=sys.stderr)
        return 1

    print(f"-> {response.status_code} {response.text}")
    if response.status_code == 200 and response.json().get("resumed"):
        print(
            "\nResumed. The post-call steps run in the background -- watch them "
            f"appear on\n  {API}/orchestrations/{args.orchestration_id}"
        )
    elif response.status_code == 200:
        print(
            "\nNot resumed: this call was already claimed. That is the "
            "idempotency gate\nworking -- a redelivered report must not run the "
            "post-call steps twice."
        )
    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
