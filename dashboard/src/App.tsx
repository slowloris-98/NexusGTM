import { useCallback, useEffect, useMemo, useState } from "react";
import { api, plural, type SearchHit } from "./api";
import { useAsync } from "./components";
import { Rail, type Selection } from "./Rail";
import { Briefing } from "./panes/Briefing";
import { Orchestration } from "./panes/Orchestration";
import { SystemMap } from "./panes/SystemMap";
import { Spend } from "./panes/Spend";
import { needsAttention } from "./vocabulary";

/**
 * One screen. A persistent rail of every orchestration, and a pane holding whatever
 * is selected -- with the unselected state carrying the day's answer rather than
 * sitting empty.
 */
export default function App() {
  const [selection, setSelection] = useState<Selection>({ kind: "briefing" });
  const [term, setTerm] = useState("");
  const [query, setQuery] = useState("");

  const { data: rowData, error: rowError } = useAsync(() => api.orchestrations(), [], 4000);
  const { data: depts } = useAsync(() => api.departments(), [], 15000);

  const rows = useMemo(() => rowData ?? [], [rowData]);

  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);

  useEffect(() => {
    if (!query) {
      setHits(null);
      setSearchError(null);
      return;
    }
    let alive = true;
    api
      .search(query)
      .then((result) => {
        if (!alive) return;
        setHits(result);
        setSearchError(null);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setSearchError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, [query]);

  /** Typing narrows the rail instantly; Enter sends the term to the store's search. */
  const filtered = useMemo(() => {
    const needle = term.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((r) => r.crm_reference_id.toLowerCase().includes(needle));
  }, [rows, term]);

  const attentionCount = rows.filter((r) => needsAttention(r.status)).length;
  const spendToday = rowData
    ? rows
        .filter((r) => new Date(r.started_at).toDateString() === new Date().toDateString())
        .reduce((sum, r) => sum + (r.total_cost_usd ?? 0), 0)
    : null;

  const openOrchestration = useCallback(
    (id: string) => setSelection({ kind: "orchestration", id }),
    [],
  );

  const unreachable = depts?.unreachable ?? [];
  const deptCount = depts?.departments.length ?? 0;
  const agentCount = depts?.departments.reduce((sum, d) => sum + d.agents.length, 0) ?? 0;

  return (
    <div className={`console${selection.kind === "orchestration" ? " detail-open" : ""}`}>
      <header className="systembar">
        <h1 className="wordmark">NexusGTM</h1>
        <span className="spacer" />
        {depts && (
          <span className={`health${unreachable.length ? " degraded" : ""}`}>
            <span
              className={`dot ${unreachable.length ? "attention" : "good"}`}
              aria-hidden="true"
            />
            <span className="text">
              {unreachable.length
                ? `${unreachable.map((d) => d.name).join(", ")} not answering`
                : `${plural(deptCount, "department")} answering`}
            </span>
          </span>
        )}
      </header>

      <Rail
        rows={filtered}
        term={term}
        onTerm={setTerm}
        onSearch={setQuery}
        searchHits={hits}
        searchError={searchError}
        selection={selection}
        onSelect={setSelection}
        spendToday={spendToday}
        attentionCount={attentionCount}
        agentCount={agentCount}
      />

      <main className="pane" aria-live="polite">
        <div className={`pane-inner${selection.kind === "map" ? " is-map" : ""}`}>
          <button
            type="button"
            className="pane-back"
            onClick={() => setSelection({ kind: "briefing" })}
          >
            ← All leads
          </button>

          {rowError && selection.kind === "briefing" ? (
            <>
              <header className="pane-head">
                <h1>Cannot reach the control plane API</h1>
              </header>
              <p className="fault-note">{rowError}</p>
              <p className="gloss">
                Start it with{" "}
                <span className="mono">python -m uvicorn api.main:app --port 8000</span>.
                Orchestrations already recorded are unaffected — this screen only reads.
              </p>
            </>
          ) : selection.kind === "briefing" ? (
            <Briefing
              rows={rows}
              depts={depts}
              onOpen={openOrchestration}
              onSpend={() => setSelection({ kind: "spend" })}
            />
          ) : selection.kind === "spend" ? (
            <Spend />
          ) : selection.kind === "map" ? (
            <SystemMap depts={depts} rows={rows} />
          ) : (
            <Orchestration id={selection.id} />
          )}
        </div>
      </main>
    </div>
  );
}
