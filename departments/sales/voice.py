"""Voice agents: place a call, and later read back what happened on it.

Two agents rather than one, because a phone call does not fit the agent contract.
Every agent here is one-shot and synchronous -- `execute` runs, returns, and the
orchestrator moves on -- while a conversation lasts minutes and talks back.

The resolution is to stop pretending the call is a function call:

    voice_outreach        dials and returns immediately, in about a second. The
                          graph then has nothing to do and exits, leaving the
                          orchestration parked in `running`.
    voice_call_outcome    runs later, on the *same* orchestration, once the call
                          has ended. It reads the authoritative record, bills the
                          minutes, and hands the planner something to write back.

What resumes the run between them is the voice webhook, or the reconciler when
the webhook never arrives -- see `api/voice.py`. Neither lives here, because an
agent holds no database handle and cannot be waiting for anything.

Call policy -- how long, what hours, what to say, when to hand off -- is rendered
in from the playbook rather than written into these prompts, so it cannot drift
from what the rest of the system is told. Missing keys raise deliberately.
"""

from __future__ import annotations

import json
import os
import uuid

from core.agent import Agent, Spender
from core.config import playbook
from core.llm import strict_schema

from departments.sales.clients import (
    VOICE_PER_MINUTE_USD,
    VendorError,
    VoiceClient,
    calls_enabled,
)

# Policy renderers. Missing keys raise rather than falling back to a literal: a
# default here would be the duplicated constant the playbook exists to remove,
# and `Agent.run` turns the raise into status="error", which is the right answer
# for misconfigured call policy -- better a re-plan than a call made under rules
# nobody declared.


def _policy() -> dict:
    return playbook()["call_policy"]


def render_identity() -> str:
    """Who the caller says they are, for any prompt that speaks to a prospect.

    Lives here rather than in `server.py` because both sales prompts need it and
    `server.py` already imports from this module. The failure it prevents is not
    hypothetical: asked to "name the company" with no company in scope, the model
    spoke the literal string "[Company Name]" to a prospect on a live call.

    Raises on a missing key like the other policy renderers -- an anonymous caller
    is worse than a re-planned run.
    """
    i = playbook()["identity"]
    company = i["company"]
    return (
        f"YOU WORK FOR: {company} -- {i['one_liner'].strip()}\n"
        f"This is the company you are reaching out ON BEHALF OF, and it is never "
        f"the prospect's own company.\n"
        f"Say the name {company!r} out loud in the opening line. Forbidding only "
        f"brackets was not enough: a live call opened 'on behalf of Jordan Lee at "
        f"our team', which is neither a placeholder nor the company's name. "
        f"'our team', 'our company', 'us' and every other stand-in are wrong -- "
        f"write {company!r}."
    )


def render_call_rules() -> str:
    """The whole of call policy as prose, for the call's system prompt."""
    p = _policy()
    return "\n".join(
        [
            f"CALL LENGTH: end the call within {p['max_duration_minutes']} minutes.",
            f"CALLING HOURS: {p['calling_hours'].strip()}",
            f"OPENING AND CONSENT: {p['consent_script'].strip()}",
            f"HANDING OFF TO A HUMAN: {p['escalation'].strip()}",
            f"NEVER: {p['disallowed'].strip()}",
        ]
    )


def max_duration_seconds() -> int:
    return int(_policy()["max_duration_minutes"]) * 60


def escalation_number() -> str:
    return str(_policy()["escalation_number"])


def reconcile_after_minutes() -> int:
    return int(_policy()["reconcile_after_minutes"])


def reconcile_interval_seconds() -> int:
    return int(_policy()["reconcile_interval_seconds"])


