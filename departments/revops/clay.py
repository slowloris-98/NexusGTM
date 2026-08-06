"""Clay agents. RevOps owns the data foundation, so Clay lives here.

Clay is the retrieval half of the enrichment waterfall. `marketing.enrichment`
infers firmographics from a model; these agents fetch them from Clay's provider
marketplace. Same output shape on purpose -- `scoring` and `outreach_draft`
consume either interchangeably -- with a `source` stamp so downstream work can
tell a retrieved headcount from a guessed one.

Three conventions worth knowing before editing:

    A miss is not an error. When Clay has no match, these agents return
    status="ok" with `clay_enrichment.matched = false` and deliberately do NOT
    write a `firmographics` key. Writing an empty one would satisfy `scoring`'s
    required field and let the planner score nothing at all; withholding it keeps
    scoring blocked until either Clay or the marketing fallback produces real
    data, while still recording the miss so the planner does not retry Clay.
    `buying_signals` is withheld on the same miss for the same reason: an empty
    list would assert an absence nobody checked. See `ClayEnrichAgent.execute`.

    Routines are workspace-defined. The routine ids and the column names a
    routine emits come from your Clay workspace, not from Clay's API. That is why
    ids are environment variables and why field reads go through `_pluck`, which
    accepts several plausible names. `_pluck` is the one place to adjust when your
    routine's columns differ.

    Signals are normalised in Python, not by a model. One routine returns
    firmographics AND the raw signal columns -- job postings, funding records,
    news items, a technology list -- and `_buying_signals` turns them into short
    phrases here. Two reasons, and the second is the load-bearing one. A model
    asked to summarise retrieved data guesses again, which is the thing Clay is
    here to remove. And the raw blobs must never reach the blackboard: forty job
    postings with descriptions on the board are re-tokenised at every later step,
    because `sales.outreach_draft` serialises its ENTIRE payload into a prompt
    and charges it against the budget guardrail. Only the phrases go out.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

from core.agent import Agent, Spender
from core.llm import strict_schema

from departments.revops.clients import CLAY_CREDIT_USD, ClayClient, VendorError

CONSUMER_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "proton.me",
    "protonmail.com",
    "icloud.com",
    "aol.com",
}


def _norm(key: str) -> str:
    """Reduce a column name to letters and digits, lowercased."""
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def _pluck(record: dict, *keys: str, default: Any = None) -> Any:
    """First non-empty value among several plausible column names.

    Clay routine outputs are named by whoever built the routine, so a single
    hardcoded key would break on someone else's workspace. Matching is done on
    the normalised name, because Clay's own enrichments emit display-style
    titles: "Employee Count", "employee_count", and "employeeCount" are the
    same field, and an exact lookup would miss two of the three silently --
    which reads downstream as "Clay had no data" rather than "we asked wrong".
    """
    if not isinstance(record, dict):
        return default

    normalized = {_norm(k): v for k, v in record.items()}
    for key in keys:
        value = normalized.get(_norm(key))
        if value not in (None, "", [], {}):
            return value
    return default


def _as_int(value: Any) -> int:
    """Coerce a headcount that may arrive as text.

    Clay output columns can be typed Text, and providers format headcounts as
    "1,200", "500+", or "420.0". A bare int() rejects all three and would fall
    back to 0 -- which is not a harmless default: scoring reads a zero-employee
    company, trips the under_10_employees negative signal, and disqualifies a
    good lead with a plausible-looking reason. Parse the leading number instead.
    """
    if isinstance(value, bool) or value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return int(value)

    digits = ""
    for char in str(value).strip():
        if char.isdigit():
            digits += char
        elif char in ",_ ":
            continue  # thousands separators
        elif char == "." and digits:
            break  # "420.7" -> 420, not 4207
        elif digits:
            break  # "500+", "1200 employees"
        else:
            continue  # leading "~", "$", ">"
    return int(digits) if digits else 0


def _domain_of(lead: dict, firmographics: dict | None = None) -> str:
    for candidate in (
        (firmographics or {}).get("domain"),
        lead.get("domain"),
        lead.get("website"),
    ):
        if candidate:
            return str(candidate).strip().lower().removeprefix("https://").removeprefix(
                "http://"
            ).removeprefix("www.").split("/")[0]
    email = str(lead.get("email") or "")
    return email.split("@")[-1].strip().lower() if "@" in email else ""


def _is_consumer_email(lead: dict) -> bool:
    """Computed, not inferred -- this is a set membership test, not a judgment."""
    email = str(lead.get("email") or "")
    if "@" not in email:
        return False
    return email.rsplit("@", 1)[-1].strip().lower() in CONSUMER_EMAIL_DOMAINS


def _bill_credits(llm: Spender, response: dict) -> None:
    """Charge Clay credits to the run's budget.

    Credits are real spend, and the budget guardrail halts on a total. Without
    this an agent could burn a vendor quota and report zero.

    Clay does not put a per-call credit count in the documented response, so
    this reads several plausible fields and falls back to the item count against
    CLAY_CREDIT_USD. An unset rate prices at zero, the same convention
    `core.llm.price_of` uses for a model missing from the table: report nothing
    rather than invent a number.
    """
    credits = _pluck(response, "credits_used", "credits", "creditsUsed", default=0)
    usage = response.get("usage") or {}
    if not credits and isinstance(usage, dict):
        credits = _pluck(usage, "credits_used", "credits", default=0)
    if not credits:
        credits = response.get("finished") or len(_records_of(response))
    try:
        billable = float(credits or 0)
    except (TypeError, ValueError):
        billable = 0.0
    # Gate on the priced amount, not the credit count: with no rate configured
    # every call would otherwise log a zero-dollar event and clutter the ledger.
    amount = billable * CLAY_CREDIT_USD
    if amount:
        llm.record_external(amount)


def _records_of(response: dict) -> list[dict]:
    """Pull the record dicts out of a Clay response.

    Routines and Searches both answer under `data`, but a routine wraps each row
    as `{id, status, result}` while a search returns the record directly. The
    `result` key is what tells them apart. A row Clay marks `failed` is dropped:
    a partial answer is still an answer, and the miss path handles an empty list.
    """
    rows = response.get("data")
    if not isinstance(rows, list):
        return []

    records = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "result" in row:
            if row.get("status") == "failed":
                continue
            result = row.get("result")
            if isinstance(result, dict):
                records.append(result)
        else:
            records.append(row)
    return records


# --------------------------------------------------------------- signal columns
# Aliases, same rationale as `_pluck`'s: whoever built the routine named these
# columns, so a single hardcoded key would break on someone else's workspace.

JOB_KEYS = (
    "job_openings", "open_roles", "job_postings", "jobs", "open_positions",
    "hiring", "job_openings_count", "open_roles_count", "jobs_count", "roles",
)
JOB_TITLE_KEYS = ("title", "job_title", "role", "position", "name")
JOB_DATE_KEYS = ("posted_at", "posted", "posted_date", "date", "created_at",
                 "published_at", "first_seen")

FUNDING_KEYS = (
    "latest_funding", "last_funding", "funding", "funding_round",
    "latest_funding_round", "funding_rounds", "recent_funding",
    "last_funding_round", "investment",
)
FUNDING_ROUND_KEYS = ("round", "round_type", "series", "stage", "type", "name")
FUNDING_AMOUNT_KEYS = ("amount", "amount_usd", "raised", "size", "funding_amount",
                       "value", "total_raised")
FUNDING_DATE_KEYS = ("date", "announced_at", "announced_date", "announced_on",
                     "closed_on", "posted_at", "funding_date")

NEWS_KEYS = ("recent_news", "news", "latest_news", "news_items", "press",
             "press_mentions", "articles", "headlines", "news_articles")
NEWS_TITLE_KEYS = ("title", "headline", "name", "summary", "text")
NEWS_DATE_KEYS = ("date", "published_at", "published", "published_date",
                  "posted_at", "created_at")

TECH_KEYS = ("technologies", "technology", "tech_stack", "stack", "tools",
             "technographics", "software", "tech")
TECH_NAME_KEYS = ("name", "technology", "tool", "title", "product")

EXPLICIT_KEYS = ("signals", "buying_signals", "events")

# Presentation, not policy -- the same category as queue names and segments,
# which stay in the agent's own contract rather than the playbook. One headline
# stays one line in a prompt.
SIGNAL_PHRASE_MAX_CHARS = 140


def _as_date(value: Any) -> date | None:
    """Best-effort date from a loosely typed Clay column, or None.

    Unreadable is not the same as old. `_pluck` exists because "we asked wrong"
    must not read downstream as "Clay had no data"; the same rule applies to a
    date format nobody anticipated, so an unparseable date is None -- and None
    survives the recency filter rather than silently deleting a real event.
    """
    if value in (None, "", [], {}) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / (1000.0 if abs(float(value)) > 1e11 else 1.0)
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y",
                "%B %d, %Y", "%d %b %Y", "%b %Y", "%B %Y", "%Y-%m"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _within(when: date | None, days: int) -> bool:
    """A dated item ages out; an undated one does not.

    Dropping stale events is not tidiness -- `outreach_draft` opens with whatever
    it is handed, and congratulating someone on a round they closed eleven months
    ago is worse than saying nothing. But dropping UNdated items would delete
    real events over a formatting mismatch, which is the failure `_as_date`
    returns None for in the first place.
    """
    return True if when is None else (date.today() - when).days <= days


def _phrase(text: Any) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= SIGNAL_PHRASE_MAX_CHARS:
        return cleaned
    return cleaned[: SIGNAL_PHRASE_MAX_CHARS - 3].rstrip() + "..."


def _split_list(text: str) -> list[str]:
    """A Clay column typed Text holding a list: "Salesforce, Marketo; Segment"."""
    for separator in ("\n", ";", "|", "/"):
        text = text.replace(separator, ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def _dedupe(phrases: list[str]) -> list[str]:
    seen: set[str] = set()
    unique = []
    for phrase in phrases:
        if phrase and phrase.lower() not in seen:
            seen.add(phrase.lower())
            unique.append(phrase)
    return unique


def _dated_items(
    raw: Any, title_keys: tuple[str, ...], date_keys: tuple[str, ...]
) -> list[tuple[str, date | None]]:
    """Normalise a jobs- or news-shaped column into (label, date) pairs.

    One function for both because the shapes Clay emits are the same three: a
    list of dicts, a list of strings, or a single dict someone forgot to wrap.
    """
    if raw in (None, "", [], {}):
        return []
    if isinstance(raw, dict):
        raw = [raw]
    elif isinstance(raw, str):
        raw = _split_list(raw)
    if not isinstance(raw, list):
        return []

    items = []
    for entry in raw:
        if isinstance(entry, dict):
            label = _phrase(_pluck(entry, *title_keys, default=""))
            when = _as_date(_pluck(entry, *date_keys))
        else:
            label, when = _phrase(entry), None
        if label:
            items.append((label, when))
    return items


def _recent_first(
    items: list[tuple[str, date | None]], days: int
) -> list[tuple[str, date | None]]:
    fresh = [item for item in items if _within(item[1], days)]
    # Undated items sort last rather than jumping the queue on a date never read.
    fresh.sort(key=lambda item: item[1] or date.min, reverse=True)
    return fresh


def _money(value: Any) -> str:
    """Format a raised amount; a pre-formatted string is trusted as-is.

    `_as_int("$30M")` is 30, and "raised $30" is a worse answer than the string
    the provider already wrote -- so only a real number gets humanised here.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        amount = float(value)
        for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
            if abs(amount) >= size:
                text = f"{amount / size:.1f}"
                return f"${text[:-2] if text.endswith('.0') else text}{unit}"
        return f"${int(amount)}"
    return _phrase(value)


