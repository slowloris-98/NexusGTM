import { useCallback, useEffect, useMemo, useState } from "react";
import { api, plural, type SearchHit } from "./api";
import { useAsync } from "./components";
import { Rail, type Selection } from "./Rail";
import { Briefing } from "./panes/Briefing";
import { Department } from "./panes/Department";
import { Orchestration } from "./panes/Orchestration";
import { SystemMap } from "./panes/SystemMap";
import { Spend } from "./panes/Spend";
import { activeTabKey, tabsFor } from "./tabs";
import { useTheme, type Theme } from "./theme";
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
  const [theme, toggleTheme] = useTheme();

  const { data: rowData, error: rowError } = useAsync(() => api.orchestrations(), [], 4000);
  const { data: depts } = useAsync(() => api.departments(), [], 15000);
  // Config-derived rather than registry-derived, so it changes only when someone
  // edits the playbook and restarts. A slow poll is the honest cadence.
  const { data: flows } = useAsync(() => api.flows(), [], 60000);

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

  const tabs = tabsFor(depts);
  const activeTab = activeTabKey(selection);

  // The map and the department panes are whole-screen views. The rail is Home's
  // instrument -- a list of orchestrations -- and keeping it beside a constellation
  // or a set of trigger buttons is a column of the wrong question. Unmounted rather
  // than hidden, so nothing behind the pane keeps polling.
  const railHidden = selection.kind === "map" || selection.kind === "department";
  // Under 900px the pane is a fixed overlay that only `detail-open` reveals, so a
  // whole-screen tab has to open it the same way an orchestration does.
  const detailOpen = selection.kind === "orchestration" || railHidden;

  return (
    <div
      className={`console${detailOpen ? " detail-open" : ""}${railHidden ? " rail-collapsed" : ""}`}
    >
      <header className="systembar">
        <h1 className="wordmark">NexusGTM</h1>
        <nav className="tabs" aria-label="Sections">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`tab${tab.down ? " down" : ""}`}
              aria-current={activeTab === tab.key}
              onClick={() => setSelection(tab.selection)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <ThemeToggle theme={theme} onToggle={toggleTheme} />
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

      {!railHidden && (
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
        />
      )}

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
          ) : selection.kind === "department" ? (
            <Department
              id={selection.id}
              depts={depts}
              flows={flows}
              rows={rows}
              onOpen={openOrchestration}
            />
          ) : (
            <Orchestration id={selection.id} depts={depts} />
          )}
        </div>
      </main>
    </div>
  );
}

/**
 * Light is the console's default and dark is the reader's choice, so this is one button
 * with two states rather than a three-way including "system" -- there is no system state
 * to return to.
 *
 * Named for what pressing it does, not for what is currently on. aria-pressed is
 * deliberately absent: a toggle button that announces both a changing label and a pressed
 * state reads as contradictory ("switch to dark, pressed"), and the label alone is
 * unambiguous.
 */
function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  const target = theme === "dark" ? "light" : "dark";
  const label = `Switch to ${target} mode`;
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={onToggle}
      aria-label={label}
      title={label}
    >
      {theme === "dark" ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="4.5" />
          <path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M20.5 14.4A8.5 8.5 0 1 1 9.6 3.5a6.8 6.8 0 0 0 10.9 10.9z" />
        </svg>
      )}
    </button>
  );
}
