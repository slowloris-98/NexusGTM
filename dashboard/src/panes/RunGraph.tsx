import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { pathFromRuns, plural, type Departments, type OrchestrationDetail, type PathStep } from "../api";
import { usePrefersReducedMotion } from "../components";
import {
  NODE,
  SIZE,
  agentKey,
  buildGraph,
  runHops,
  liveKeys,
  starfield,
  type GraphNode,
} from "../graph/layout";

/**
 * One run's route through the system.
 *
 * The Map pane's constellation, drawn for a single lead: the same geometry, the same
 * classes, the same dark world -- but fed by this run's own path instead of a tally across
 * every orchestration, and with everything the run did not touch dimmed back. Seeing what
 * was available and went unused is half of what makes a route legible.
 *
 * The other half is order, which a still picture of a finished run cannot carry. So a
 * single head walks the hops in the sequence the planner chose them, and loops. It is a
 * replay of a real route, not decoration.
 */

/** Carries a department's generated hue into CSS, where the colour recipe lives. */
const hueVar = (hue: number | null): CSSProperties =>
  ({ "--hue": hue ?? 210 }) as CSSProperties;

const pct = (n: number) => `${(n / SIZE) * 100}%`;

const anchorShift: Record<GraphNode["labelAnchor"], string> = {
  middle: "translate(-50%, -50%)",
  start: "translate(0, -50%)",
  end: "translate(-100%, -50%)",
};

/** The map's speed constant, so a hop here and a hop there travel at the same rate. */
const DOT_SPEED = 240; // viewBox units per second
const hopDuration = (length: number) => Math.min(3.2, Math.max(1.2, length / DOT_SPEED));

/** A beat between hops, so the relay reads as steps taken rather than one long slide. */
const REST_MS = 140;