def _explicit_phrases(record: dict) -> list[str]:
    """A workspace that already emits a `signals` column still works.

    Kept from the agent this one absorbed. A hand-authored column is a human's
    phrasing and beats anything assembled here, so it leads the list.
    """
    found = _pluck(record, *EXPLICIT_KEYS, default=[])
    if isinstance(found, str):
        return [_phrase(found)]

    phrases = []
    for item in found if isinstance(found, list) else []:
        if isinstance(item, str):
            phrases.append(_phrase(item))
        elif isinstance(item, dict):
            label = _pluck(item, "label", "type", "name", "signal", "title")
            if label:
                phrases.append(_phrase(label))
    return [phrase for phrase in phrases if phrase]


def _funding_phrases(record: dict, window: int) -> list[str]:
    """At most one phrase: the round before the latest is history, not a signal."""
    raw = _pluck(record, *FUNDING_KEYS)
    if raw in (None, "", [], {}):
        return []
    if isinstance(raw, str):
        # Already prose ("Series B, $30M, Jan 2025"). Splitting it means guessing.
        return [f"funding: {_phrase(raw)}"]

    rounds = [raw] if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    dated: list[tuple[str, date | None]] = []
    for entry in rounds:
        if isinstance(entry, str) and entry.strip():
            dated.append((f"funding: {_phrase(entry)}", None))
            continue
        if not isinstance(entry, dict):
            continue
        amount = _pluck(entry, *FUNDING_AMOUNT_KEYS)
        parts = [
            part
            for part in (
                _phrase(_pluck(entry, *FUNDING_ROUND_KEYS, default="")),
                _money(amount) if amount not in (None, "") else "",
            )
            if part
        ]
        if parts:
            dated.append(
                (f"funding: raised {' '.join(parts)}",
                 _as_date(_pluck(entry, *FUNDING_DATE_KEYS)))
            )

    return [label for label, _ in _recent_first(dated, window)[:1]]


