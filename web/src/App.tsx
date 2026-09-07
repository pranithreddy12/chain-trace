import { useState, useRef, useEffect } from "react";
import InputBar from "./components/InputBar";
import StatsRow from "./components/StatsRow";
import ConstellationGraph from "./components/ConstellationGraph";
import EndpointsTable from "./components/EndpointsTable";
import TransactionsTable from "./components/TransactionsTable";
import ReportPanel from "./components/ReportPanel";
import {
  traceFunds,
  highlightPath,
  type TraceRequest,
  type TraceResponse,
} from "./api";

const CHAINS: Record<string, string> = {
  ethereum: "Ethereum",
  bsc: "BNB Chain",
  tron: "Tron",
};

export default function App() {
  const [data, setData] = useState<TraceResponse | null>(null);
  const [investigationId, setInvestigationId] = useState<string | null>(null);
  const [inFlight, setInFlight] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const tRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startTimer = () => {
    setInFlight(true);
    setElapsed(0);
    setError(null);
    tRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
  };

  const stopTimer = () => {
    if (tRef.current) {
      clearInterval(tRef.current);
      tRef.current = null;
    }
    setInFlight(false);
  };

  const runTrace = async (req: TraceRequest) => {
    startTimer();
    try {
      const res = await traceFunds(req);
      setData(res);
      setInvestigationId(res.investigation_id);
    } catch (e) {
      setError((e as Error).message || "Trace failed");
      setData(null);
    } finally {
      stopTimer();
    }
  };

  const runHighlight = async () => {
    if (!investigationId) return;
    startTimer();
    try {
      const res = await highlightPath(investigationId);
      setData(res);
    } catch (e) {
      setError((e as Error).message || "Highlight failed");
    } finally {
      stopTimer();
    }
  };

  // auto-clear error after a few seconds
  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 6000);
    return () => clearTimeout(t);
  }, [error]);

  return (
    <div className="app">
      <header className="header">
        <h1>CHAINTRACE</h1>
        <span className="sub">Blockchain Fund-Flow Investigation</span>
      </header>

      <InputBar
        onTrace={runTrace}
        disabled={false}
        inFlight={inFlight}
        elapsed={elapsed}
      />

      {error && (
        <div
          className="toast err"
          style={{ position: "relative", transform: "none", marginBottom: 14, marginTop: -8 }}
        >
          {error}
        </div>
      )}

      {data && <StatsRow data={data} />}

      {investigationId && (
        <div className="section">
          <div className="section-head">
            <h2>Fund Flow Constellation</h2>
            <div className="actions">
              <button className="btn" onClick={runHighlight} disabled={inFlight}>
                Highlight strongest path
              </button>
              <button
                className="btn"
                onClick={() => {
                  if (!data) return;
                  setData({
                    ...data,
                    nodes: data.nodes.map((n) => ({ ...n, onPath: false })),
                    links: data.links.map((l) => ({ ...l, onPath: false })),
                  });
                }}
                disabled={inFlight}
              >
                Clear
              </button>
            </div>
          </div>
          <ConstellationGraph nodes={data!.nodes} links={data!.links} />
        </div>
      )}

      {data && data.endpoints.length > 0 && (
        <div className="section">
          <div className="section-head">
            <h2>Top Investigative Endpoints</h2>
          </div>
          <EndpointsTable endpoints={data.endpoints} />
        </div>
      )}

      {data && data.transactions.length > 0 && (
        <div className="section">
          <div className="section-head">
            <h2>Transactions</h2>
          </div>
          <TransactionsTable transactions={data.transactions} />
        </div>
      )}

      {investigationId && <ReportPanel investigation_id={investigationId} />}
    </div>
  );
}
