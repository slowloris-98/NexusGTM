"""Tools the voice agent calls *during* a conversation. Not agents, deliberately.

The vendor's in-call tool webhook blocks the conversation while it waits, with a
hard timeout measured in single-digit seconds. A planner call alone costs several
of those, and a planner plus an MCP round trip costs more than the whole budget --
so these cannot be registered agents, and nothing here may consult the planner.

They are plain functions, dispatched by name, fast by construction. That is the
entire design constraint.

The split is worth stating because it is easy to get backwards: the *planner*
decides what the system does about an account, before and after the call. These
decide what the agent can do inside a sentence. A meeting booked here is recorded
by the planner afterwards, through crm_sync, like every other outcome.

Every tool is a dry run in this slice. Provenance is stamped in Python rather
than asked of the model, the same way the four stub agents do it: two hops
downstream nothing remembers the booking was invented, and a model asked to
self-report `dry_run` will eventually say False.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

log = logging.getLogger(__name__)


def _ok(tool: str, **fields: Any) -> dict:
    """A successful tool result, stamped as simulated in Python.

    `spoken` is what the agent should say next. It is returned rather than left
    to the model because the caller is waiting in real time: a tool that answers
    with raw fields makes the agent pause to compose a sentence from them.
    """
    return {"tool": tool, "ok": True, "dry_run": True, "source": "simulated", **fields}


def _refused(tool: str, spoken: str, reason: str) -> dict:
    """A tool that could not do the thing, phrased for the caller to hear.

    Never an exception. Raising into the vendor's blocking webhook abandons the
    conversation mid-sentence, which the caller experiences as a hang-up; the
    agent should apologise and carry on instead.
    """
    return {
        "tool": tool,
        "ok": False,
        "dry_run": True,
        "source": "simulated",
        "spoken": spoken,
        "reason": reason,
    }


def _slots(count: int = 3) -> list[str]:
    """The next few business-hour slots, as ISO timestamps.

    Computed rather than asked of a model: a calendar offer the caller agrees to
    becomes a commitment, and a plausible-looking invented time is the one thing
    this must never produce.
    """
    cursor = datetime.now(timezone.utc) + timedelta(days=1)
    cursor = cursor.replace(hour=15, minute=0, second=0, microsecond=0)
    out: list[str] = []
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def _spoken_slot(iso: str) -> str:
    """"Thursday at 3pm" -- %-I is not portable, so the hour is formatted by hand."""
    when = datetime.fromisoformat(iso)
    hour = when.hour % 12 or 12
    return f"{when.strftime('%A')} at {hour}{'am' if when.hour < 12 else 'pm'}"


def lookup_availability(args: dict) -> dict:
    """Offer two or three concrete slots, never an open-ended question."""
    slots = _slots(3)
    offered = " or ".join(_spoken_slot(s) for s in slots[:2])
    return _ok(
        "lookup_availability",
        slots=slots,
        rep=args.get("assigned_rep"),
        spoken=f"I have {offered} — would either of those work?",
    )


def book_meeting(args: dict) -> dict:
    """Book one of the offered slots. The most concrete thing a sales call does."""
    slot = str(args.get("slot") or "").strip()
    if not slot:
        return _refused(
            "book_meeting",
            "Let me check what times we have and come back to you.",
            "no slot supplied; call lookup_availability first",
        )
    attendee = str(args.get("email") or "").strip()
    if not attendee:
        return _refused(
            "book_meeting",
            "What is the best email address to send the invite to?",
            "no attendee email on the lead or in the call",
        )
    return _ok(
        "book_meeting",
        slot=slot,
        attendee=attendee,
        rep=args.get("assigned_rep"),
        duration_minutes=int(args.get("duration_minutes") or 30),
        spoken="Booked — you'll get a calendar invite in a moment.",
    )


def send_sms(args: dict) -> dict:
    """Text a confirmation or a link while the caller is still on the line."""
    to = str(args.get("to") or "").strip()
    if not to:
        return _refused(
            "send_sms",
            "What is the best number for that?",
            "no recipient number",
        )
    return _ok(
        "send_sms",
        to=to,
        body=str(args.get("body") or "")[:320],
        spoken="Sent — it should arrive shortly.",
    )


def send_email(args: dict) -> dict:
    """Email a follow-up. The fallback when the caller says now is a bad time."""
    to = str(args.get("to") or "").strip()
    if not to:
        return _refused(
            "send_email",
            "What is the best email address for that?",
            "no recipient address",
        )
    return _ok(
        "send_email",
        to=to,
        subject=str(args.get("subject") or "")[:200],
        body=str(args.get("body") or "")[:2000],
        spoken="Sent that over — you should see it in your inbox.",
    )


TOOLS: dict[str, Callable[[dict], dict]] = {
    "lookup_availability": lookup_availability,
    "book_meeting": book_meeting,
    "send_sms": send_sms,
    "send_email": send_email,
}


def dispatch(name: str, args: dict) -> dict:
    """Run one in-call tool by name. Never raises.

    An unknown name is the vendor calling a tool we have retired or never had,
    and the conversation is still waiting -- so it comes back as something the
    agent can say, like every other failure here.
    """
    tool = TOOLS.get(name)
    if tool is None:
        log.warning("voice agent called unknown tool %r", name)
        return _refused(
            name or "unknown",
            "I can't do that from here, but I can have someone follow up.",
            f"unknown tool {name!r}; known: {', '.join(sorted(TOOLS))}",
        )
    try:
        return tool(args or {})
    except Exception as exc:  # a live caller is waiting; never raise at them
        log.exception("in-call tool %r failed", name)
        return _refused(
            name,
            "Something went wrong on my end — let me have someone follow up.",
            f"{type(exc).__name__}: {exc}",
        )