def _job_phrases(record: dict, window: int, cap: int) -> list[str]:
    raw = _pluck(record, *JOB_KEYS)
    if raw in (None, "", [], {}):
        return []

    # A bare count is a legitimate configuration -- "how many roles are open" is
    # one of Clay's cheapest job enrichments -- and it carries no titles.
    counted = (isinstance(raw, (int, float)) and not isinstance(raw, bool)) or (
        isinstance(raw, str) and not any(char.isalpha() for char in raw)
    )
    if counted:
        openings = _as_int(raw)
        return [f"{openings} open roles"] if openings else []

    titles = [
        f"hiring: {label}"
        for label, _ in _recent_first(
            _dated_items(raw, JOB_TITLE_KEYS, JOB_DATE_KEYS), window
        )
    ]
    if len(titles) > cap:
        # Past the cap the count says more than three more titles would.
        return [f"{len(titles)} open roles", *titles[: cap - 1]]
    return titles


def _news_phrases(record: dict, window: int, cap: int) -> list[str]:
    items = _recent_first(
        _dated_items(_pluck(record, *NEWS_KEYS), NEWS_TITLE_KEYS, NEWS_DATE_KEYS),
        window,
    )
    return [f"news: {label}" for label, _ in items[:cap]]


def _tech_phrases(record: dict, cap: int) -> list[str]:
    raw = _pluck(record, *TECH_KEYS)
    if isinstance(raw, str):
        names: list[Any] = _split_list(raw)
    elif isinstance(raw, dict):
        names = [_pluck(raw, *TECH_NAME_KEYS, default="")]
    elif isinstance(raw, list):
        names = [
            _pluck(item, *TECH_NAME_KEYS, default="") if isinstance(item, dict) else item
            for item in raw
        ]
    else:
        return []

    names = _dedupe([_phrase(name) for name in names if name])
    # One phrase, not one per tool: a stack is context rather than an event, and
    # it should not out-vote a funding round on phrase count alone.
    return [f"uses {', '.join(names[:cap])}"] if names else []


