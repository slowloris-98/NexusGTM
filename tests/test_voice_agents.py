"""The two voice agents, and the guarantees that keep them honest.

Nothing here needs a telephony account. Two seams make that work, and both are
the same ones the Clay and HubSpot tests use: `VOICE_ENABLED` off by default, and
an injectable `transport` so the real URLs and headers are still exercised.
"""

from __future__ import annotations

import inspect
import json

import httpx
import pytest

import departments.sales.server as sales_server
import departments.sales.voice as voice_module
from core import llm as llm_module
from core.config import playbook
from departments.sales.clients import VoiceClient, normalize_call
from departments.sales.voice import VoiceCallOutcomeAgent, VoiceOutreachAgent
from tests.conftest import result

LEAD = {"name": "Dana Reed", "email": "dana@acme.com", "phone": "+15551230000"}
FIRMO = {"domain": "acme.com", "company_name": "Acme", "source": "clay"}

BRIEF = {
    "first_message": "Hi Dana, it's Alex calling from NexusGTM about your Series B -- is now a good time?",
    "objective": "Book 20 minutes with the rep.",
    "talking_points": ["Raised a Series B in March"],
    "likely_objections": ["Already have a vendor"],
}

OUTCOME = {
    "disposition": "meeting_booked",
    "summary": "Agreed to meet Thursday.",
    "meeting_booked": True,
    "escalated": False,
    "next_step": "Send the invite.",
    "objections_raised": [],
}


@pytest.fixture(autouse=True)
def voice_off(monkeypatch):
    """Calls disabled unless a test says otherwise -- the shipped default."""
    monkeypatch.delenv("VOICE_ENABLED", raising=False)
    monkeypatch.delenv("VOICE_API_KEY", raising=False)
    monkeypatch.delenv("VOICE_AGENT_ID", raising=False)


@pytest.fixture
def parsed(monkeypatch):
    """Patch the single LLM seam and hand back the list of calls made."""

    def install(*payloads):
        calls: list[dict] = []
        queue = list(payloads)

        def fake(prompt, **kwargs):
            calls.append({"prompt": prompt, **kwargs})
            return result(parsed=queue.pop(0) if queue else {})

        monkeypatch.setattr(llm_module, "llm_call", fake)
        return calls

    return install


# ----------------------------------------------------------------- voice_outreach


def test_dry_run_places_no_call_and_bills_only_the_brief(parsed):
    """Off by default: a synthetic id, no transport, one cost event."""
    calls = parsed(BRIEF)
    envelope = VoiceOutreachAgent().run(
        {"lead": LEAD, "firmographics": FIRMO, "assigned_rep": "Alex Chen"}
    )

    assert envelope["status"] == "ok"
    call = envelope["output"]["voice_call"]
    assert call["dry_run"] is True
    assert call["call_id"].startswith("dry-")
    assert call["status"] == "dialing"
    assert call["recipient"] == LEAD["phone"]
    # Only the briefing call. A second event would mean something started billing.
    assert len(calls) == 1
    assert len(envelope["cost_events"]) == 1


def test_outreach_refuses_without_a_phone_number(parsed):
    """A call needs a number, and a model must never be asked to supply one."""
    parsed(BRIEF)
    envelope = VoiceOutreachAgent().run(
        {"lead": {"email": "d@acme.com"}, "firmographics": FIRMO, "assigned_rep": "Alex"}
    )
    assert envelope["status"] == "error"
    assert "phone" in envelope["output"]["error"].lower()


def test_outreach_requires_a_rep_and_firmographics(parsed):
    parsed(BRIEF, BRIEF)
    no_rep = VoiceOutreachAgent().run({"lead": LEAD, "firmographics": FIRMO})
    assert no_rep["status"] == "error"
    assert "assigned_rep" in no_rep["output"]["error"]

    no_firmo = VoiceOutreachAgent().run({"lead": LEAD, "assigned_rep": "Alex"})
    assert no_firmo["status"] == "error"
    assert "firmographics" in no_firmo["output"]["error"]


def test_call_policy_reaches_the_prompt(parsed):
    """Policy is rendered in, not written into the prompt -- so it cannot drift."""
    calls = parsed(BRIEF)
    VoiceOutreachAgent().run(
        {"lead": LEAD, "firmographics": FIRMO, "assigned_rep": "Alex Chen"}
    )
    instructions = calls[0]["instructions"]
    assert "12 minutes" in instructions          # max_duration_minutes
    assert "weekdays only" in instructions       # calling_hours
    assert "AI assistant" in instructions        # consent_script


