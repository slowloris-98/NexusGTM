import { useCallback, useEffect, useState } from "react";
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
