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
import { api, plural, usd } from "../api";
import { Block, ChartTooltip, useAsync } from "../components";
import { readAgent, readOutcome } from "../vocabulary";

const AXIS = { fill: "var(--muted)", fontSize: 11 };

/** The cost question, one level down from the briefing's one-line answer. */
export function Spend() {
  const { data, error } = useAsync(() => api.costs(), [], 10000);

  if (error)
    return (
      <>
        <header className="pane-head">
          <h1>Could not load spend</h1>
        </header>
        <p className="fault-note">{error}</p>
      </>
    );

  if (!data)
    return (
      <header className="pane-head">
        <h1>Spend</h1>
        <p className="meta">Loading…</p>
      </header>
    );

  const totalSpend = data.by_agent.reduce((sum, a) => sum + a.total_cost_usd, 0);
  const runs = data.per_outcome.reduce((sum, o) => sum + o.count, 0);
  const qualified = data.per_outcome.find((o) => o.outcome === "qualified");

  if (totalSpend === 0 && runs === 0)
    return (
      <>
        <header className="pane-head">
          <h1>Spend</h1>
        </header>
        <p className="answer">Nothing has cost anything yet.</p>
        <p className="support">
          <span>
            Run <span className="mono">scripts/seed.py</span> to put three leads through
            and populate this.
          </span>
        </p>
      </>
    );

  const byAgent = [...data.by_agent]
    .sort((a, b) => b.total_cost_usd - a.total_cost_usd)
    .map((a) => ({ ...a, label: readAgent(a.agent) }));

  return (
    <>
      <header className="pane-head">
        <h1>Spend</h1>
        <p className="meta">
          Every figure is metered in-process from token usage and priced against{" "}
          <span className="mono">config/llm.yaml</span> — not read back from a tracing
          service.
        </p>
      </header>

      <Block title="All time">
        <p className="answer">
          <span className="fig">{usd(totalSpend)}</span> across{" "}
          {plural(runs, "orchestration")}
          {qualified?.count ? (
            <>
              , or{" "}
              <span className="fig">
                {usd(qualified.total_cost_usd / qualified.count)}
              </span>{" "}
              per qualified lead.
            </>
          ) : (
            "."
          )}
        </p>
        <p className="support">
          <span>{plural(data.by_agent.length, "agent")} metered</span>
          <span className="sep">
            the planner is metered too, as its own line
          </span>
        </p>
      </Block>

      <Block title="Where the money goes">
        <ResponsiveContainer width="100%" height={Math.max(160, byAgent.length * 46)}>
          <BarChart data={byAgent} layout="vertical" margin={{ left: 8, right: 68, top: 4 }}>
            <CartesianGrid horizontal={false} stroke="var(--grid)" />
            <XAxis
              type="number"
              tick={AXIS}
              tickLine={false}
              axisLine={{ stroke: "var(--baseline)" }}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={124}
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
      </Block>

      <Block title="Over time">
        {data.trend.length < 2 ? (
          <p className="empty">
            One day of runs so far. A trend needs at least two to say anything true.
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data.trend} margin={{ left: 8, right: 16, top: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis
                dataKey="day"
                tick={AXIS}
                tickLine={false}
                axisLine={{ stroke: "var(--baseline)" }}
              />
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
      </Block>

      <Block title="Was it worth it">
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th>Outcome</th>
                <th className="num">Leads</th>
                <th className="num">Total</th>
                <th className="num">Each</th>
              </tr>
            </thead>
            <tbody>
              {data.per_outcome.map((o) => (
                <tr key={o.outcome}>
                  <td>{readOutcome(o.outcome)?.label ?? "No verdict"}</td>
                  <td className="num">{o.count}</td>
                  <td className="num">{usd(o.total_cost_usd)}</td>
                  <td className="num">{usd(o.avg_cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Block>

      <Block title="By department">
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th>Department</th>
                <th className="num">LLM calls</th>
                <th className="num">Total</th>
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
      </Block>
    </>
  );
}
