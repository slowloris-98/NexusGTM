import { api, usd, when } from "../api";
import { Empty, Panel, StatusBadge, useAsync } from "../components";
import { Trail } from "./DecisionTrail";

export function Results({
  selected,
  onOpen,
  onBack,
}: {
  selected: string | null;
  onOpen: (id: string) => void;
  onBack: () => void;
}) {
  if (selected) return <Detail id={selected} onBack={onBack} />;
  return <List onOpen={onOpen} />;
}

function List({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, error } = useAsync(() => api.orchestrations(), []);
  if (error) return <Empty>Cannot reach the API: {error}</Empty>;

  return (
    <Panel title="Results" hint="Every orchestration, its outcome, and what it cost. Click a row for the full trail.">
      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              <th>Account</th>
              <th>Status</th>
              <th>Outcome</th>
              <th className="num">Steps</th>
              <th className="num">Cost</th>
              <th>Started</th>
              <th>Finished</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((r) => (
              <tr key={r.id} className="clickable" onClick={() => onOpen(r.id)}>
                <td className="mono">{r.crm_reference_id}</td>
                <td>
                  <StatusBadge status={r.status} />
                </td>
                <td>
                  <StatusBadge status={r.outcome} />
                </td>
                <td className="num">{r.step_count}</td>
                <td className="num">{usd(r.total_cost_usd)}</td>
                <td>{when(r.started_at)}</td>
                <td>{when(r.finished_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data?.length === 0 && <Empty>No orchestrations yet. Run scripts/seed.py.</Empty>}
    </Panel>
  );
}

function Detail({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, error } = useAsync(() => api.orchestration(id), [id]);
  if (error) return <Empty>Cannot load orchestration: {error}</Empty>;
  if (!data) return <Empty>Loading…</Empty>;

  return (
    <>
      <button className="back" onClick={onBack}>
        ← All results
      </button>

      <Panel title={`Orchestration · ${data.crm_reference_id}`}>
        <div className="tiles" style={{ marginBottom: 0 }}>
          <div className="tile">
            <div className="label">Status</div>
            <div style={{ marginTop: 4 }}>
              <StatusBadge status={data.status} />
            </div>
          </div>
          <div className="tile">
            <div className="label">Outcome</div>
            <div style={{ marginTop: 4 }}>
              <StatusBadge status={data.outcome} />
            </div>
          </div>
          <div className="tile">
            <div className="label">Total LLM cost</div>
            <div className="value">{usd(data.total_cost_usd)}</div>
          </div>
          <div className="tile">
            <div className="label">Steps run</div>
            <div className="value">{data.runs.length}</div>
          </div>
        </div>
      </Panel>

      <Panel title="Decision trail" hint="Why the planner chose each agent. Paths vary per run, so this is how the path is audited.">
        <Trail decisions={data.decisions} />
      </Panel>

      <Panel title="Agent runs" hint="What each agent received, what it returned, and what it cost.">
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Department</th>
                <th>Agent</th>
                <th>Status</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {data.runs.map((run) => (
                <tr key={run.id}>
                  <td className="num">{run.step_no}</td>
                  <td>{run.department}</td>
                  <td>{run.agent}</td>
                  <td>
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="num">{usd(run.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Final result" hint="The blackboard as it stood when the run terminated.">
        <pre className="payload">{JSON.stringify(data.final_result, null, 2)}</pre>
      </Panel>
    </>
  );
}
