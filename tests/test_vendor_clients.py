"""Transport behaviour for the two vendor clients.

Both vendors fail in ways the agents above them must not have to think about, so
the mapping from HTTP status to a legible `VendorError` is pinned here rather
than in each agent. The Clay poll loop is the other half: a routine that never
finishes has to end on this side, because `max_steps` is a step ceiling and would
never cut it off.
"""

from __future__ import annotations

import json

import httpx
import pytest

from departments.revops import clients
from departments.revops.clients import ClayClient, HubspotClient, VendorError


def _client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://vendor.test"
    )


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, "rejected the credentials"),
        (403, "rejected the credentials"),
        (429, "rate limited"),
        (500, "returned 500"),
    ],
)
def test_http_failures_become_legible_vendor_errors(status, expected):
    with _client(lambda request: httpx.Response(status, text="nope")) as client:
        with pytest.raises(VendorError, match=expected):
            clients._request(client, "GET", "/thing", "Clay")


def test_a_transport_failure_names_the_vendor_and_the_call():
    def boom(request):
        raise httpx.ConnectError("no route to host")

    with _client(boom) as client:
        with pytest.raises(VendorError, match="Clay GET /thing failed"):
            clients._request(client, "GET", "/thing", "Clay")


def test_non_json_is_reported_rather_than_raising_a_bare_valueerror():
    with _client(lambda request: httpx.Response(200, text="<html>oops</html>")) as client:
        with pytest.raises(VendorError, match="non-JSON"):
            clients._request(client, "GET", "/thing", "HubSpot")


def test_an_empty_body_is_not_an_error():
    with _client(lambda request: httpx.Response(204)) as client:
        assert clients._request(client, "DELETE", "/thing", "HubSpot") == {}


# ------------------------------------------------------------------ poll loop


@pytest.fixture
def clay(monkeypatch):
    monkeypatch.setattr(clients, "CLAY_POLL_INTERVAL_S", 0)
    return ClayClient(api_key="test-key")


def _routed(monkeypatch, clay, handler):
    """Route this client through a mock transport.

    Injected rather than swapping `_client`, so the real headers, base URL, and
    timeout are all still exercised -- replacing the method would test a client
    the production code never builds.
    """
    clay.transport = httpx.MockTransport(handler)


def test_the_api_key_travels_on_clays_own_header(clay, monkeypatch):
    """Not the Authorization bearer scheme HubSpot uses.

    Getting this wrong surfaces as a 401, which is indistinguishable from a
    bad key -- an hour of debugging the wrong thing.
    """
    seen: dict = {}

    def handler(request):
        seen["header"] = request.headers.get("clay-api-key")
        seen["auth"] = request.headers.get("authorization")
        if request.method == "POST":
            return httpx.Response(202, json={"routine_run_id": "run-1"})
        return httpx.Response(200, json={"status": "complete", "data": []})

    _routed(monkeypatch, clay, handler)
    clay.run_routine("function:t_abc", [{"domain": "x.com"}])

    assert seen["header"] == "test-key"
    assert seen["auth"] is None


def test_bare_inputs_are_wrapped_in_clays_per_item_envelope(clay, monkeypatch):
    """Agents pass plain input dicts; the {id, inputs} shape is built here."""
    seen: dict = {}

    def handler(request):
        if request.method == "POST":
            seen["body"] = json.loads(request.content)
            seen["url"] = str(request.url)
            return httpx.Response(202, json={"routine_run_id": "run-1"})
        return httpx.Response(200, json={"status": "complete", "data": []})

    _routed(monkeypatch, clay, handler)
    clay.run_routine("function:t_abc", [{"domain": "x.com"}])

    assert seen["body"] == {"items": [{"id": "row-1", "inputs": {"domain": "x.com"}}]}
    assert seen["url"].endswith("/routines/function:t_abc/run")


def test_a_routine_is_polled_until_it_completes(clay, monkeypatch):
    polls = {"n": 0}

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"routine_run_id": "run-1"})
        polls["n"] += 1
        assert str(request.url).endswith("/routines/run/run-1/results")
        if polls["n"] < 3:
            # Clay answers 202 while items are outstanding.
            return httpx.Response(
                202, json={"status": "in_progress", "total": 1, "finished": 0}
            )
        return httpx.Response(
            200,
            json={
                "status": "complete",
                "data": [{"id": "row-1", "status": "complete", "result": {"a": 1}}],
            },
        )

    _routed(monkeypatch, clay, handler)

    result = clay.run_routine("function:t_abc", [{"domain": "x.com"}])

    assert result["data"][0]["result"] == {"a": 1}
    assert polls["n"] == 3


def test_a_routine_that_never_finishes_times_out(clay, monkeypatch):
    monkeypatch.setattr(clients, "CLAY_MAX_WAIT_S", 0)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"routine_run_id": "run-1"})
        return httpx.Response(
            202, json={"status": "in_progress", "total": 4, "finished": 1}
        )

    _routed(monkeypatch, clay, handler)

    # The item counts go in the message: "slow" and "stuck on one row" are
    # different problems and the error should say which.
    with pytest.raises(VendorError, match=r"in_progress \(1/4 items\) after"):
        clay.run_routine("function:t_abc", [{"domain": "x.com"}])


def test_a_run_with_no_id_is_caught_rather_than_polled_forever(clay, monkeypatch):
    _routed(monkeypatch, clay, lambda request: httpx.Response(202, json={}))

    with pytest.raises(VendorError, match="did not return a routine_run_id"):
        clay.run_routine("function:t_abc", [{"domain": "x.com"}])


