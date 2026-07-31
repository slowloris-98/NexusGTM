import { useState } from "react";
import { Cost } from "./views/Cost";
import { DecisionTrailView } from "./views/DecisionTrail";
import { Knowledge } from "./views/Knowledge";
import { Live } from "./views/Live";
import { Results } from "./views/Results";

const TABS = ["Live", "Results", "Cost", "Decision trail", "Knowledge base"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const [tab, setTab] = useState<Tab>("Live");
  const [selected, setSelected] = useState<string | null>(null);

  const open = (id: string) => {
    setSelected(id);
    setTab("Results");
  };

  return (
    <div className="app">
      <header className="masthead">
        <h1>NexusGTM Control Plane</h1>
      </header>
      <p className="subtitle">
        Marketing · RevOps · Sales — agent orchestrations, their outcomes, and what they cost.
      </p>

      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t}
            aria-current={t === tab}
            onClick={() => {
              setTab(t);
              if (t !== "Results") setSelected(null);
            }}
          >
            {t}
          </button>
        ))}
      </nav>

      {tab === "Live" && <Live onOpen={open} />}
      {tab === "Results" && (
        <Results selected={selected} onOpen={setSelected} onBack={() => setSelected(null)} />
      )}
      {tab === "Cost" && <Cost />}
      {tab === "Decision trail" && <DecisionTrailView />}
      {tab === "Knowledge base" && <Knowledge onOpen={open} />}
    </div>
  );
}
