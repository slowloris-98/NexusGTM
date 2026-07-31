import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, usd } from "../api";
import { ChartTooltip, Empty, Panel, Tile, useAsync } from "../components";

const AXIS = { fill: "var(--muted)", fontSize: 11 };

export function Cost() {
  const { data, error } = useAsync(() => api.costs(), [], 10000);
  if (error) return <Empty>Cannot reach the API: {error}</Empty>;
  if (!data) return <Empty>Loading…</Empty>;

  const totalSpend = data.by_agent.reduce((s, a) => s + a.total_cost_usd, 0);
  const qualified = data.per_outcome.find((o) => o.outcome === "qualified");
  const allRuns = data.per_outcome.reduce((s, o) => s + o.count, 0);

  const byAgent = [...data.by_agent]
    .sort((a, b) => b.total_cost_usd - a.total_cost_usd)
    .map((a) => ({ ...a, label: a.agent }));

  if (totalSpend === 0 && allRuns === 0) {
    return <Empty>No cost recorded yet. Run scripts/seed.py.</Empty>;
  }

  return (
    <>
      <div className="tiles">
        <Tile label="Total LLM spend" value={usd(totalSpend)} note="All orchestrations to date" />
        <Tile
          label="Cost per qualified lead"
          value={qualified?.count ? usd(qualified.total_cost_usd / qualified.count) : "—"}
          note={qualified?.count ? `${qualified.count} qualified` : "none yet"}
        />
        <Tile
          label="Avg cost per orchestration"
          value={allRuns ? usd(totalSpend / allRuns) : "—"}
          note={`${allRuns} finished`}
        />
        <Tile label="Agents metered" value={String(data.by_agent.length)} />
      </div>

      <Panel
        title="LLM cost by agent"
        hint="One number per agent. Computed in-process from token usage, not read back from a tracing service."
      >
        <ResponsiveContainer width="100%" height={Math.max(160, byAgent.length * 46)}>
          <BarChart data={byAgent} layout="vertical" margin={{ left: 8, right: 64, top: 4 }}>
            <CartesianGrid horizontal={false} stroke="var(--grid)" />
            <XAxis type="number" tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--baseline)" }} />
            <YAxis
              type="category"
              dataKey="label"
              width={110}
              tick={AXIS}
              tickLine={false}
              axisLine={{ stroke: "var(--baseline)" }}
            />
            <Tooltip
              cursor={{ fill: "var(--grid)", opacity: 0.4 }}
              content={<ChartTooltip unit={usd} />}
            />
            <Bar dataKey="total_cost_usd" fill="var(--series-1)" radius={[0, 4, 4, 0]} barSize={16}>
              <LabelList
                dataKey="total_cost_usd"
                position="right"
                formatter={(v: number) => usd(v)}
                style={{ fill: "var(--text-secondary)", fontSize: 11 }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="Spend over time" hint="Daily LLM cost across all orchestrations.">
        {data.trend.length < 2 ? (
          <Empty>Needs at least two days of runs to plot a trend.</Empty>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data.trend} margin={{ left: 8, right: 16, top: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis dataKey="day" tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--baseline)" }} />
              <YAxis tick={AXIS} tickLine={false} axisLine={false} />
              <Tooltip content={<ChartTooltip unit={usd} />} />
              <Line
                type="monotone"
                dataKey="total_cost_usd"
                stroke="var(--series-1)"
                strokeWidth={2}
                dot={{ r: 4, fill: "var(--series-1)", stroke: "var(--surface-1)", strokeWidth: 2 }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </Panel>

      <Panel title="Cost per outcome" hint="The business question: was it worth it?">
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th>Outcome</th>
                <th className="num">Orchestrations</th>
                <th className="num">Total cost</th>
                <th className="num">Avg cost</th>
              </tr>
            </thead>
            <tbody>
              {data.per_outcome.map((o) => (
                <tr key={o.outcome}>
                  <td>{o.outcome}</td>
                  <td className="num">{o.count}</td>
                  <td className="num">{usd(o.total_cost_usd)}</td>
                  <td className="num">{usd(o.avg_cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Rolled up by department">
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th>Department</th>
                <th className="num">LLM calls</th>
                <th className="num">Total cost</th>
              </tr>
            </thead>
            <tbody>
              {data.by_department.map((d) => (
                <tr key={d.department}>
                  <td>{d.department}</td>
                  <td className="num">{d.calls}</td>
                  <td className="num">{usd(d.total_cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
