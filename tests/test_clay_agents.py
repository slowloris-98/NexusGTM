"""Clay is the retrieval half of the enrichment waterfall.

What matters is not that a happy-path lookup maps fields, but that a *miss*
behaves correctly: it must not look like an error, must not write a
`firmographics` key that would unblock scoring on nothing, and must leave enough
on the blackboard for the planner to reach for the fallback instead of retrying.

One routine now returns firmographics AND the raw signal columns, so the second
half of this file pins the normaliser that turns those columns into phrases --
including the thing it exists to guarantee, that the raw blobs never reach the
blackboard at all.

No network: the vendor clients are patched at the method boundary, and the
transport-level behaviour is pinned separately in test_vendor_clients.py.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
import yaml

from core import config
from departments.revops import clay
from departments.revops.clay import ClayEnrichAgent, ClaySourceAgent


@pytest.fixture(autouse=True)
def clay_env(monkeypatch):
    monkeypatch.setenv("CLAY_API_KEY", "test-key")
    monkeypatch.setenv("CLAY_ENRICH_ROUTINE_ID", "routine-enrich")


@pytest.fixture
def clay_returns(monkeypatch):
    """Script what Clay's routine/search endpoints hand back."""

    def install(response: dict, *, method: str = "run_routine"):
        calls: list = []

        def fake(self, *args, **kwargs):
            calls.append((args, kwargs))
            return response

        monkeypatch.setattr(clay.ClayClient, method, fake)
        return calls

    return install


@pytest.fixture
def signals_policy(tmp_path, monkeypatch):
    """Point the playbook loader at a temp file carrying a known signals block."""

    def write(block: dict | None = {"recency_days": 180, "max_per_category": 3}):
        pb = {} if block is None else {"signals": block}
        (tmp_path / "playbook.yaml").write_text(yaml.safe_dump(pb), encoding="utf-8")
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        config.load.cache_clear()
        return pb

    yield write
    config.load.cache_clear()  # do not leak the temp playbook into later tests


POLICY = {"recency_days": 180, "max_per_category": 3}

LEAD = {
    "name": "Priya Raman",
    "email": "priya@northwind.com",
    "company": "Northwind Logistics",
}


def days_ago(n: int) -> str:
    """Dates in these fixtures must be relative.

    A literal like "2025-11-03" passes on the day it is written and starts
    failing once it drifts past `recency_days` -- months later, in a test that
    looks nothing like a calendar bug.
    """
    return (date.today() - timedelta(days=n)).isoformat()


def routine_result(*results, failed: int = 0) -> dict:
    """A completed routine run, in the shape Clay's results endpoint returns.

    Rows come back wrapped as {id, status, result}; a search returns records
    bare. That difference is what `_records_of` keys on.
    """
    data = [
        {"id": f"row-{i}", "status": "complete", "result": r}
        for i, r in enumerate(results, start=1)
    ]
    data += [
        {"id": f"fail-{i}", "status": "failed", "error": {"message": "no match"}}
        for i in range(failed)
    ]
    return {
        "routine_run_id": "run-1",
        "status": "complete",
        "total": len(data),
        "finished": len(data),
        "data": data,
    }


