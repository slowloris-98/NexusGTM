import { useMemo, useState, type CSSProperties } from "react";
import { plural, type Departments, type Orchestration } from "../api";
import {
  NODE,
  ORCHESTRATOR,
  buildGraph,
  flowEdges,
  liveKeys,
  starfield,
  SIZE,
  type FlowEdge,
  type GraphNode,
} from "../graph/layout";

/**
 * The system as a constellation: the orchestrator at the centre, a hub per department,
 * a node per agent, joined by the handoffs the planner actually decided.
 *
 * Everything is derived from /departments and /orchestrations, both already polling in
 * App. The pane fetches nothing of its own.
 *
 * This is the one surface that departs from the console's visual system -- a committed
 * dark world with a generated colour per department. Recorded in DESIGN.md under Scoped
 * Exceptions. Every static colour lives in the .map-* block in styles.css; the only value
 * set from here is each department's hue, which is data, not a design decision.
 */

/** Carries a department's generated hue into CSS, where the colour recipe lives. */
const hueVar = (hue: number | null): CSSProperties =>
  ({ "--hue": hue ?? 210 }) as CSSProperties;

const pct = (n: number) => `${(n / SIZE) * 100}%`;

/**
 * Labels are HTML over the SVG, not <text> inside it.
 *
 * Text inside a scaled viewBox is sized in user units, so it shrinks with the pane --
 * a label that reads at 11px on a wide screen drops under 8px on a narrow one. As HTML
 * it stays on the documented type ramp at every width, and renders crisper.
 */
const anchorShift: Record<GraphNode["labelAnchor"], string> = {
  middle: "translate(-50%, -50%)",
  start: "translate(0, -50%)",
  end: "translate(-100%, -50%)",
};

/**
 * What the readout strip is showing.
 *
 * The strip is a locked height with two clamped rows, because the canvas above it is
 * sized from leftover space -- a strip that grew with its text would resize the whole
 * constellation on every hover.
 */
type Readout = { name: string; detail: string };

const nodeReadout = (node: GraphNode): Readout => ({
  name: node.label,
  detail: node.description,
});

/** Dots move at one speed rather than for one duration, so a long chord does not appear to
    crawl beside a short one. Clamped so extremes stay watchable. */
const DOT_SPEED = 240; // viewBox units per second
const dotDuration = (length: number) =>
  Math.min(3.2, Math.max(1.2, length / DOT_SPEED));

/** More than this many edges and the dots stop being worth their paint cost. */
const MAX_DOTS = 24;

const edgeReadout = (edge: FlowEdge): Readout => ({
  name: `${edge.fromLabel} → ${edge.toLabel}`,
  detail:
    `The planner handed off this way ${plural(edge.count, "time")}.` +
    (edge.rejected > 0
      ? ` ${edge.rejected} of those were rejected by ${edge.toLabel}'s schema and re-planned.`
      : ""),
});

/**
 * Named SystemMap, not Map: an export called `Map` shadows the global Map constructor in
 * every module that imports it, so `new Map()` silently becomes a component call.
 */
