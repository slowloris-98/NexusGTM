import { api, usd, when } from "../api";
import { Empty, Panel, StatusBadge, Tile, useAsync } from "../components";

export function Live({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, error } = useAsync(() => api.orchestrations(), [], 4000);
  const { data: depts } = useAsync(() => api.departments(), [], 15000);

  if (error) return <Empty>Cannot reach the API: {error}</Empty>;
  const rows = data ?? [];
  const inFlight = rows.filter((r) => r.status === "running");
  const today = rows.filter(
    (r) => new Date(r.started_at).toDateString() === new Date().toDateString(),
  );

  return (
    <>
      {depts?.unreachable?.length ? (
        <div className="banner">
          <strong>Partial action space.</strong> Unreachable:{" "}
          {depts.unreachable.map((d) => `${d.name} (${d.endpoint})`).join(", ")}. The
          planner is running with the departments that answered.
        </div>
      ) : null}

      <div className="tiles">
        <Tile label="In flight" value={String(inFlight.length)} />
        <Tile label="Started today" value={String(today.length)} />
        <Tile
          label="Spend today"
          value={usd(today.reduce((s, r) => s + (r.total_cost_usd ?? 0), 0))}
        />
        <Tile
          label="Departments live"
          value={String(depts?.departments.length ?? 0)}
          note={`${depts?.departments.reduce((s, d) => s + d.agents.length, 0) ?? 0} agents registered`}
        />
      </div>

      <Panel title="In flight" hint="Polled every 4 seconds.">
        {inFlight.length === 0 ? (
          <Empty>Nothing running right now.</Empty>
        ) : (
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Current stage</th>
                  <th className="num">Steps</th>
                  <th className="num">Spent</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {inFlight.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => onOpen(r.id)}>
                    <td className="mono">{r.crm_reference_id}</td>
                    <td>{r.current_stage ?? "planning"}</td>
                    <td className="num">{r.step_count}</td>
                    <td className="num">{usd(r.total_cost_usd)}</td>
                    <td>{when(r.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Registered departments" hint="Built from the registry — new departments appear here automatically.">
        <div className="dept-grid">
          {(depts?.departments ?? []).map((d) => (
            <div key={d.id} className="dept">
              <div className="name">{d.name}</div>
              <div className="endpoint mono">{d.endpoint}</div>
              <ul>
                {d.agents.map((a) => (
                  <li key={a.name}>{a.name}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Recent" hint="Most recent orchestrations, whatever their status.">
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
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 12).map((r) => (
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