def test_identity_reaches_the_briefing_and_the_live_call(parsed, monkeypatch):
    """The fix for a placeholder spoken aloud to a real prospect.

    The first live call opened "calling on behalf of Jordan Alvarez at Company
    Name" because nothing told the model who we work for. Both halves are asserted
    because fixing only the briefing leaves the opening line right and the rest of
    the conversation drifting -- which is what actually happened on that call.
    """
    calls = parsed(BRIEF)
    monkeypatch.setenv("VOICE_ENABLED", "true")
    monkeypatch.setenv("VOICE_API_KEY", "vk-test")
    monkeypatch.setenv("VOICE_AGENT_ID", "agent-1")

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": "call-abc", "status": "queued"})

    monkeypatch.setattr(
        voice_module,
        "VoiceClient",
        lambda: VoiceClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setenv("VOICE_MODEL_PROVIDER", "vapi")
    monkeypatch.setenv("VOICE_MODEL", "test-model")

    VoiceOutreachAgent().run(
        {"lead": LEAD, "firmographics": FIRMO, "assigned_rep": "Alex Chen"}
    )

    company = playbook()["identity"]["company"]
    assert company in calls[0]["instructions"]

    sent = json.loads(seen[0].content)
    system = sent["assistantOverrides"]["model"]["messages"][0]["content"]
    assert company in system


def test_missing_identity_key_raises(parsed, monkeypatch):
    """An anonymous caller is worse than a re-planned run."""
    parsed(BRIEF)
    monkeypatch.setattr(voice_module, "playbook", lambda: {"call_policy": {}})
    envelope = VoiceOutreachAgent().run(
        {"lead": LEAD, "firmographics": FIRMO, "assigned_rep": "Alex"}
    )
    assert envelope["status"] == "error"


def test_the_briefing_forbids_a_bracketed_placeholder(parsed):
    """Belt and braces: the instruction that says never to invent a name."""
    calls = parsed(BRIEF)
    VoiceOutreachAgent().run(
        {"lead": LEAD, "firmographics": FIRMO, "assigned_rep": "Alex Chen"}
    )
    instructions = calls[0]["instructions"].lower()
    assert "bracket" in instructions
    assert "placeholder" in instructions


def test_missing_policy_key_raises(parsed, monkeypatch):
    """A misconfigured policy is a re-plan, not a call made under unknown rules."""
    parsed(BRIEF)
    monkeypatch.setattr(
        voice_module, "_policy", lambda: {"max_duration_minutes": 12}
    )
    envelope = VoiceOutreachAgent().run(
        {"lead": LEAD, "firmographics": FIRMO, "assigned_rep": "Alex"}
    )
    assert envelope["status"] == "error"


def test_enabled_outreach_posts_once_and_does_not_wait(parsed, monkeypatch):
    """With calls on: exactly one POST, and it returns on `dialing`."""
    parsed(BRIEF)
    monkeypatch.setenv("VOICE_ENABLED", "true")
    monkeypatch.setenv("VOICE_API_KEY", "vk-test")
    monkeypatch.setenv("VOICE_AGENT_ID", "agent-1")

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": "call-abc", "status": "queued"})

    monkeypatch.setattr(
        voice_module,
        "VoiceClient",
        lambda: VoiceClient(transport=httpx.MockTransport(handler)),
    )

    envelope = VoiceOutreachAgent().run(
        {"lead": LEAD, "firmographics": FIRMO, "assigned_rep": "Alex Chen"}
    )

    assert envelope["status"] == "ok"
    assert envelope["output"]["voice_call"]["call_id"] == "call-abc"
    assert envelope["output"]["voice_call"]["dry_run"] is False
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/call"
    assert seen[0].headers["authorization"] == "Bearer vk-test"

    body = json.loads(seen[0].content)
    assert body["customer"]["number"] == LEAD["phone"]
    # The ceiling the playbook declares, not a literal in the agent.
    assert body["assistantOverrides"]["maxDurationSeconds"] == 720

    # Still only the briefing call: minutes are unknown until the call ends, and
    # billing an estimate would put a number in the ledger no invoice matches.
    assert len(envelope["cost_events"]) == 1


# ------------------------------------------------------------- voice_call_outcome


def test_outcome_bills_the_minutes_exactly_once(parsed):
    calls = parsed(OUTCOME)
    envelope = VoiceCallOutcomeAgent().run(
        {
            "voice_call": {"call_id": "call-abc"},
            "call_record": {
                "status": "completed",
                "duration_seconds": 240,
                "cost_usd": 0.31,
                "transcript": "assistant: hi\nuser: sure",
            },
        }
    )

    assert envelope["status"] == "ok"
    out = envelope["output"]["voice_call_outcome"]
    assert out["disposition"] == "meeting_booked"
    assert out["cost_usd"] == pytest.approx(0.31)
    # Status and duration are stamped from the record, never restated by the model.
    assert out["status"] == "completed"
    assert out["duration_seconds"] == 240

    # One LLM event plus one telephony event.
    amounts = sorted(e["amount_usd"] for e in envelope["cost_events"])
    assert amounts == pytest.approx([0.001, 0.31])
    assert len(calls) == 1


def test_outcome_records_nothing_when_the_call_cost_nothing(parsed):
    """An unpriced vendor logs no event, rather than a stream of zero-dollar ones."""
    parsed(OUTCOME)
    envelope = VoiceCallOutcomeAgent().run(
        {
            "voice_call": {"call_id": "dry-1"},
            "call_record": {"status": "completed", "duration_seconds": 0, "cost_usd": 0},
        }
    )
    assert len(envelope["cost_events"]) == 1  # the LLM call only


def test_a_call_the_vendor_lost_is_still_a_writable_outcome(parsed):
    """status ok, disposition unknown -- never an error.

    The planner must be able to write "we could not reach them" back to the CRM.
    Erroring here would burn a retry on a call that will never be found.
    """
    parsed()
    envelope = VoiceCallOutcomeAgent().run({"voice_call": {"call_id": "call-gone"}})

    assert envelope["status"] == "ok"
    out = envelope["output"]["voice_call_outcome"]
    assert out["disposition"] == "unknown"
    assert out["meeting_booked"] is False
    assert out["source"] == "inferred"


def test_outcome_needs_a_call_id(parsed):
    parsed(OUTCOME)
    envelope = VoiceCallOutcomeAgent().run({})
    assert envelope["status"] == "error"
    assert "call_id" in envelope["output"]["error"]


# ------------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    "ended_reason,expected",
    [
        ("customer-ended-call", "completed"),
        ("assistant-forwarded-call", "transferred"),
        ("customer-did-not-answer", "no_answer"),
        ("voicemail", "no_answer"),
        ("something-new-from-the-vendor", "completed"),
    ],
)
def test_vendor_end_reasons_map_onto_our_vocabulary(ended_reason, expected):
    record = normalize_call(
        {"id": "c1", "endedReason": ended_reason, "durationSeconds": 90}
    )
    assert record["status"] == expected


