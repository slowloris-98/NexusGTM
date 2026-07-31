import { useCallback, useEffect, useState } from "react";

/** Status is never carried by color alone -- the badge always shows its label. */
export function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="badge neutral">unknown</span>;
  const tone =
    status === "completed" || status === "ok" || status === "qualified"
      ? "good"
      : status === "running"
        ? "neutral"
        : status.startsWith("halted") || status === "invalid_input" || status === "disqualified"
          ? "warn"
          : "bad";
  return <span className={`badge ${tone}`}>{status.replace(/_/g, " ")}</span>;
}

/** Fetch + poll helper. The live view polls; there are no websockets. */
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
    if (!pollMs) return () => {
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

export function Panel({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {hint && <p className="hint">{hint}</p>}
      {children}
    </section>
  );
}

export function Tile({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note && <div className="note">{note}</div>}
    </div>
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

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="empty">{children}</p>;
}