class VoiceOutreachAgent(Agent):
    department = "sales"
    name = "voice_outreach"
    description = (
        "Place a live outbound phone call to a qualified lead, with an AI agent "
        "that can answer questions, book a meeting on the rep's calendar, and "
        "hand off to a human. Requires an assigned rep, firmographics, and a "
        "phone number on the lead. Returns as soon as the call is DIALING -- it "
        "does not wait for the conversation, so its output carries a call_id and "
        "no outcome. The outcome arrives later as further steps on this same run, "
        "once the call has ended. Finish the run after this reports dialing; do "
        "not also queue an email for the same touch. Never call this for a "
        "disqualified lead, and never twice in one run."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "lead": {
                "type": "object",
                "description": "Must carry a phone number. Required.",
            },
            "firmographics": {"type": "object", "description": "Required."},
            "assigned_rep": {
                "type": "string",
                "description": "Routing output. Required -- the call is made on their behalf.",
            },
            "crm_reference_id": {
                "type": "string",
                "description": (
                    "The ACCOUNT reference shown in your prompt. Travels with the "
                    "call so the outcome can be written back to the right account."
                ),
            },
            "segment": {"type": "string"},
            "fit_score": {"type": "integer"},
            "churn_risk": {"type": "integer"},
            "reasons": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Scoring reasons -- what the call should open from.",
            },
            "buying_signals": {"type": "array", "items": {"type": "string"}},
            "draft": {
                "type": "object",
                "description": "An outreach_draft, when one exists. Its angle is reused.",
            },
        },
        "required": ["lead", "firmographics", "assigned_rep"],
    }

    output_schema = strict_schema(
        "voice_call_brief",
        {
            "first_message": {
                "type": "string",
                "description": "The agent's opening line, under 35 words.",
            },
            "objective": {
                "type": "string",
                "description": "What a good outcome of this call is, in one line.",
            },
            "talking_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific facts about this account to draw on.",
            },
            "likely_objections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "What this account is most likely to push back on.",
            },
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        lead = payload.get("lead") or {}
        if not payload.get("assigned_rep"):
            raise ValueError("payload.assigned_rep is required; route the lead first")
        if not payload.get("firmographics"):
            raise ValueError("payload.firmographics is required; enrich the lead first")

        # Resolved here rather than asked of the model, for the same reason
        # send_outreach resolves the recipient address in Python: a phone number
        # is the one field a call must never contain a plausible guess of, and a
        # model asked for one will invent a well-formed number.
        phone = str(lead.get("phone") or lead.get("phone_number") or "").strip()
        if not phone:
            raise ValueError(
                "lead carries no phone number; voice_outreach cannot dial. "
                "Queue an email touch with send_outreach instead."
            )

        brief = llm(
            json.dumps(payload, default=str),
            instructions=(
                "You are briefing an AI voice agent about to phone this account on "
                "behalf of the assigned rep. Write the opening line it will speak "
                "and the notes it will work from.\n\n"
                + render_identity()
                + "\n\n"
                f"The opening line must name the rep and say they are from "
                f"{playbook()['identity']['company']} -- the literal name, never "
                f"'our team' or any other stand-in -- then say why we are calling in "
                "one clause drawn from something specific about this account, and then "
                "ask whether now is a good time. Under 35 words, spoken English: it is "
                "read aloud, so no bullet points, no email phrasing, no 'I hope this "
                "finds you well', and nothing in brackets, because a placeholder here "
                "is spoken to the prospect verbatim.\n\n"
                "Talking points are facts about THIS account from the firmographics, "
                "scoring reasons and buying signals. Never invent one.\n\n"
                + render_call_rules()
            ),
            schema=self.output_schema,
        )
        composed = brief.parsed or {}

        system_prompt = "\n\n".join(
            [
                f"You are an AI voice assistant calling on behalf of {payload['assigned_rep']}.",
                # Also in the live call's own prompt, not just the opening line.
                # Without it only the first sentence knows who we are and the rest
                # of the conversation drifts -- the first real call opened as
                # "[Company Name]" and then described a different company entirely.
                render_identity(),
                f"OBJECTIVE: {composed.get('objective', '')}",
                "TALKING POINTS (facts about this account -- never invent others):\n"
                + "\n".join(f"- {t}" for t in composed.get("talking_points") or []),
                "LIKELY OBJECTIONS:\n"
                + "\n".join(f"- {o}" for o in composed.get("likely_objections") or []),
                render_call_rules(),
                f"TRANSFER NUMBER: {escalation_number()}",
                "TOOLS: use lookup_availability then book_meeting to put time on the "
                "calendar, send_sms or send_email to confirm. Offer two specific "
                "slots rather than asking when they are free.",
            ]
        )

        # What the end-of-call webhook has to work with, minutes from now. The
        # vendor echoes this back untouched, and `orchestration_id` is the only
        # thing that tells api/voice.py which run to resume -- without it the
        # call completes, the report arrives, and nothing happens.
        metadata = {
            "orchestration_id": llm.orchestration_id,
            "crm_reference_id": payload.get("crm_reference_id"),
            "assigned_rep": payload["assigned_rep"],
        }

        # Not enabled: no transport is constructed at all, so a test run or a seed
        # pass cannot dial. Mirrors crm_sync's CRM_WRITE_ENABLED gate, except that
        # a placed call cannot be deleted afterwards the way a junk record can --
        # which is why this one is checked before the client exists, not after.
        if not calls_enabled():
            return {
                "voice_call": {
                    "call_id": f"dry-{uuid.uuid4().hex[:16]}",
                    "status": "dialing",
                    "recipient": phone,
                    "owner": payload["assigned_rep"],
                    "first_message": composed.get("first_message", ""),
                    "objective": composed.get("objective", ""),
                    "dry_run": True,
                }
            }

        agent_id = os.getenv("VOICE_AGENT_ID", "").strip()
        if not agent_id:
            raise VendorError(
                "VOICE_ENABLED is on but VOICE_AGENT_ID is not set in this process."
            )

        placed = VoiceClient().create_call(
            phone_number=phone,
            agent_id=agent_id,
            system_prompt=system_prompt,
            first_message=composed.get("first_message", ""),
            metadata=metadata,
            max_duration_seconds=max_duration_seconds(),
            from_number_id=os.getenv("VOICE_PHONE_NUMBER_ID", "").strip() or None,
            # Both or neither: the vendor rejects a partial model override. Unset,
            # the assistant's own configured prompt and model stand and the
            # per-call briefing does not reach the call -- see create_call.
            model_provider=os.getenv("VOICE_MODEL_PROVIDER", "").strip() or None,
            model_name=os.getenv("VOICE_MODEL", "").strip() or None,
        )

        # No cost recorded here. The minutes are not known until the call ends,
        # and billing an estimate would put a number in the ledger that no
        # invoice will ever match. voice_call_outcome records the real one.
        return {
            "voice_call": {
                "call_id": placed["call_id"],
                "status": placed.get("status") or "dialing",
                "recipient": phone,
                "owner": payload["assigned_rep"],
                "first_message": composed.get("first_message", ""),
                "objective": composed.get("objective", ""),
                "dry_run": False,
            }
        }