def test_a_call_that_left_no_trace_at_all_is_a_failure():
    """`failed` is reserved for no duration AND no end reason -- nothing happened."""
    assert normalize_call({"id": "c1"})["status"] == "failed"


def test_an_unmapped_end_reason_on_a_connected_call_is_completed():
    """An end reason we have not mapped is our gap, not a failed conversation.

    Vendors add reasons freely. Treating each new one as a failure would write
    "failed" against calls that went fine, and the CRM would say the opposite of
    what happened.
    """
    record = normalize_call(
        {
            "id": "c1",
            "endedReason": "some-reason-invented-next-quarter",
            "startedAt": "2026-10-15T20:20:28.619Z",
            "endedAt": "2026-10-15T20:22:31.036Z",
        }
    )
    assert record["status"] == "completed"
    assert record["duration_seconds"] == 122


# ------------------------------------------------------- duration, the real shape


def test_duration_from_timestamps_only():
    """The exact `GET /call` shape Vapi returns: no duration field of any kind.

    This is the test that was missing. Every earlier case fed `durationSeconds`,
    which Vapi never sends -- so the suite passed green while a real 2-minute call
    came back with duration None, a $0.00 cost and a status that could read
    `failed`. Verified against call 01a14139 (20:20:28 -> 20:22:31).
    """
    record = normalize_call(
        {
            "id": "01a14139",
            "endedReason": "customer-ended-call",
            "startedAt": "2026-10-15T20:20:28.619Z",
            "endedAt": "2026-10-15T20:22:31.036Z",
            "cost": 0.1336,
        }
    )
    assert record["duration_seconds"] == 122
    assert record["status"] == "completed"
    assert record["cost_usd"] == pytest.approx(0.1336)


