"""HTTP client for the voice telephony vendor. The only vendor-aware file in sales.

Kept out of `server.py` for two reasons. The first is the same one RevOps splits
`clients.py` out for: an agent should say what it wants, not how a vendor is
authenticated. The second is specific to this department -- a test asserts that
`departments/sales/server.py` contains no transport at all, because
`send_outreach` must stay unable to send. That guarantee survives by keeping
every transport in this module instead.

Vendor-shaped field names (`endedReason`, `costBreakdown`, ...) are normalised
here and nowhere else, so switching vendor is a change to this file and no other.
Environment variables are deliberately vendor-neutral (`VOICE_*`) for the same
reason.

Every failure raises. `Agent.run` turns a raise into status="error", which the
orchestrator feeds back to the planner as an observation -- so a missing API key
is a re-plan, not a crash.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime
from typing import Any

import httpx

VOICE_BASE_URL = os.getenv("VOICE_BASE_URL", "https://api.vapi.ai")

# Off by default, like CRM_WRITE_ENABLED. Nobody wants a test run or a seed pass
# dialling real phone numbers the first time they try this, and unlike a junk CRM
# record a placed call cannot be deleted afterwards.
def calls_enabled() -> bool:
    return os.getenv("VOICE_ENABLED", "").strip().lower() in ("1", "true", "yes")


# Telephony bills by the minute, not by the token. Priced here so the minutes a
# call reports reach the same cost ledger as LLM spend; an unset rate prices at
# zero, matching how `core.llm.price_of` handles an unknown model rather than
# guessing a number.
#
# Unlike Clay credits, this is only ever *recorded*, never enforced: voice spend
# is exempt from the budget guardrail, because a ceiling cannot halt a
# conversation that has already happened.
VOICE_PER_MINUTE_USD = float(os.getenv("VOICE_PER_MINUTE_USD", "0") or 0)


class VendorError(RuntimeError):
    """A vendor call failed, timed out, or was rejected."""


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    absent_status: int | None = None,
    **kwargs: Any,
) -> dict | None:
    """Send one request and map every vendor failure onto a legible VendorError.

    `absent_status` names a status that means "no such thing" rather than
    "something went wrong". The voice vendor answers 404 for a call id it has no
    record of, and that is an answer the reconciler needs: a call the vendor lost
    still has to reach the planner, as an unknown outcome rather than an error.
    """
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise VendorError(f"voice vendor {method} {path} failed: {exc}") from exc

    if absent_status is not None and response.status_code == absent_status:
        return None

    if response.status_code in (401, 403):
        raise VendorError(
            f"voice vendor rejected the credentials ({response.status_code}). "
            f"Check VOICE_API_KEY in this process's environment."
        )
    if response.status_code == 429:
        raise VendorError("voice vendor rate limited the request (429).")
    if response.status_code >= 400:
        raise VendorError(
            f"voice vendor {method} {path} returned {response.status_code}: "
            f"{response.text[:300]}"
        )

    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise VendorError(
            f"voice vendor returned non-JSON from {path}: {response.text[:200]}"
        ) from exc


def verify_signature(body: bytes, header: str | None) -> bool:
    """Authenticate a webhook against VOICE_WEBHOOK_SECRET. Constant-time either way.

    Two schemes are accepted, because vendors disagree about which they use and
    Vapi in particular has no dependable signature at all -- its own support
    could not confirm one, and the documented mechanism is a shared secret echoed
    back verbatim:

        shared secret   the header IS the secret (Vapi's `x-vapi-secret`, or an
                        Authorization bearer). Does not authenticate the body,
                        only the caller.
        HMAC-SHA256     hex digest of the raw body under the secret, optionally
                        prefixed (`sha256=...`). Stronger, and what a vendor that
                        signs will send.

    Accepting both is what keeps a real vendor working without leaving the
    endpoint open: a caller still has to know the secret. Returns True when no
    secret is configured -- the replay script's path -- and the endpoint logs a
    warning on every such request.
    """
    secret = os.getenv("VOICE_WEBHOOK_SECRET", "").strip()
    if not secret:
        return True
    if not header:
        return False

    presented = header.strip()
    # Strip a bearer prefix before either comparison.
    if presented.lower().startswith("bearer "):
        presented = presented[7:].strip()

    # Shared secret, echoed verbatim.
    if hmac.compare_digest(presented, secret):
        return True

    # HMAC of the body, with or without an algorithm prefix.
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, presented.rsplit("=", 1)[-1])


class VoiceClient:
    """The telephony vendor's REST API: place a call, then read back what happened.

    Two methods, because that is the whole shape of this integration. The
    conversation itself does not come through here -- it runs on the vendor's
    side and reaches us as webhooks, which is exactly why the placing agent can
    return in one shot while the call goes on for minutes.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.api_key = api_key or os.getenv("VOICE_API_KEY", "")
        if not self.api_key:
            raise VendorError(
                "VOICE_API_KEY is not set in this process. Department servers call "
                "load_dotenv() themselves and do not inherit the API's environment."
            )
        self.base_url = (base_url or VOICE_BASE_URL).rstrip("/")
        # Injectable so tests exercise the real headers and URLs against a mock
        # transport rather than replacing this method and quietly losing both.
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
            transport=self.transport,
        )

    def create_call(
        self,
        *,
        phone_number: str,
        agent_id: str,
        system_prompt: str,
        first_message: str,
        metadata: dict,
        max_duration_seconds: int,
        from_number_id: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> dict:
        """Start an outbound call and return as soon as it is dialing.

        Deliberately does not wait. Holding this open for the call's duration
        would stall the orchestrator, and the transport would be killed by some
        layer long before a real conversation ended -- after which the planner
        would re-plan while the call was still live, and dial a second time.
        """
        body: dict[str, Any] = {
            "assistantId": agent_id,
            "customer": {"number": phone_number},
            "assistantOverrides": {
                "firstMessage": first_message,
                "maxDurationSeconds": max_duration_seconds,
                "metadata": metadata,
            },
        }

        # The caller-ID number. Optional here because the vendor falls back to the
        # assistant's own default when it has one, and a wrong id is a 400 that
        # costs nothing -- unlike omitting it, which on some accounts is also a 400.
        if from_number_id:
            body["phoneNumberId"] = from_number_id

        # Overriding the model means restating the WHOLE block, not just the part
        # being changed: the vendor validates `provider` on any model override and
        # rejects `{"messages": [...]}` alone. So the prompt is only injected when
        # a provider and model are configured to carry it -- otherwise the
        # assistant's own prompt, voice and model stand, which is the safer
        # default and the one that needs no env vars at all.
        #
        # The consequence worth knowing: without these set, the per-call briefing
        # (`talking_points`, the objective, call policy) does NOT reach the call.
        # The assistant speaks from whatever is configured in the vendor's
        # dashboard, and only `firstMessage` is per-call.
        if model_provider and model_name:
            body["assistantOverrides"]["model"] = {
                "provider": model_provider,
                "model": model_name,
                "messages": [{"role": "system", "content": system_prompt}],
            }
        with self._client() as client:
            created = _request(client, "POST", "/call", json=body)

        call_id = str((created or {}).get("id") or "").strip()
        if not call_id:
            raise VendorError(f"voice vendor did not return a call id: {created}")
        return {
            "call_id": call_id,
            "status": str((created or {}).get("status") or "dialing"),
        }

    def get_call(self, call_id: str) -> dict | None:
        """The authoritative record of a finished call, or None if the vendor lost it.

        This exists rather than trusting the end-of-call webhook because that
        payload's cost field is documented as stale -- it is assembled for
        delivery speed, not for accuracy, and arrives as zero often enough that
        billing off it would under-report every call. Fetched fresh, normalised
        to this system's vocabulary.
        """
        with self._client() as client:
            found = _request(
                client, "GET", f"/call/{call_id}", absent_status=404
            )
        return None if found is None else normalize_call(found)


_TERMINAL_STATUS = {
    # Vendor endedReason -> this system's status vocabulary. Anything unmapped
    # becomes "completed" when the call ran and "failed" when it did not, rather
    # than inventing a new status the dashboard has never heard of.
    "customer-ended-call": "completed",
    "assistant-ended-call": "completed",
    "assistant-forwarded-call": "transferred",
    "customer-did-not-answer": "no_answer",
    "customer-busy": "no_answer",
    "voicemail": "no_answer",
    "no-answer": "no_answer",
}


def _duration_of(raw: dict) -> int | None:
    """Seconds, from whichever of three shapes the vendor actually sent.

    The timestamp arm is not a nicety: Vapi returns NO duration field at all on
    `GET /call` -- not durationSeconds, not durationMs, only startedAt and endedAt
    -- so for that vendor it is the only path. Reading a field that is never
    present returned None, which silently took the per-minute cost fallback and
    the status check below with it: a real 2-minute call recorded $0.00 and could
    be classified `failed`.

    The field lookups stay ahead of it because other vendors do report a duration
    directly, and this module is what a vendor switch rewrites.
    """
    if (seconds := raw.get("durationSeconds")) is not None:
        return int(float(seconds))
    if (ms := raw.get("durationMs")) is not None:
        return int(float(ms) / 1000)

    started, ended = raw.get("startedAt"), raw.get("endedAt")
    if started and ended:
        try:
            # Vendors send Zulu time, which fromisoformat only accepts as +00:00
            # before Python 3.11.
            delta = datetime.fromisoformat(
                str(ended).replace("Z", "+00:00")
            ) - datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            return max(0, int(delta.total_seconds()))
        except ValueError:
            return None
    return None


def normalize_call(raw: dict) -> dict:
    """Vendor payload -> this system's shape. The only place vendor names appear.

    Cost comes from the vendor when it reports one and falls back to minutes times
    the configured rate, because the end-of-call figure is documented as stale and
    arrives as zero often enough that billing off it alone under-reports.
    """
    ended_reason = str(raw.get("endedReason") or "").strip()
    duration_seconds = _duration_of(raw)

    cost = raw.get("cost")
    if cost in (None, 0) and duration_seconds:
        cost = (duration_seconds / 60.0) * VOICE_PER_MINUTE_USD

    status = _TERMINAL_STATUS.get(ended_reason)
    if status is None:
        # An end reason we have not mapped is OUR gap, not a failed call -- vendors
        # add them freely, and treating every new one as a failure would write
        # "failed" against conversations that went fine. `failed` is reserved for a
        # call that left no trace at all: no duration and no end reason.
        status = "completed" if (duration_seconds or ended_reason) else "failed"

    transcript = raw.get("transcript")
    if isinstance(transcript, list):
        transcript = "\n".join(
            f"{m.get('role', '?')}: {m.get('message') or m.get('content') or ''}"
            for m in transcript
        )

    return {
        "call_id": str(raw.get("id") or ""),
        "status": status,
        "ended_reason": ended_reason or None,
        "duration_seconds": duration_seconds,
        "cost_usd": float(cost) if cost else 0.0,
        "recording_url": raw.get("recordingUrl") or raw.get("stereoRecordingUrl"),
        "transcript": transcript or None,
        "summary": raw.get("summary") or None,
        "metadata": (raw.get("assistantOverrides") or {}).get("metadata")
        or raw.get("metadata")
        or {},
    }
