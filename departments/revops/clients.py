"""HTTP clients for the two vendor systems RevOps owns: Clay and HubSpot.

Kept out of the agents so the agents stay declarative -- an agent says what it
wants, not how the vendor is authenticated, paged, or polled.

The two vendors have very different call shapes and the difference is load-
bearing:

    Clay      asynchronous. `execute routine` returns a run id; results arrive
              from a separate progress endpoint. There is no synchronous
              enrichment call on a non-Enterprise plan, so the wait happens here,
              bounded, rather than leaking a job id up into the blackboard.
    HubSpot   synchronous REST v3, bearer token, request/response.

Every failure raises. `Agent.run` turns a raise into status="error", which the
orchestrator feeds back to the planner as an observation -- so a missing API key
is a re-plan, not a crash.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

CLAY_BASE_URL = os.getenv("CLAY_BASE_URL", "https://api.clay.com/public/v0")
HUBSPOT_BASE_URL = os.getenv("HUBSPOT_BASE_URL", "https://api.hubapi.com")

# Waterfall enrichment fans out across many providers, so tens of seconds is
# normal and a minute is not alarming. The ceiling exists because `max_steps` is
# a step ceiling, not a time ceiling -- nothing else would ever end this wait.
CLAY_MAX_WAIT_S = float(os.getenv("CLAY_MAX_WAIT_S", "120"))
CLAY_POLL_INTERVAL_S = float(os.getenv("CLAY_POLL_INTERVAL_S", "3"))

# Clay bills credits, not tokens. Priced here so the credits a routine reports
# reach the same budget guardrail that halts on LLM spend; an unset rate prices
# at zero, matching how `core.llm.price_of` handles an unknown model rather than
# guessing a number.
CLAY_CREDIT_USD = float(os.getenv("CLAY_CREDIT_USD", "0") or 0)


class VendorError(RuntimeError):
    """A vendor call failed, timed out, or was rejected."""


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    vendor: str,
    *,
    absent_status: int | None = None,
    **kwargs: Any,
) -> dict | None:
    """Send one request and map every vendor failure onto a legible VendorError.

    `absent_status` names a status that means "no such thing" rather than
    "something went wrong", and turns it into None -- HubSpot answers 404 when
    asked for a property a portal does not have, which is the question
    `scripts/hubspot_setup.py` needs to ask before creating one. Without it
    passed, this only ever returns a dict.
    """
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise VendorError(f"{vendor} {method} {path} failed: {exc}") from exc

    if absent_status is not None and response.status_code == absent_status:
        return None

    if response.status_code == 401 or response.status_code == 403:
        raise VendorError(
            f"{vendor} rejected the credentials ({response.status_code}). "
            f"Check the API key in this process's environment."
        )
    if response.status_code == 429:
        raise VendorError(f"{vendor} rate limited the request (429).")
    if response.status_code >= 400:
        raise VendorError(
            f"{vendor} {method} {path} returned {response.status_code}: "
            f"{response.text[:300]}"
        )

    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise VendorError(
            f"{vendor} returned non-JSON from {path}: {response.text[:200]}"
        ) from exc


class ClayClient:
    """Clay Public API: Searches for sourcing, Routines for enrichment.

    Routines are asynchronous -- execute, then poll progress until terminal.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.api_key = api_key or os.getenv("CLAY_API_KEY", "")
        if not self.api_key:
            raise VendorError(
                "CLAY_API_KEY is not set in this process. Department servers call "
                "load_dotenv() themselves and do not inherit the API's environment."
            )
        self.base_url = (base_url or CLAY_BASE_URL).rstrip("/")
        # Injectable so tests exercise the real headers and URLs against a mock
        # transport rather than replacing this method and quietly losing both.
        self.transport = transport

    def _client(self) -> httpx.Client:
        # Clay authenticates on its own header, not the Authorization bearer
        # scheme HubSpot uses. Getting this wrong reads as a 401, which is
        # indistinguishable from a bad key.
        return httpx.Client(
            base_url=self.base_url,
            headers={
                "clay-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
            transport=self.transport,
        )

    def run_routine(self, routine_id: str, inputs: list[dict]) -> dict:
        """Execute a routine over 1-100 items and wait for it to finish.

        `inputs` are the bare input objects; the per-item envelope Clay expects
        (`{"id": ..., "inputs": {...}}`) is built here. Returns the terminal
        results payload.
        """
        if not routine_id:
            raise VendorError("no Clay routine id configured for this agent")
        if not 1 <= len(inputs) <= 100:
            raise VendorError(f"Clay routines take 1-100 items, got {len(inputs)}")

        items = [
            {"id": f"row-{i}", "inputs": item} for i, item in enumerate(inputs, start=1)
        ]

        with self._client() as client:
            started = _request(
                client,
                "POST",
                f"/routines/{routine_id}/run",
                "Clay",
                json={"items": items},
            )
            run_id = started.get("routine_run_id")
            if not run_id:
                raise VendorError(f"Clay did not return a routine_run_id: {started}")
            return self._await_run(client, routine_id, str(run_id))

    def _await_run(self, client: httpx.Client, routine_id: str, run_id: str) -> dict:
        """Poll until the run reports `complete`.

        The results endpoint answers 202 while work is outstanding and 200 once
        it is done, so the HTTP status alone would be a workable signal -- but
        the body's own `status` is the documented one, and it survives a proxy
        that normalises 202 to 200.
        """
        deadline = time.monotonic() + CLAY_MAX_WAIT_S
        while True:
            progress = _request(
                client, "GET", f"/routines/run/{run_id}/results", "Clay"
            )
            status = str(progress.get("status", "")).lower()
            if status == "complete":
                return progress
            if status in ("failed", "error", "cancelled", "canceled"):
                raise VendorError(
                    f"Clay routine {routine_id} run {run_id} ended {status}: "
                    f"{progress.get('message') or progress}"
                )
            if time.monotonic() >= deadline:
                finished = progress.get("finished")
                total = progress.get("total")
                raise VendorError(
                    f"Clay routine {routine_id} run {run_id} still "
                    f"{status or 'pending'} ({finished}/{total} items) after "
                    f"{CLAY_MAX_WAIT_S:.0f}s"
                )
            time.sleep(CLAY_POLL_INTERVAL_S)

    def search(self, filters: dict, limit: int, source_type: str = "companies") -> dict:
        """Run a structured-filter Search over Clay's GTM dataset.

        Two calls: creating a search returns only an id, and the records come
        from running that search's iterator. One page is enough here -- this
        system qualifies one account per orchestration.
        """
        with self._client() as client:
            created = _request(
                client,
                "POST",
                "/search/filters-mode",
                "Clay",
                json={"source_type": source_type, "filters": filters},
            )
            search_id = created.get("search_id")
            if not search_id:
                raise VendorError(f"Clay did not return a search_id: {created}")

            return _request(
                client,
                "POST",
                f"/search/filters-mode/{search_id}/run",
                "Clay",
                json={"limit": max(1, min(int(limit), 500))},
            )

    def search_fields(self, source_type: str = "companies") -> dict:
        """Filter fields Clay accepts for a source type.

        Not called in the request path -- the field vocabulary is Clay's to
        change, and this is how you look up the current names when a search
        starts coming back empty.
        """
        with self._client() as client:
            return _request(
                client,
                "GET",
                "/search/filters-mode/fields",
                "Clay",
                params={"source_type": source_type},
            )


class HubspotClient:
    """HubSpot CRM v3. Synchronous REST, private app bearer token."""

    def __init__(
        self,
        access_token: str | None = None,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.access_token = access_token or os.getenv("HUBSPOT_ACCESS_TOKEN", "")
        if not self.access_token:
            raise VendorError(
                "HUBSPOT_ACCESS_TOKEN is not set in this process. Department servers "
                "call load_dotenv() themselves and do not inherit the API's environment."
            )
        self.base_url = (base_url or HUBSPOT_BASE_URL).rstrip("/")
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
            transport=self.transport,
        )

    def search(
        self, object_type: str, prop: str, value: str, properties: list[str]
    ) -> dict | None:
        """First record whose `prop` equals `value`, or None."""
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": prop, "operator": "EQ", "value": value}]}
            ],
            "properties": properties,
            "limit": 1,
        }
        with self._client() as client:
            found = _request(
                client,
                "POST",
                f"/crm/v3/objects/{object_type}/search",
                "HubSpot",
                json=body,
            )
        results = found.get("results") or []
        return results[0] if results else None

    def create(self, object_type: str, properties: dict) -> dict:
        with self._client() as client:
            return _request(
                client,
                "POST",
                f"/crm/v3/objects/{object_type}",
                "HubSpot",
                json={"properties": properties},
            )

    def update(self, object_type: str, record_id: str, properties: dict) -> dict:
        with self._client() as client:
            return _request(
                client,
                "PATCH",
                f"/crm/v3/objects/{object_type}/{record_id}",
                "HubSpot",
                json={"properties": properties},
            )

    def get_property(self, object_type: str, name: str) -> dict | None:
        """The property definition, or None when this portal has no such property.

        A 404 here is an answer, not a failure: it is how `hubspot_setup` decides
        whether to create one. Same shape as `search` returning None for no match.
        """
        with self._client() as client:
            return _request(
                client,
                "GET",
                f"/crm/v3/properties/{object_type}/{name}",
                "HubSpot",
                absent_status=404,
            )

    def create_property(self, object_type: str, spec: dict) -> dict:
        """Define a custom property. Needs the crm.schemas.<object>.write scope."""
        with self._client() as client:
            return _request(
                client,
                "POST",
                f"/crm/v3/properties/{object_type}",
                "HubSpot",
                json=spec,
            )

    def get_property_group(self, object_type: str, name: str) -> dict | None:
        """The group definition, or None when this portal has no such group."""
        with self._client() as client:
            return _request(
                client,
                "GET",
                f"/crm/v3/properties/{object_type}/groups/{name}",
                "HubSpot",
                absent_status=404,
            )

    def create_property_group(self, object_type: str, spec: dict) -> dict:
        """Define a property group. Properties need one to land in."""
        with self._client() as client:
            return _request(
                client,
                "POST",
                f"/crm/v3/properties/{object_type}/groups",
                "HubSpot",
                json=spec,
            )

    def account_info(self) -> dict:
        """Portal id, time zone, and account type. The cheapest token check there
        is -- it needs no CRM scope, so a failure here is the token itself."""
        with self._client() as client:
            return _request(client, "GET", "/account-info/v3/details", "HubSpot")

    def record_url(self, portal_id: str | None, object_type: str, record_id: str) -> str:
        portal = portal_id or os.getenv("HUBSPOT_PORTAL_ID", "")
        if not portal:
            return f"{self.base_url}/crm/v3/objects/{object_type}/{record_id}"
        return f"https://app.hubspot.com/contacts/{portal}/record/{object_type}/{record_id}"