def test_duration_ms_is_accepted():
    """The middle arm: vendors that report milliseconds rather than seconds."""
    assert normalize_call({"id": "c1", "durationMs": 122400})["duration_seconds"] == 122


def test_a_direct_duration_field_wins_over_timestamps():
    """Most authoritative first -- a vendor that states a duration is believed."""
    record = normalize_call(
        {
            "id": "c1",
            "durationSeconds": 90,
            "startedAt": "2026-10-15T20:20:28Z",
            "endedAt": "2026-10-15T20:22:31Z",
        }
    )
    assert record["duration_seconds"] == 90


def test_unparseable_timestamps_do_not_raise():
    """A vendor date format we cannot read is a missing duration, not a crash."""
    record = normalize_call(
        {"id": "c1", "startedAt": "last Tuesday", "endedAt": "later"}
    )
    assert record["duration_seconds"] is None


def test_cost_falls_back_to_minutes_with_only_timestamps(monkeypatch):
    """Pins the $0.00 regression: the fallback must fire on a derived duration.

    The live call recorded $0.00 against a real $0.1336 precisely because the
    duration was None here, so this arithmetic was skipped.
    """
    monkeypatch.setattr("departments.sales.clients.VOICE_PER_MINUTE_USD", 0.15)
    record = normalize_call(
        {
            "id": "c1",
            "endedReason": "customer-ended-call",
            "startedAt": "2026-10-15T20:20:28.619Z",
            "endedAt": "2026-10-15T20:22:31.036Z",
            "cost": 0,
        }
    )
    assert record["cost_usd"] == pytest.approx(122 / 60 * 0.15)
    assert record["cost_usd"] > 0


def test_cost_falls_back_to_minutes_when_the_vendor_reports_zero(monkeypatch):
    """The vendor's cost field is documented as stale and often arrives zero."""
    monkeypatch.setattr("departments.sales.clients.VOICE_PER_MINUTE_USD", 0.10)
    record = normalize_call(
        {"id": "c1", "endedReason": "customer-ended-call", "durationSeconds": 120, "cost": 0}
    )
    assert record["cost_usd"] == pytest.approx(0.20)


def test_transcript_list_is_flattened():
    record = normalize_call(
        {
            "id": "c1",
            "durationSeconds": 10,
            "transcript": [
                {"role": "assistant", "message": "hello"},
                {"role": "user", "message": "hi"},
            ],
        }
    )
    assert record["transcript"] == "assistant: hello\nuser: hi"


# --------------------------------------------------------------- the sales module


def test_sales_server_still_acquires_no_transport():
    """The guarantee that keeps `send_outreach` unable to send.

    The voice agents need HTTP, which is exactly why their client lives in a
    sibling module. If a transport ever appears in server.py this fails, and it
    should -- the no-send promise is scoped to this file's source.
    """
    source = inspect.getsource(sales_server)
    for forbidden in ("httpx", "requests", "smtplib", "urllib", "Client("):
        assert forbidden not in source, f"sales server acquired {forbidden}"


def test_identity_also_reaches_the_email_draft(parsed):
    """outreach_draft had the same gap, just less visible than speech.

    "from [Company]" in an email body is no better than it spoken aloud, and both
    prompts now render the same single source.
    """
    from departments.sales.server import OutreachDraftAgent

    calls = parsed({"subject": "s", "body": "b", "personalization_notes": []})
    OutreachDraftAgent().run(
        {"firmographics": FIRMO, "assigned_rep": "Alex Chen", "lead": LEAD}
    )
    assert playbook()["identity"]["company"] in calls[0]["instructions"]


def test_live_tools_never_reach_the_planner():
    """In-call tools answer inside a blocking webhook; a planner call cannot fit.

    Checks the imports rather than the source text, because the module's own
    docstring explains at length why it does not touch the planner -- and a
    substring match would read that explanation as a violation.
    """
    import ast

    import departments.sales.live_tools as live_tools

    tree = ast.parse(inspect.getsource(live_tools))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{a.name}" for a in node.names)

    for forbidden in ("core.planner", "core.llm", "core.agent", "core.registry", "httpx"):
        assert not any(
            name == forbidden or name.startswith(f"{forbidden}.")
            for name in imported
        ), f"live tools imported {forbidden}"
