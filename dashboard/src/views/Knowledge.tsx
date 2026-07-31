import { useState } from "react";
import { api, usd, when } from "../api";
import { Empty, Panel, StatusBadge, useAsync } from "../components";

/** Plain-text search across past runs. The store is also the knowledge base. */
export function Knowledge({ onOpen }: { onOpen: (id: string) => void }) {
  const [term, setTerm] = useState("");
  const [query, setQuery] = useState("");

  const { data, error } = useAsync(
    () => (query ? api.search(query) : Promise.resolve([])),
    [query],
  );

  return (
    <>
      <form
        className="controls"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(term.trim());
        }}
      >
        <input
          type="search"
          value={term}
          placeholder="Search past runs — company, industry, rationale…"
          onChange={(e) => setTerm(e.target.value)}
        />
        <button className="back" type="submit" style={{ marginBottom: 0 }}>
          Search
        </button>
      </form>

      <Panel title="Knowledge base" hint="Every agent input and output ever recorded.">
        {error && <Empty>Search failed: {error}</Empty>}
        {!query && <Empty>Enter a term to search across every past run.</Empty>}
        {query && data?.length === 0 && <Empty>No runs match “{query}”.</Empty>}

        {data && data.length > 0 && (
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Agent</th>
                  <th>Status</th>
                  <th>Outcome</th>
                  <th className="num">Cost</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {data.map((hit) => (
                  <tr
                    key={hit.id}
                    className="clickable"
                    onClick={() => onOpen(hit.orchestration_id)}
                  >
                    <td className="mono">{hit.crm_reference_id}</td>
                    <td>
                      {hit.department} / {hit.agent}
                    </td>
                    <td>
                      <StatusBadge status={hit.status} />
                    </td>
                    <td>
                      <StatusBadge status={hit.outcome} />
                    </td>
                    <td className="num">{usd(hit.cost_usd)}</td>
                    <td>{when(hit.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