def test_a_match_produces_firmographics_stamped_as_retrieved(clay_returns, spender):
    clay_returns(
        routine_result(
            {
                "company_name": "Northwind Logistics",
                "domain": "northwind.com",
                "industry": "Logistics Software",
                "employee_count": 420,
                "revenue_band": "$25M-$50M",
                "hq_region": "North America",
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["clay_enrichment"]["matched"] is True
    firmographics = output["firmographics"]
    assert firmographics["employee_count"] == 420
    # The stamp is load-bearing: crm_sync reads it to decide whether these
    # numbers may be written to HubSpot under their canonical property names.
    assert firmographics["source"] == "clay"


def test_a_miss_withholds_firmographics_so_scoring_stays_blocked(clay_returns, spender):
    """The whole point of the miss path.

    Returning an empty `firmographics` would satisfy ScoringAgent's required
    field and let the planner score a company it knows nothing about. The same
    argument covers `buying_signals`: Clay never identified the company, so an
    empty list would assert an absence nobody actually checked.
    """
    clay_returns(routine_result())

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert "firmographics" not in output
    assert "buying_signals" not in output
    assert "signal_source" not in output
    assert output["clay_enrichment"]["matched"] is False
    assert "northwind.com" in output["clay_enrichment"]["reason"]


def test_a_miss_is_not_an_error(clay_returns):
    """It must reach the planner as an observation, not a failed run."""
    clay_returns(routine_result())

    envelope = ClayEnrichAgent().run({"lead": LEAD})

    assert envelope["status"] == "ok"


def test_a_consumer_address_is_answered_without_paying_clay(clay_returns, spender):
    calls = clay_returns(routine_result())

    output = ClayEnrichAgent().execute(
        {"lead": {"email": "alexdoe94@gmail.com", "company": "self-employed"}}, spender
    )

    assert output["clay_enrichment"]["matched"] is False
    assert "gmail.com" in output["clay_enrichment"]["reason"]
    assert "buying_signals" not in output  # no call means nothing to report
    assert calls == []  # never dispatched
    assert spender.events == []


def test_credits_are_billed_to_the_run(clay_returns, monkeypatch):
    """Clay bills credits, and the budget guardrail halts on a total.

    Without this an agent could burn a vendor quota and report a spend of zero.
    Driven through `run` rather than `execute` so the attribution the
    orchestrator will actually record is what gets asserted.

    The call count is asserted alongside the cost event, not instead of it: a
    second routine call that happened to report zero credits would slip past a
    cost-event count. One qualified lead is one Clay run.
    """
    monkeypatch.setattr(clay, "CLAY_CREDIT_USD", 0.002)
    calls = clay_returns(
        {
            **routine_result({"company_name": "Northwind", "domain": "northwind.com"}),
            "credits_used": 5,
        }
    )

    envelope = ClayEnrichAgent().run({"lead": LEAD})

    assert envelope["status"] == "ok"
    assert len(calls) == 1
    assert len(envelope["cost_events"]) == 1
    event = envelope["cost_events"][0]
    assert event["amount_usd"] == pytest.approx(0.010)
    assert (event["department"], event["agent"]) == ("revops", "clay_enrich")


def test_a_missing_key_fails_the_call_rather_than_the_process(monkeypatch):
    """A partial vendor setup is a re-plan, not a crash."""
    monkeypatch.delenv("CLAY_API_KEY", raising=False)

    envelope = ClayEnrichAgent().run({"lead": LEAD})

    assert envelope["status"] == "error"
    assert "CLAY_API_KEY" in envelope["output"]["error"]


def test_a_lead_or_an_account_will_do(clay_returns, spender):
    """The expansion flow has an account, not a lead.

    Both key off the domain, so requiring `lead` would cost the planner a
    rejected dispatch and a re-plan every time it followed the pattern prose.
    """
    clay_returns(routine_result({"company_name": "Northwind", "domain": "northwind.com"}))

    output = ClayEnrichAgent().execute(
        {"account": {"name": "Northwind", "domain": "northwind.com"}}, spender
    )

    assert output["firmographics"]["company_name"] == "Northwind"


def test_neither_a_lead_nor_an_account_raises(spender):
    with pytest.raises(ValueError, match="lead or an account"):
        ClayEnrichAgent().execute({}, spender)


def test_sourcing_promotes_one_candidate_and_keeps_the_rest(
    clay_returns, spender, monkeypatch
):
    """One orchestration qualifies one lead; the list stays for visibility."""
    monkeypatch.setattr(clay, "default_source_limit", lambda: 10)
    # A search returns records bare under `data`, with no per-row result wrapper.
    clay_returns(
        {
            "data": [
                {"company_name": "Northwind", "domain": "northwind.com"},
                {"company_name": "Quillstack", "domain": "quillstack.com"},
            ],
            "has_more": False,
        },
        method="search",
    )

    output = ClaySourceAgent().execute({"icp_brief": "B2B software in NA"}, spender)

    assert len(output["sourced_accounts"]) == 2
    assert output["lead"]["company"] == "Northwind"
    assert output["lead"]["source"] == "clay_source"


def test_sourcing_requires_a_brief(spender):
    with pytest.raises(ValueError, match="icp_brief"):
        ClaySourceAgent().execute({"icp_brief": "   "}, spender)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (420, 420),
        (420.7, 420),
        ("420", 420),
        ("1,200", 1200),
        ("1_200", 1200),
        ("500+", 500),
        ("420.0", 420),
        ("~350", 350),
        (">1000 employees", 1000),
        ("", 0),
        (None, 0),
        ("unknown", 0),
    ],
)
def test_headcount_survives_a_text_typed_clay_column(raw, expected, clay_returns, spender):
    """A Clay output column typed Text is a legitimate configuration.

    Falling back to 0 would not be a harmless default -- it trips the
    under_10_employees negative signal and disqualifies a good lead.
    """
    clay_returns(
        routine_result(
            {"company_name": "Northwind", "domain": "northwind.com", "employee_count": raw}
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["firmographics"]["employee_count"] == expected


def test_clays_own_display_titles_map_without_renaming(clay_returns, spender):
    """A function wired straight from "Enrich Company" emits titles like this.

    Exact-key lookup would miss every one of them, and a miss reads downstream
    as "Clay had no data" rather than "we asked for the wrong key". The signal
    columns are included because title case is exactly what a Clay-wired
    function emits for those too.
    """
    clay_returns(
        routine_result(
            {
                "Name": "Northwind Logistics",
                "Domain": "northwind.com",
                "Industry": "Logistics Software",
                "Employee Count": "1,200",
                "Estimated Annual Revenue": "$50M-$100M",
                "Headquarters": "Chicago, IL",
                "Job Openings": [{"title": "VP RevOps", "posted_at": days_ago(4)}],
                "Latest Funding": {"round": "Series B", "amount": 30_000_000,
                                   "date": days_ago(30)},
                "Recent News": [{"title": "Opens Berlin office", "date": days_ago(9)}],
                "Technologies": ["Salesforce", "Segment"],
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)
    firmographics = output["firmographics"]

    assert firmographics["company_name"] == "Northwind Logistics"
    assert firmographics["industry"] == "Logistics Software"
    assert firmographics["employee_count"] == 1200
    assert firmographics["revenue_band"] == "$50M-$100M"
    assert firmographics["hq_region"] == "Chicago, IL"
    assert output["buying_signals"] == [
        "funding: raised Series B $30M",
        "hiring: VP RevOps",
        "news: Opens Berlin office",
        "uses Salesforce, Segment",
    ]
    assert output["signal_source"] == "clay"


@pytest.mark.parametrize(
    "column", ["employee_count", "Employee Count", "employeeCount", "EMPLOYEE_COUNT"]
)
def test_column_naming_style_does_not_change_the_answer(column, clay_returns, spender):
    clay_returns(
        routine_result(
            {"company_name": "Northwind", "domain": "northwind.com", column: 420}
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["firmographics"]["employee_count"] == 420


# ------------------------------------------------------------ signal normaliser


def test_a_hand_written_signals_column_is_still_honoured(clay_returns, spender):
    """A workspace that already emits a `signals` column keeps working.

    A human's phrasing beats anything assembled here, so it leads the list.
    Note the record carries a company_name: without one the miss path swallows
    it and this asserts on nothing.
    """
    clay_returns(
        routine_result(
            {
                "company_name": "Northwind",
                "signals": [
                    "raised Series B",
                    {"label": "hired VP RevOps"},
                    {"type": "job_change"},
                ],
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["buying_signals"] == [
        "raised Series B",
        "hired VP RevOps",
        "job_change",
    ]
    assert output["signal_source"] == "clay"


def test_no_signals_is_an_answer_not_a_failure(clay_returns, spender):
    """"We looked and there is nothing" must not read as "we never looked".

    Since the merge these are two different answers from the same agent: an
    empty list means Clay matched the company and it is quiet, an absent key
    means Clay never matched it at all. Do not "simplify" the miss path into
    always publishing [] -- that collapses the distinction the planner reads.
    """
    clay_returns(routine_result({"company_name": "Northwind", "domain": "northwind.com"}))

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert output["buying_signals"] == []
    assert output["clay_enrichment"]["matched"] is True


@pytest.mark.parametrize(
    "raw, expected",
    [
        ([{"title": "VP RevOps", "posted_at": days_ago(3)}], ["hiring: VP RevOps"]),
        (["VP RevOps", "Enterprise AE"], ["hiring: VP RevOps", "hiring: Enterprise AE"]),
        ({"title": "VP RevOps"}, ["hiring: VP RevOps"]),          # forgot to wrap
        ("VP RevOps, Enterprise AE", ["hiring: VP RevOps", "hiring: Enterprise AE"]),
        (12, ["12 open roles"]),                                   # a count column
        ("12", ["12 open roles"]),
        (0, []),
        ([], []),
        (None, []),
    ],
)
def test_a_jobs_column_of_any_shape_becomes_hiring_phrases(raw, expected):
    assert clay._job_phrases({"job_openings": raw}, 180, 3) == expected


def test_past_the_cap_the_count_says_more_than_the_titles_would():
    raw = [{"title": f"Role {i}"} for i in range(9)]

    phrases = clay._job_phrases({"jobs": raw}, 180, 3)

    assert phrases == ["9 open roles", "hiring: Role 0", "hiring: Role 1"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({"round": "Series B", "amount": 30_000_000}, ["funding: raised Series B $30M"]),
        ({"round": "Series D", "amount": 1_200_000_000}, ["funding: raised Series D $1.2B"]),
        ({"round": "Seed", "amount": 750_000}, ["funding: raised Seed $750K"]),
        # "$30M" is already formatted; _as_int would read it as 30.
        ({"round": "Series B", "amount": "$30M"}, ["funding: raised Series B $30M"]),
        ({"round": "Series A"}, ["funding: raised Series A"]),
        ({"amount": 5_000_000}, ["funding: raised $5M"]),
        ("Series B, $30M, Jan 2025", ["funding: Series B, $30M, Jan 2025"]),
        (None, []),
        ([], []),
    ],
)
def test_a_funding_column_of_any_shape_becomes_one_phrase(raw, expected):
    assert clay._funding_phrases({"latest_funding": raw}, 180) == expected


def test_only_the_latest_round_is_a_signal():
    """The round before the latest is history, not a reason to call today."""
    rounds = [
        {"round": "Seed", "amount": 2_000_000, "date": days_ago(150)},
        {"round": "Series A", "amount": 12_000_000, "date": days_ago(20)},
    ]

    assert clay._funding_phrases({"funding_rounds": rounds}, 180) == [
        "funding: raised Series A $12M"
    ]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ([{"title": "Opens Berlin office", "date": days_ago(5)}],
         ["news: Opens Berlin office"]),
        (["Opens Berlin office"], ["news: Opens Berlin office"]),
        ({"headline": "Opens Berlin office"}, ["news: Opens Berlin office"]),
        (None, []),
    ],
)
def test_a_news_column_of_any_shape_becomes_news_phrases(raw, expected):
    assert clay._news_phrases({"recent_news": raw}, 180, 3) == expected


@pytest.mark.parametrize(
    "raw",
    [
        ["Salesforce", "Marketo", "Segment", "salesforce"],
        [{"name": "Salesforce"}, {"name": "Marketo"}, {"name": "Segment"}],
        "Salesforce, Marketo, Segment",
        "Salesforce; Marketo | Segment",
    ],
)
def test_technologies_collapse_to_one_phrase(raw):
    """A stack is context, not an event.

    One phrase per tool would let a long tech list out-vote a funding round on
    phrase count alone.
    """
    assert clay._tech_phrases({"tech_stack": raw}, 3) == ["uses Salesforce, Marketo, Segment"]


@pytest.mark.parametrize("age, kept", [(10, True), (179, True), (400, False)])
def test_stale_items_are_dropped_and_recent_ones_kept(age, kept):
    record = {
        "news": [{"title": "Opens Berlin office", "date": days_ago(age)}],
        "job_openings": [{"title": "VP RevOps", "posted_at": days_ago(age)}],
        "latest_funding": {"round": "Series B", "date": days_ago(age)},
    }

    signals = clay._buying_signals(record, POLICY)

    assert bool(signals) is kept


@pytest.mark.parametrize("stamp", [None, "", "sometime last spring", "not a date"])
def test_an_unparseable_date_is_kept_rather_than_dropped(stamp):
    """`_pluck`'s philosophy, applied to dates.

    Unreadable is not the same as old. Dropping these would delete real events
    over a formatting mismatch -- and read downstream as "the company is quiet".
    """
    record = {"news": [{"title": "Opens Berlin office", "date": stamp}]}

    assert clay._news_phrases(record, 180, 3) == ["news: Opens Berlin office"]


@pytest.mark.parametrize(
    "stamp",
    ["2026-03-14", "2026-03-14T09:00:00Z", "Mar 14, 2026", "March 14, 2026", "03/14/2026"],
)
def test_the_date_formats_a_clay_column_actually_emits_all_parse(stamp):
    assert clay._as_date(stamp) == date(2026, 3, 14)


def test_the_signal_order_puts_funding_before_hiring_before_news():
    """outreach_draft reads the list top-down looking for an opening line."""
    record = {
        "technologies": ["Segment"],
        "recent_news": ["Opens Berlin office"],
        "job_openings": ["VP RevOps"],
        "latest_funding": {"round": "Series B"},
        "signals": ["champion changed jobs"],
    }

    assert clay._buying_signals(record, POLICY) == [
        "champion changed jobs",
        "funding: raised Series B",
        "hiring: VP RevOps",
        "news: Opens Berlin office",
        "uses Segment",
    ]


def test_the_phrase_cap_comes_from_the_playbook(signals_policy):
    signals_policy({"recency_days": 180, "max_per_category": 1})
    record = {"recent_news": ["first", "second", "third"]}

    assert clay._news_phrases(record, 180, clay.signal_policy()["max_per_category"]) == [
        "news: first"
    ]


def test_the_recency_window_comes_from_the_playbook(signals_policy):
    signals_policy({"recency_days": 7, "max_per_category": 3})
    record = {"recent_news": [{"title": "Opens Berlin office", "date": days_ago(30)}]}

    assert clay._buying_signals(record, clay.signal_policy()) == []


def test_missing_signal_policy_costs_nothing(signals_policy, clay_returns):
    """A misconfigured playbook must fail free, not after paying Clay.

    The policy is read at the top of execute(), before the vendor call, so this
    reaches the planner as a free, re-plannable observation rather than a credit
    spent on a response the agent then cannot normalise.
    """
    signals_policy(None)
    calls = clay_returns(routine_result({"company_name": "Northwind"}))

    envelope = ClayEnrichAgent().run({"lead": LEAD})

    assert envelope["status"] == "error"
    assert calls == []
    assert envelope["cost_events"] == []


def test_raw_signal_blobs_never_reach_the_blackboard(clay_returns, spender):
    """The reason the normaliser is in Python at all.

    sales/server.py:56 serialises its ENTIRE payload into the outreach prompt.
    A forty-item job array with descriptions on the blackboard is re-tokenised
    at every later step and charged against the budget guardrail, so the raw
    columns must stay inside execute() and only the phrases go out.
    """
    filler = "a very long job description that must never reach a prompt " * 5
    clay_returns(
        routine_result(
            {
                "company_name": "Northwind",
                "domain": "northwind.com",
                "job_openings": [
                    {"title": f"Role {i}", "description": filler, "posted_at": days_ago(3)}
                    for i in range(40)
                ],
                "recent_news": [
                    {"title": "Opens Berlin office", "body": filler, "date": days_ago(3)}
                ],
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert set(output) == {
        "firmographics",
        "buying_signals",
        "signal_source",
        "clay_enrichment",
    }
    assert "very long job description" not in json.dumps(output)
    assert output["buying_signals"][0] == "40 open roles"


def test_one_routine_call_per_invocation(clay_returns, spender):
    """The merge's whole economic claim: one qualified lead is one Clay run."""
    calls = clay_returns(
        routine_result(
            {
                "company_name": "Northwind",
                "domain": "northwind.com",
                "latest_funding": {"round": "Series B", "date": days_ago(10)},
            }
        )
    )

    output = ClayEnrichAgent().execute({"lead": LEAD}, spender)

    assert len(calls) == 1
    assert output["firmographics"]["source"] == "clay"
    assert output["buying_signals"] == ["funding: raised Series B"]


# ------------------------------------------------------- handoff into scoring


def test_signals_survive_the_handoff_into_scoring(monkeypatch):
    """The silent-drop risk in this change.

    clay_enrich publishes buying_signals at the top level, while the old
    enrichment nested them inside firmographics. If ScoringAgent's schema does
    not declare the property, the orchestrator's pre-dispatch validation still
    passes and the signals simply never reach the prompt -- no error anywhere.
    """
    import jsonschema

    from core import llm as llm_module
    from conftest import result as llm_result
    from departments.revops.server import ScoringAgent

    payload = {
        "firmographics": {"company_name": "Northwind", "source": "clay"},
        "buying_signals": ["funding: raised Series B $30M"],
    }

    # Exactly the check core.orchestrator._invoke runs before dispatching.
    jsonschema.validate(payload, ScoringAgent.input_schema)

    seen: dict = {}

    def capture(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["instructions"] = kwargs.get("instructions", "")
        return llm_result(parsed={"fit_score": 80})

    monkeypatch.setattr(llm_module, "llm_call", capture)
    ScoringAgent().run(payload)

    assert "raised Series B" in seen["prompt"]
    # Reaching the prompt is not the same as being weighed. Without this the
    # signals arrive as unexplained JSON the scorer is free to ignore.
    assert "buying signal" in seen["instructions"].lower()
    assert "negative_signals" in seen["instructions"]
