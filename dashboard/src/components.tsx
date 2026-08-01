import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import type { Reading, Tone } from "./vocabulary";

/**
 * Status is never carried by colour alone -- every chip renders its word.
 * The tone only reinforces what the label already says.
 */
export function Chip({ reading }: { reading: Reading | null }) {
  if (!reading) return <span className="chip neutral">No verdict</span>;
  return (
    <span className={`chip ${reading.tone}`} title={reading.gloss || undefined}>
      {reading.label}
    </span>
  );
}

/**
 * Decorative only. Every caller prints the status word immediately beside it, so
 * announcing the tone again would just make a screen reader say it twice.
 */
export function Dot({ tone }: { tone: Tone }) {
  return <span className={`dot ${tone}`} aria-hidden="true" />;
}

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

/**
 * The reduced-motion preference, live.
 *
 * The stylesheet already collapses every duration under that query, so nothing driven by
 * CSS alone needs this. An animation driven from JS does: the run graph's relay advances
 * on a timer, and collapsing a CSS duration does not stop a setTimeout. Live rather than
 * read once, because the setting can change while the console is open.
 */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia(REDUCED_MOTION);
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    },
    () => window.matchMedia(REDUCED_MOTION).matches,
    () => false,
  );
}

/** Fetch + poll helper. The console polls; there are no websockets. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[], pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const stable = useCallback(fn, deps);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const result = await stable();
        if (alive) {
          setData(result);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    };
    load();
    if (!pollMs)
      return () => {
        alive = false;
      };
    const timer = setInterval(load, pollMs);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [stable, pollMs]);

  return { data, error, loading };
}

export function Block({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="block">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

/**
 * A pair of blocks that sit side by side once the pane is wide enough for two readable
 * columns, and stack like any other block when it is not.
 *
 * The pane is fluid and uncapped, so on a wide monitor the alternative is dead ground
 * beside a 46ch measure. Which blocks pair is a per-pane judgement -- two small tables,
 * or a rationale beside the picture of it -- so it is stated at the call site rather
 * than inferred from position.
 */
export function Spread({ children }: { children: React.ReactNode }) {
  return <div className="spread">{children}</div>;
}

export function ChartTooltip({
  active,
  payload,
  label,
  unit,
}: {
  active?: boolean;
  payload?: { value: number; name: string }[];
  label?: string;
  unit?: (n: number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="k">{label}</div>
      {payload.map((p) => (
        <div key={p.name}>
          <span className="v">{unit ? unit(p.value) : p.value}</span>
        </div>
      ))}
    </div>
  );
}

/** Loading is a shape, not a spinner dropped in the middle of content. */
export function Skeleton({ width, height = 13 }: { width: string; height?: number }) {
  return (
    <span
      className="skeleton"
      style={{ display: "inline-block", width, height }}
      aria-hidden="true"
    />
  );
}