class VoiceCallOutcomeAgent(Agent):
    department = "sales"
    name = "voice_call_outcome"
    description = (
        "Read back what happened on a voice call that has ALREADY ENDED, and bill "
        "its minutes to this run. Returns the disposition, a summary, whether a "
        "meeting was booked or a human was brought in, and the call's cost. It is "
        "the only source of a call's outcome, and it never places or retries one. "
        "Do NOT call this in the same run that placed the call. voice_outreach "
        "returns while the phone is still ringing -- status 'queued' or 'dialing' "
        "means the conversation has not happened yet, so finish the run instead. "
        "The run resumes on its own once the call ends, and this agent is the "
        "first step of that resumed half. Called too early it errors, because a "
        "call still in flight has no outcome to read. "
        "It does answer for a call that never connected -- a no-answer is a result "
        "worth recording against the account, not a failure."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "voice_call": {
                "type": "object",
                "description": "voice_outreach output, carrying call_id. Required.",
            },
            "call_id": {
                "type": "string",
                "description": "The call id, if not passing the whole voice_call object.",
            },
        },
        "required": [],
    }

    output_schema = strict_schema(
        "voice_call_outcome",
        {
            "disposition": {
                "type": "string",
                "description": (
                    "meeting_booked | interested | not_interested | callback_requested "
                    "| escalated_to_human | no_answer | unknown"
                ),
            },
            "summary": {
                "type": "string",
                "description": "What was said and agreed, in two or three sentences.",
            },
            "meeting_booked": {"type": "boolean"},
            "escalated": {"type": "boolean"},
            "next_step": {
                "type": "string",
                "description": "What the rep should do next, in one line.",
            },
            "objections_raised": {"type": "array", "items": {"type": "string"}},
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        call = payload.get("voice_call") or {}
        call_id = str(payload.get("call_id") or call.get("call_id") or "").strip()
        if not call_id:
            raise ValueError(
                "payload.voice_call.call_id is required; this agent reads back a call "
                "that has already been placed"
            )

        # A record supplied by the resuming webhook, which already fetched it.
        # Saves a second vendor round trip on the common path and is the only
        # source at all on the dry-run path, where no vendor knows this call.
        record = payload.get("call_record") or call.get("record")

        if record is None and not str(call_id).startswith("dry-") and calls_enabled():
            record = VoiceClient().get_call(call_id)

        if record is None:
            # The vendor has no record, or never had one. Still an answer: the
            # planner must be able to write *something* back, so this returns ok
            # with an unknown disposition rather than erroring and burning a retry
            # on a call that will never be found.
            return {
                "voice_call_outcome": {
                    "call_id": call_id,
                    "disposition": "unknown",
                    "summary": (
                        "No record of this call could be retrieved from the telephony "
                        "provider. The call may never have connected."
                    ),
                    "meeting_booked": False,
                    "escalated": False,
                    "next_step": "Have the rep attempt contact manually.",
                    "status": "unknown",
                    "duration_seconds": None,
                    "cost_usd": 0.0,
                    "source": "inferred",
                }
            }

        # The precondition every other agent here has: voice_outreach refuses
        # without a phone, crm_sync without firmographics, and this one must
        # refuse a call that has not finished. Asking the vendor mid-ring returns
        # no duration and no end reason, which reads as a failed call -- a run
        # once wrote "the call did not connect" about a call that was still
        # ringing and went on to last forty seconds.
        #
        # Agent.run turns this into status="error", which the planner sees as an
        # observation and answers by finishing the run. That is correct: the
        # webhook, or the reconciler, resumes it once the call actually ends.
        if record.get("status") == "in_progress":
            raise ValueError(
                f"call {call_id} is still in progress; its outcome is not knowable "
                "yet. Finish this run -- it resumes on its own when the call ends."
            )

        # Telephony spend, recorded exactly once, here. Gated on a non-zero
        # amount like Clay's credit billing: an unpriced vendor logs nothing
        # rather than a stream of zero-dollar events.
        #
        # Recorded, never enforced -- this lands after the conversation already
        # happened, so no ceiling could have prevented it. That is why voice is
        # exempt from the budget guardrail rather than metered against it.
        cost = float(record.get("cost_usd") or 0.0)
        if not cost and record.get("duration_seconds"):
            cost = (int(record["duration_seconds"]) / 60.0) * VOICE_PER_MINUTE_USD
        if cost:
            llm.record_external(cost)

        transcript = record.get("transcript") or ""
        read = llm(
            json.dumps(
                {
                    "status": record.get("status"),
                    "ended_reason": record.get("ended_reason"),
                    "duration_seconds": record.get("duration_seconds"),
                    "vendor_summary": record.get("summary"),
                    "transcript": transcript[:12000],
                },
                default=str,
            ),
            instructions=(
                "You are reading a finished sales call to record its outcome. Judge "
                "only from the transcript and the call's status -- never infer a "
                "commitment nobody made.\n\n"
                "Set meeting_booked true only if a specific time was agreed. Set "
                "escalated true only if the call was handed to a human. If the call "
                "never connected -- no answer, voicemail, busy -- the disposition is "
                "no_answer and the summary says so plainly; do not narrate a "
                "conversation that did not happen. next_step is for the rep who "
                "picks this up, in one line."
            ),
            schema=self.output_schema,
        )
        parsed = read.parsed or {}

        # Status, duration and cost are stamped from the vendor record, not from
        # the model. The model reads the conversation; it does not get to restate
        # how long the call was or what it cost.
        return {
            "voice_call_outcome": {
                **parsed,
                "call_id": call_id,
                "status": record.get("status"),
                "ended_reason": record.get("ended_reason"),
                "duration_seconds": record.get("duration_seconds"),
                "cost_usd": round(cost, 6),
                "recording_url": record.get("recording_url"),
                "source": "simulated" if str(call_id).startswith("dry-") else "retrieved",
            }
        }