export function RunGraph({
  depts,
  detail,
}: {
  depts: Departments | null;
  detail: OrchestrationDetail;
}) {
  // The detail endpoint does not compute an agent_path; its runs are the same data.
  const path = useMemo(() => pathFromRuns(detail.runs), [detail.runs]);

  // One "orchestration" carrying just this route. That is all buildGraph reads, and it
  // means an agent whose department is unreachable right now still gets a node, restored
  // from this run's own history by the same fallback the map uses.
  const graph = useMemo(() => buildGraph(depts, [{ agent_path: path }]), [depts, path]);
  const hops = useMemo(() => runHops(graph, path), [graph, path]);

  const touched = useMemo(
    () => new Set(path.map((s) => agentKey(s.department, s.agent))),
    [path],
  );
  const touchedDepts = useMemo(() => new Set(path.map((s) => s.department)), [path]);

  // Gives node pulses while the run is going, and the core pulse when it is running but
  // has not picked an agent yet -- both correct here, and both free.
  const live = useMemo(
    () => liveKeys([{ status: detail.status, agent_path: path }]),
    [detail.status, path],
  );

  const stars = useMemo(() => starfield(420), []);
  const reduced = usePrefersReducedMotion();

  // ---- the relay -------------------------------------------------------------------
  //
  // A cursor, not CSS. A staggered animation-delay cannot express a relay: hop n's start
  // offset is the sum of the durations before it, which CSS can carry per element, but the
  // loop period would then have to be that sum too -- and animation-duration is already
  // spent on travel time. Expressing both needs keyframe percentages parameterised per
  // element, which CSS has no mechanism for.
  const [head, setHead] = useState(0);
  const count = hops.length;

  // Latest-value ref rather than a dependency. Durations change only when the path does,
  // and a path change already moves `count`; keeping them out of the effect's deps is what
  // makes the 4s poll free -- identical data still hands us a new `hops` array every time.
  const durations = useRef<number[]>([]);
  useEffect(() => {
    durations.current = hops.map((h) => hopDuration(h.length));
  });

  useEffect(() => {
    // No hops, or motion is unwelcome: no timer exists at all. This is the part the
    // stylesheet cannot do -- collapsing a CSS duration does not stop a setTimeout.
    if (!count || reduced) return;
    // setTimeout, not setInterval: every hop is a different length and so a different wait.
    const wait = (durations.current[head % count] ?? 1.2) * 1000 + REST_MS;
    const timer = setTimeout(() => setHead((h) => (h + 1) % count), wait);
    return () => clearTimeout(timer);
  }, [head, count, reduced]);

  const current = count ? hops[head % count] : null;

  const agents = graph.nodes.filter((n) => n.kind === "agent");

  const labelOf = (step: PathStep) =>
    graph.byKey.get(agentKey(step.department, step.agent))?.label ??
    step.agent.replace(/[_-]+/g, " ");
  const deptLabelOf = (step: PathStep) =>
    graph.departments.find((d) => d.department === step.department)?.label ?? step.department;

  // Much shorter than the map's inventory on purpose: the runs table directly above already
  // names every department, agent and result, so the graph's job in words is the route.
  const describe = [
    path.length === 1
      ? `One agent: ${labelOf(path[0])} in ${deptLabelOf(path[0])}. No handoffs.`
      : `${plural(path.length, "agent")}, in order: ${path
          .map((step, i) =>
            i > 0 && step.agent === path[i - 1].agent && step.department === path[i - 1].department
              ? `${labelOf(step)} again`
              : `${labelOf(step)} in ${deptLabelOf(step)}`,
          )
          .join(", then ")}.`,
    path.some((s) => s.status === "invalid_input")
      ? `${plural(
          path.filter((s) => s.status === "invalid_input").length,
          "handoff",
        )} was refused by the receiving agent's schema and re-planned.`
      : "",
    live.nodes.size
      ? `${[...live.nodes].map((k) => graph.byKey.get(k)?.label ?? k).join(", ")} is working now.`
      : "",
    live.planning ? "The planner is choosing the first step." : "",
  ]
    .filter(Boolean)
    .join(" ");

  if (!depts) return <p className="empty">Reading the registry…</p>;

  // Dimming is only meaningful against something lit. With no route there is nothing to
  // light, and a wholly dimmed constellation is a near-black rectangle carrying nothing.
  if (path.length === 0)
    return <p className="empty">No agent ran, so there is no path to draw.</p>;

  return (
    <div className="map-stage is-run">
      <svg className="map-starfield" aria-hidden="true">
        <g className="map-stars map-stars-a">
          {stars.slice(0, 270).map((s, i) => (
            <circle
              key={i}
              cx={`${((s.x / SIZE) * 100).toFixed(2)}%`}
              cy={`${((s.y / SIZE) * 100).toFixed(2)}%`}
              r={s.r.toFixed(2)}
              opacity={s.o.toFixed(2)}
            />
          ))}
        </g>
        <g className="map-stars map-stars-b">
          {stars.slice(270).map((s, i) => (
            <circle
              key={i}
              cx={`${((s.x / SIZE) * 100).toFixed(2)}%`}
              cy={`${((s.y / SIZE) * 100).toFixed(2)}%`}
              r={(s.r * 0.8).toFixed(2)}
              opacity={(s.o * 0.7).toFixed(2)}
            />
          ))}
        </g>
      </svg>

      <div className="map-canvas">
        <svg
          className="map-svg"
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-labelledby="run-graph-title run-graph-desc"
        >
          <title id="run-graph-title">How this run moved through the system</title>
          <desc id="run-graph-desc">{describe}</desc>

          {/* Own ids: ids are unique per document and the Map pane may not be mounted, so
              neither component can borrow the other's. Both are dressed by .map-halo. */}
          <defs>
            <radialGradient id="run-core-halo" className="map-halo">
              <stop className="halo-in" offset="0%" />
              <stop className="halo-mid" offset="45%" />
              <stop className="halo-out" offset="100%" />
            </radialGradient>
            <filter id="run-node-glow" x="-120%" y="-120%" width="340%" height="340%">
              <feGaussianBlur stdDeviation="9" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* The whole system's silhouette, including everything this run left alone. */}
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

          {/* This run's hops. No --fade: every one of them happened, and there is no linger
              clock on a replay, so the var(--fade, 1) fallback is exactly right. Nor any
              traversed/untraversed weighting -- on a finished run that would be a lie. */}
          <g className="map-flow">
            {hops.map((h) => (
              <path
                key={h.id}
                className={[
                  h.status === "invalid_input" ? "rejected" : "",
                  current?.id === h.id ? "hot" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                style={{ ...hueVar(h.hue), "--w": 1 } as CSSProperties}
                d={h.d}
              />
            ))}
          </g>

          {/* The head. Keying on `head` is the restart: React remounts the circle at each
              advance, so map-travel begins at frame 0 with no animation:none dance.

              It visits refused hops too, unlike the map, which suppresses dots on edges
              where nothing flowed. Here the sequence is the subject -- the planner did make
              that handoff and it was turned away, and skipping it breaks the count. */}
          <g className="map-flow-dots">
            {!reduced && current && (
              <circle
                key={head}
                className="relay"
                cx={0}
                cy={0}
                r={3.5}
                style={
                  {
                    "--hue": current.hue,
                    "--x1": `${current.x1.toFixed(1)}px`,
                    "--y1": `${current.y1.toFixed(1)}px`,
                    "--x2": `${current.x2.toFixed(1)}px`,
                    "--y2": `${current.y2.toFixed(1)}px`,
                    animationDuration: `${hopDuration(current.length).toFixed(2)}s`,
                  } as CSSProperties
                }
              />
            )}
          </g>

          <g className={live.planning ? "map-core live" : "map-core"}>
            {/* No fill attribute: `.map-stage.is-run .map-core .halo` points it at
                #run-core-halo, and CSS would beat an attribute here anyway. */}
            <circle className="halo" cx={SIZE / 2} cy={SIZE / 2} r={NODE.coreHalo} />
            <circle className="core" cx={SIZE / 2} cy={SIZE / 2} r={NODE.core} />
            <circle className="ring" cx={SIZE / 2} cy={SIZE / 2} r={NODE.coreRing} />
          </g>

          {graph.departments.map((d) => (
            <g
              key={d.key}
              className={`map-hub${d.reachable ? "" : " down"}${
                touchedDepts.has(d.department ?? "") ? "" : " dimmed"
              }`}
              style={hueVar(d.hue)}
            >
              <circle className="ring" cx={d.x} cy={d.y} r={NODE.hubRing} />
              <circle className="hub" cx={d.x} cy={d.y} r={d.r} />
            </g>
          ))}

          {agents.map((a) => {
            const isLive = live.nodes.has(a.key);
            return (
              <g
                key={a.key}
                className={[
                  "map-node",
                  isLive ? "live" : "",
                  a.fromHistory ? "historical" : "",
                  touched.has(a.key) ? "" : "dimmed",
                ]
                  .filter(Boolean)
                  .join(" ")}
                style={hueVar(a.hue)}
              >
                {isLive && <circle className="pulse" cx={a.x} cy={a.y} r={NODE.agentPulse} />}
                <circle
                  className="node"
                  cx={a.x}
                  cy={a.y}
                  r={a.r}
                  filter={isLive ? "url(#run-node-glow)" : undefined}
                />
              </g>
            );
          })}
        </svg>

        <div className="map-labels" aria-hidden="true">
          {graph.departments.map((d) => (
            <div
              key={d.key}
              className={`map-dept-label${d.reachable ? "" : " down"}${
                touchedDepts.has(d.department ?? "") ? "" : " dimmed"
              }`}
              style={{
                left: pct(d.labelX),
                top: pct(d.labelY),
                transform: anchorShift[d.labelAnchor],
                textAlign:
                  d.labelAnchor === "end" ? "right" : d.labelAnchor === "start" ? "left" : "center",
              }}
            >
              {d.label}
              {!d.reachable && <span className="sub">not answering</span>}
            </div>
          ))}
          {agents.map((a) => (
            // `shown` is the map's hover gate, reused as a "this run used it" gate: the
            // agents on the route name themselves and the rest stay quiet. No `dimmed`
            // here -- a hidden label needs none and a revealed one must not carry it.
            <div
              key={a.key}
              className={`map-agent-label${a.fromHistory ? " historical" : ""}${
                live.nodes.has(a.key) ? " live" : ""
              }${touched.has(a.key) ? " shown" : ""}`}
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
    </div>
  );
}