def _buying_signals(record: dict, policy: dict) -> list[str]:
    """Raw Clay columns -> short phrases. Deterministic, and deliberately so.

    Clay's AI columns hit a token limit synthesising this inside the workspace,
    so the synthesis moved here -- but it moved into Python, not into an LLM
    call. See the module docstring for why that distinction is load-bearing.

    Order is strongest-first and matters: `outreach_draft` reads the list
    top-down looking for an opening line.
    """
    window, cap = policy["recency_days"], policy["max_per_category"]
    return _dedupe(
        [
            *_explicit_phrases(record),
            *_funding_phrases(record, window),
            *_job_phrases(record, window, cap),
            *_news_phrases(record, window, cap),
            *_tech_phrases(record, cap),
        ]
    )


class ClayEnrichAgent(Agent):
    department = "revops"
    name = "clay_enrich"
    description = (
        "Look up a company in Clay in ONE call: real firmographics -- industry, "
        "employee count, revenue band, HQ region -- plus live buying signals "
        "(funding rounds, hiring, recent news, technology adoption) normalised "
        "into short phrases. Firmographics are retrieved from data providers and "
        "signals are monitored events, so no model can produce either: prefer "
        "this over 'enrichment', which only estimates firmographics and returns "
        "no signals at all. Signals are published as top-level buying_signals "
        "for 'scoring'. Takes a lead, or an account when the subject is a "
        "customer we already serve -- it keys off the domain either way. Returns "
        "clay_enrichment.matched=false when Clay has no record for the company: "
        "a normal result, and the cue to fall back to 'enrichment'. Never run "
        "this twice in a run, and never run 'enrichment' once this one matched."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "lead": {
                "type": "object",
                "description": "Raw lead as captured: name, email, company, source.",
            },
            "account": {
                "type": "object",
                "description": (
                    "An account we already serve, when there is no lead. Must "
                    "carry a domain."
                ),
            },
        },
        # Either will do, so neither is individually required; the check is in
        # execute(), where "at least one of" can actually be said -- the same
        # reason routing and crm_lookup declare no required properties.
        "required": [],
    }

    # Declared as the published output contract, not fed to a model: this agent
    # maps a vendor response, and running retrieved data back through an LLM
    # would reintroduce exactly the guessing Clay is here to remove. `required`
    # is written out rather than left to strict mode's every-property rule,
    # because on a miss only clay_enrichment is published -- and a schema that
    # never reaches a provider should say what is guaranteed, not what strict
    # mode would demand.
    output_schema = strict_schema(
        "clay_enrichment",
        {
            "firmographics": {
                "type": "object",
                "description": "Absent on a miss, deliberately -- see the module docstring.",
                "properties": {
                    "company_name": {"type": "string"},
                    "domain": {"type": "string"},
                    "industry": {"type": "string"},
                    "employee_count": {"type": "integer"},
                    "revenue_band": {"type": "string"},
                    "hq_region": {"type": "string"},
                    "free_email_domain": {"type": "boolean"},
                    "source": {"type": "string", "description": "Always 'clay'."},
                },
            },
            "buying_signals": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Short normalised phrases: 'funding: raised Series B $30M', "
                    "'hiring: VP Revenue Operations', '12 open roles', 'news: "
                    "...', 'uses Salesforce, Segment'. Top level, where scoring "
                    "declares it. Empty means Clay matched the company and found "
                    "nothing; absent means Clay never matched it."
                ),
            },
            "signal_source": {"type": "string", "description": "Always 'clay'."},
            "clay_enrichment": {
                "type": "object",
                "properties": {
                    "matched": {"type": "boolean"},
                    "reason": {"type": "string", "description": "Empty on a match."},
                },
            },
        },
        required=["clay_enrichment"],
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        subject = payload.get("lead") or payload.get("account")
        if not subject:
            raise ValueError("payload needs a lead or an account to look up")

        # Read before the vendor call, not after: a playbook missing the signals
        # block should cost nothing, rather than a Clay credit for a response
        # this agent is then unable to normalise.
        policy = signal_policy()

        domain = _domain_of(subject)
        if not domain or domain in CONSUMER_EMAIL_DOMAINS:
            # Nothing to look a company up by. A consumer address is itself the
            # answer, so report the miss rather than paying Clay for it. No
            # buying_signals key either -- we never looked, so we cannot say.
            return {
                "clay_enrichment": {
                    "matched": False,
                    "reason": (
                        "no company domain on the lead"
                        if not domain
                        else f"consumer email domain ({domain})"
                    ),
                }
            }

        client = ClayClient()
        response = client.run_routine(
            os.getenv("CLAY_ENRICH_ROUTINE_ID", ""),
            [
                {
                    "domain": domain,
                    "company": subject.get("company") or subject.get("name"),
                    "email": subject.get("email"),
                }
            ],
        )
        _bill_credits(llm, response)

        records = _records_of(response)
        record = records[0] if records else {}
        if not record or not _pluck(record, "company_name", "name", "companyName"):
            return {
                "clay_enrichment": {
                    "matched": False,
                    "reason": f"Clay returned no match for {domain}",
                }
            }

        # Aliases cover Clay's own enrichment titles alongside the names this
        # system asks for, so a function wired straight from "Enrich Company"
        # works without renaming every output column by hand.
        employees = _as_int(
            _pluck(record, "employee_count", "employees", "headcount", "size", default=0)
        )

        return {
            "firmographics": {
                "company_name": _pluck(record, "company_name", "name", "companyName", default=""),
                "domain": _pluck(record, "domain", "website", "url", default=domain),
                "industry": _pluck(record, "industry", "sector", default=""),
                "employee_count": employees,
                "revenue_band": _pluck(
                    record,
                    "revenue_band",
                    "revenue",
                    "annual_revenue",
                    "estimated_annual_revenue",
                    default="",
                ),
                "hq_region": _pluck(
                    record,
                    "hq_region",
                    "region",
                    "headquarters",
                    "hq_location",
                    "location",
                    "country",
                    default="",
                ),
                "free_email_domain": _is_consumer_email(subject),
                "source": "clay",
            },
            # An empty list here is a real answer -- "we looked at this company
            # and there is nothing" -- and the planner should not retry. That is
            # a different statement from the miss paths above, which withhold the
            # key because Clay never identified the company to look at.
            #
            # The raw signal columns stay in this frame and are never published:
            # a jobs array carries forty postings with descriptions, and
            # sales.outreach_draft serialises its ENTIRE payload into a prompt.
            "buying_signals": _buying_signals(record, policy),
            "signal_source": "clay",
            "clay_enrichment": {"matched": True, "reason": ""},
        }