export function SystemMap({
  depts,
  rows,
}: {
  depts: Departments | null;
  rows: Orchestration[];
}) {
  const graph = useMemo(() => buildGraph(depts, rows), [depts, rows]);
  const edges = useMemo(() => flowEdges(graph, rows), [graph, rows]);
  const live = useMemo(() => liveKeys(rows), [rows]);
  const stars = useMemo(() => starfield(260), []);

  // One slot fed by nodes and edges alike, so the strip does not care what produced it.
  const [readout, setReadout] = useState<Readout | null>(null);
  const [branch, setBranch] = useState<string | null>(null);
  const [hotEdge, setHotEdge] = useState<string | null>(null);
  const [hotNode, setHotNode] = useState<string | null>(null);

  const agents = graph.nodes.filter((n) => n.kind === "agent");
  const unreachable = graph.departments.filter((d) => !d.reachable);

  const describe = [
    `${plural(graph.departments.length, "department")}, ${plural(agents.length, "agent")}.`,
    // Named explicitly, because the on-screen labels only appear on hover and an agent
    // with no recorded handoff would otherwise be named nowhere at all.
    graph.departments
      .map((d) => {
        const own = agents.filter((a) => a.department === d.department);
        return `${d.label}: ${own.length ? own.map((a) => a.label).join(", ") : "no agents known"}.`;
      })
      .join(" "),
    unreachable.length
      ? `Not answering: ${unreachable.map((d) => d.label).join(", ")}.`
      : "All departments answering.",
    edges.length
      ? `Observed handoffs: ${edges
          .map((e) => `${e.fromLabel} to ${e.toLabel}, ${plural(e.count, "time")}`)
          .join("; ")}.`
      : "No handoffs recorded yet.",
    live.planning ? "The planner is choosing a next step." : "",
    live.nodes.size
      ? `Working now: ${[...live.nodes].map((k) => graph.byKey.get(k)?.label ?? k).join(", ")}.`
      : "",
  ]
    .filter(Boolean)
    .join(" ");

  if (!depts)
    return (
      <header className="pane-head">
        <h1>Map</h1>
        <p className="meta">Reading the registry…</p>
      </header>
    );

  const dimmed = (node: GraphNode) => branch != null && node.department !== branch;

  const enter = (node: GraphNode) => {
    setReadout(nodeReadout(node));
    setHotNode(node.key);
    if (node.kind === "department") setBranch(node.department);
  };

  /** Edges highlight themselves but never dim branches -- two dimming gestures fighting
      each other makes the map twitch when the pointer crosses from a chord to a hub. */
  const enterEdge = (edge: FlowEdge) => {
    setReadout(edgeReadout(edge));
    setHotEdge(edge.id);
  };

  const leave = () => {
    setReadout(null);
    setBranch(null);
    setHotEdge(null);
    setHotNode(null);
  };

  return (
    <>
      <header className="pane-head">
        <h1>Map</h1>
        <p className="meta">
          Built from the registry, so a department added to{" "}
          <span className="mono">config/departments.yaml</span> appears here on its own.
          Lines between agents are handoffs the planner actually made — nothing declares
          them.
        </p>
      </header>

      <div className="map-stage">
        <div className="map-canvas">
        <svg
          className="map-svg"
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-labelledby="map-title map-desc"
        >
          <title id="map-title">Agent system map</title>
          <desc id="map-desc">{describe}</desc>

          <defs>
            <radialGradient id="core-halo">
              <stop className="halo-in" offset="0%" />
              <stop className="halo-mid" offset="45%" />
              <stop className="halo-out" offset="100%" />
            </radialGradient>
            <filter id="node-glow" x="-120%" y="-120%" width="340%" height="340%">
              <feGaussianBlur stdDeviation="9" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          <g className="map-stars map-stars-a">
            {stars.slice(0, 170).map((s, i) => (
              <circle key={i} cx={s.x} cy={s.y} r={s.r} opacity={s.o} />
            ))}
          </g>
          <g className="map-stars map-stars-b">
            {stars.slice(170).map((s, i) => (
              <circle key={i} cx={s.x} cy={s.y} r={s.r * 0.8} opacity={s.o * 0.7} />
            ))}
          </g>

          {/* Structural: the constellation's silhouette. */}
          <g className="map-structural">
            {graph.structural.map((e) => (
              <line
                key={e.id}
                className={e.reachable ? e.kind : `${e.kind} down`}
                style={hueVar(e.hue)}
                x1={e.x1}
                y1={e.y1}
                x2={e.x2}
                y2={e.y2}
              />
            ))}
          </g>

          {/* Flow: the routes the planner chose, weighted by how often it chose them. */}
          <g className="map-flow">
            {edges.map((e) => {
              const allRejected = e.rejected === e.count;
              const active = live.nodes.has(e.to) || live.nodes.has(e.from);
              const otherBranch =
                branch != null && graph.byKey.get(e.from)?.department !== branch;
              return (
                <path
                  key={e.id}
                  className={[
                    allRejected ? "rejected" : "",
                    active ? "live" : "",
                    otherBranch ? "muted" : "",
                    hotEdge === e.id ? "hot" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  style={{ ...hueVar(e.hue), "--w": e.weight } as CSSProperties}
                  d={e.d}
                />
              );
            })}
          </g>

          {/* A dot per handoff, running sender to receiver. Wholly-rejected edges get none:
              nothing flowed, so nothing travels. */}
          <g className="map-flow-dots">
            {edges
              .filter((e) => e.rejected !== e.count)
              .slice(-MAX_DOTS)
              .map((e, i) => (
                <circle
                  key={e.id}
                  className={
                    branch != null && graph.byKey.get(e.from)?.department !== branch
                      ? "muted"
                      : undefined
                  }
                  cx={0}
                  cy={0}
                  r={3.5}
                  style={
                    {
                      "--hue": e.hue,
                      "--x1": `${e.x1.toFixed(1)}px`,
                      "--y1": `${e.y1.toFixed(1)}px`,
                      "--x2": `${e.x2.toFixed(1)}px`,
                      "--y2": `${e.y2.toFixed(1)}px`,
                      animationDuration: `${dotDuration(e.length).toFixed(2)}s`,
                      // Index-derived, so dots never fire in lockstep and never reshuffle
                      // between polls -- `edges` is already deterministically sorted.
                      animationDelay: `${((i * 0.45) % 1.8).toFixed(2)}s`,
                    } as CSSProperties
                  }
                />
              ))}
          </g>

          {/* Invisible hit targets. A chord is 1-3.4px of stroke, which no pointer can
              reliably find; these sit under the nodes so node hover still wins on overlap. */}
          <g className="map-flow-hit">
            {edges.map((e) => (
              <path
                key={e.id}
                d={e.d}
                onMouseEnter={() => enterEdge(e)}
                onMouseLeave={leave}
              />
            ))}
          </g>

          {/* Centre: the planner. */}
          <g
            className={live.planning ? "map-core live" : "map-core"}
            onMouseEnter={() => enter(graph.byKey.get(ORCHESTRATOR)!)}
            onMouseLeave={leave}
          >
            <circle className="halo" cx={SIZE / 2} cy={SIZE / 2} r={NODE.coreHalo} />
            <circle className="core" cx={SIZE / 2} cy={SIZE / 2} r={NODE.core} />
            <circle className="ring" cx={SIZE / 2} cy={SIZE / 2} r={NODE.coreRing} />
          </g>

          {/* Department hubs. */}
          {graph.departments.map((d) => (
            <g
              key={d.key}
              className={`map-hub${d.reachable ? "" : " down"}${dimmed(d) ? " dimmed" : ""}`}
              style={hueVar(d.hue)}
              onMouseEnter={() => enter(d)}
              onMouseLeave={leave}
            >
              <circle className="ring" cx={d.x} cy={d.y} r={NODE.hubRing} />
              <circle className="hub" cx={d.x} cy={d.y} r={d.r} />
            </g>
          ))}

          {/* Agent nodes. */}
          {agents.map((a) => {
            const isLive = live.nodes.has(a.key);
            return (
              <g
                key={a.key}
                className={[
                  "map-node",
                  isLive ? "live" : "",
                  a.fromHistory ? "historical" : "",
                  dimmed(a) ? "dimmed" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                style={hueVar(a.hue)}
                onMouseEnter={() => enter(a)}
                onMouseLeave={leave}
              >
                {isLive && (
                  <circle className="pulse" cx={a.x} cy={a.y} r={NODE.agentPulse} />
                )}
                <circle
                  className="node"
                  cx={a.x}
                  cy={a.y}
                  r={a.r}
                  filter={isLive ? "url(#node-glow)" : undefined}
                />
              </g>
            );
          })}
        </svg>

        <div className="map-labels" aria-hidden="true">
          {graph.departments.map((d) => (
            <div
              key={d.key}
              className={`map-dept-label${d.reachable ? "" : " down"}${dimmed(d) ? " dimmed" : ""}`}
              style={{
                left: pct(d.labelX),
                top: pct(d.labelY),
                transform: anchorShift[d.labelAnchor],
                textAlign: d.labelAnchor === "end" ? "right" : d.labelAnchor === "start" ? "left" : "center",
              }}
            >
              {d.label}
              {!d.reachable && <span className="sub">not answering</span>}
            </div>
          ))}
          {agents.map((a) => (
            <div
              key={a.key}
              className={`map-agent-label${a.fromHistory ? " historical" : ""}${
                dimmed(a) ? " dimmed" : ""
              }${live.nodes.has(a.key) ? " live" : ""}${hotNode === a.key ? " shown" : ""}`}
              style={{
                ...hueVar(a.hue),
                left: pct(a.x),
                top: pct(a.y + a.r + NODE.labelGap),
                transform: "translate(-50%, 0)",
              }}
            >
              {a.label}
            </div>
          ))}
        </div>
        </div>

        {/* Locked height, two clamped rows. Every state emits exactly these two elements,
            so no readout text can change the height and resize the canvas above it. */}
        <div className="map-readout" aria-live="polite">
          <p className="map-readout-status">
            {readout
              ? readout.name
              : live.nodes.size > 0
                ? `${plural(live.nodes.size, "agent")} working`
                : live.planning
                  ? "Planner choosing a next step"
                  : "Idle"}
          </p>
          <p className="map-readout-detail">
            {readout
              ? readout.detail
              : edges.length
                ? `${plural(edges.length, "distinct handoff")} observed across ${plural(rows.length, "orchestration")}. Line weight is how often.`
                : "No handoffs recorded yet — run scripts/seed.py to put leads through."}
          </p>
        </div>
      </div>

      <div className="map-legend">
        {graph.departments.map((d) => (
          <button
            key={d.key}
            type="button"
            className={`map-chip${d.reachable ? "" : " down"}`}
            style={hueVar(d.hue)}
            onMouseEnter={() => enter(d)}
            onMouseLeave={leave}
            onFocus={() => enter(d)}
            onBlur={leave}
          >
            <span className="map-chip-dot" aria-hidden="true" />
            {d.label}
            <span className="map-chip-count">
              {agents.filter((n) => n.department === d.department).length}
            </span>
          </button>
        ))}
      </div>
    </>
  );
}