def test_a_failed_routine_surfaces_the_vendor_reason(clay, monkeypatch):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"routine_run_id": "run-1"})
        return httpx.Response(
            200, json={"status": "failed", "message": "quota exhausted"}
        )

    _routed(monkeypatch, clay, handler)

    with pytest.raises(VendorError, match="quota exhausted"):
        clay.run_routine("function:t_abc", [{"domain": "x.com"}])


def test_a_search_creates_then_runs_its_iterator(clay, monkeypatch):
    """Two calls: creating a search yields only an id, records come from the run."""
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        if str(request.url).endswith("/search/filters-mode"):
            body = json.loads(request.content)
            assert body["source_type"] == "companies"
            return httpx.Response(200, json={"search_id": "s-1"})
        return httpx.Response(
            200, json={"data": [{"name": "Northwind"}], "has_more": False}
        )

    _routed(monkeypatch, clay, handler)

    result = clay.search({"industry": ["software"]}, limit=25)

    assert result["data"] == [{"name": "Northwind"}]
    assert seen[1].endswith("/search/filters-mode/s-1/run")


def test_an_unconfigured_routine_id_is_caught_before_the_call(clay):
    with pytest.raises(VendorError, match="no Clay routine id"):
        clay.run_routine("", [{"domain": "x.com"}])


def test_the_batch_ceiling_is_enforced_locally(clay):
    with pytest.raises(VendorError, match="1-100 items"):
        clay.run_routine("routine-1", [])


# -------------------------------------------------------------------- hubspot


@pytest.fixture
def hubspot():
    return HubspotClient(access_token="test-token")


def _route(client, handler):
    """Same injection as `_routed` above, and for the same reason: replacing
    `_client` would test a client the production code never builds."""
    client.transport = httpx.MockTransport(handler)


def test_the_token_travels_as_an_authorization_bearer(hubspot):
    """The mirror of the Clay header test, and the same hour of debugging.

    Clay's key goes on its own header; HubSpot's goes on Authorization. Nothing
    asserted this until a live 401 made the difference expensive to guess at.
    """
    seen: dict = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["clay"] = request.headers.get("clay-api-key")
        return httpx.Response(200, json={"results": []})

    _route(hubspot, handler)
    hubspot.search("companies", "domain", "northwind.com", ["name"])

    assert seen["auth"] == "Bearer test-token"
    assert seen["clay"] is None


def test_a_search_posts_hubspots_filter_envelope(hubspot):
    """Agents pass a property and a value; the filterGroups shape is built here."""
    seen: dict = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": [{"id": "42"}]})

    _route(hubspot, handler)
    found = hubspot.search("companies", "domain", "northwind.com", ["name", "domain"])

    assert seen["url"].endswith("/crm/v3/objects/companies/search")
    assert seen["body"] == {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "domain",
                        "operator": "EQ",
                        "value": "northwind.com",
                    }
                ]
            }
        ],
        "properties": ["name", "domain"],
        "limit": 1,
    }
    assert found == {"id": "42"}


def test_no_match_is_none_rather_than_an_empty_dict(hubspot):
    """`crm_lookup` branches on truthiness, and {} would read as a miss twice over."""
    _route(hubspot, lambda request: httpx.Response(200, json={"results": []}))

    assert hubspot.search("companies", "domain", "nope.com", ["name"]) is None


def test_writes_wrap_properties_and_pick_the_right_verb(hubspot):
    seen: list[tuple[str, str, dict]] = []

    def handler(request):
        seen.append((request.method, str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"id": "99"})

    _route(hubspot, handler)
    hubspot.create("companies", {"name": "Northwind"})
    hubspot.update("companies", "99", {"name": "Northwind Logistics"})

    method, url, body = seen[0]
    assert (method, body) == ("POST", {"properties": {"name": "Northwind"}})
    assert url.endswith("/crm/v3/objects/companies")

    method, url, body = seen[1]
    assert (method, body) == ("PATCH", {"properties": {"name": "Northwind Logistics"}})
    assert url.endswith("/crm/v3/objects/companies/99")


def test_an_absent_property_is_an_answer_and_a_broken_one_is_not(hubspot):
    """`hubspot_setup` asks about properties it is about to create.

    404 has to come back as None or provisioning could never be idempotent -- but
    only 404. A 500 still has to raise, or a portal outage would read as an empty
    portal and the script would try to create properties that already exist.
    """
    _route(hubspot, lambda request: httpx.Response(404, json={"message": "no"}))
    assert hubspot.get_property("companies", "nexusgtm_segment") is None

    _route(hubspot, lambda request: httpx.Response(500, text="down"))
    with pytest.raises(VendorError, match="returned 500"):
        hubspot.get_property("companies", "nexusgtm_segment")


def test_a_record_url_falls_back_to_the_api_when_no_portal_is_set(hubspot, monkeypatch):
    """The portal id only makes the URL clickable; without it the run still needs
    to say which record it wrote."""
    monkeypatch.delenv("HUBSPOT_PORTAL_ID", raising=False)
    assert hubspot.record_url(None, "companies", "99").endswith(
        "/crm/v3/objects/companies/99"
    )

    monkeypatch.setenv("HUBSPOT_PORTAL_ID", "12345")
    assert (
        hubspot.record_url(None, "companies", "99")
        == "https://app.hubspot.com/contacts/12345/record/companies/99"
    )