class ClaySourceAgent(Agent):
    department = "revops"
    name = "clay_source"
    description = (
        "Source net-new accounts matching an ICP brief from Clay's GTM database. "
        "Use this to start an outbound run that has no lead yet. Returns the "
        "candidate list plus the strongest candidate promoted to 'lead', so the "
        "rest of the flow proceeds normally. Requires an icp_brief; do not call "
        "this when a lead already exists."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "icp_brief": {
                "type": "string",
                "description": "Prose description of the accounts to find.",
            },
            "limit": {"type": "integer", "description": "Max accounts to return."},
        },
        "required": ["icp_brief"],
    }

    # The brief is prose and Clay Search takes structured filters, so this is the
    # one place a model belongs in the Clay path: translating intent into filters.
    # It never invents company data -- Clay supplies that.
    filter_schema = strict_schema(
        "search_filters",
        {
            "industries": {"type": "array", "items": {"type": "string"}},
            "employee_count_min": {"type": "integer"},
            "employee_count_max": {"type": "integer"},
            "regions": {"type": "array", "items": {"type": "string"}},
            "keywords": {"type": "array", "items": {"type": "string"}},
        },
    )

    output_schema = strict_schema(
        "sourced_accounts",
        {
            "sourced_accounts": {"type": "array", "items": {"type": "object"}},
            "lead": {"type": "object"},
        },
    )

    def execute(self, payload: dict, llm: Spender) -> dict:
        brief = (payload.get("icp_brief") or "").strip()
        if not brief:
            raise ValueError("payload.icp_brief is required")

        limit = int(payload.get("limit") or default_source_limit())

        translated = llm(
            brief,
            instructions=(
                "You translate a go-to-market ICP brief into structured search "
                "filters for a company database. Extract only what the brief "
                "states or clearly implies. Leave a field empty rather than "
                "inventing a constraint the brief does not support."
            ),
            schema=self.filter_schema,
        )

        client = ClayClient()
        response = client.search(translated.parsed or {}, limit)
        _bill_credits(llm, response)

        accounts = []
        for record in _records_of(response)[:limit]:
            accounts.append(
                {
                    "company": _pluck(record, "company_name", "name", "companyName", default=""),
                    "domain": _pluck(record, "domain", "website", default=""),
                    "industry": _pluck(record, "industry", "sector", default=""),
                    "employee_count": _pluck(record, "employee_count", "employees", default=None),
                    "hq_region": _pluck(record, "hq_region", "region", "country", default=""),
                }
            )

        if not accounts:
            raise VendorError(f"Clay search returned no accounts for: {brief[:120]}")

        # One orchestration qualifies one lead. The full list stays on the
        # blackboard for visibility; fanning it out into N runs is a separate
        # feature, not something to smuggle in here.
        top = accounts[0]
        return {
            "sourced_accounts": accounts,
            "lead": {
                "company": top["company"],
                "domain": top["domain"],
                "source": "clay_source",
                "note": f"sourced from ICP brief: {brief[:200]}",
            },
        }


def default_source_limit() -> int:
    """Sourcing breadth is policy, so it lives in the playbook.

    Missing keys raise rather than falling back to a literal -- a default here
    would be the duplicated constant this indirection exists to remove.
    """
    from core.config import playbook

    return int(playbook()["sourcing"]["limit"])


def signal_policy() -> dict:
    """Signal recency and phrase caps are policy, so they live in the playbook.

    Same contract as `default_source_limit`: missing keys raise. `clay_enrich`
    calls this before the vendor call, so a playbook without a `signals:` block
    fails free and fast instead of paying Clay for a response it cannot then
    normalise. The failure reaches the planner as an observation like any other.
    """
    from core.config import playbook

    block = playbook()["signals"]
    window, cap = int(block["recency_days"]), int(block["max_per_category"])
    if window < 1 or cap < 1:
        raise ValueError("signals.recency_days and signals.max_per_category must be >= 1")
    return {"recency_days": window, "max_per_category": cap}
