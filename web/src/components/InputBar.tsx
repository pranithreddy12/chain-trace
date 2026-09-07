import { useState, useRef } from "react";
import type { TraceRequest } from "../api";

interface Props {
  onTrace: (req: TraceRequest) => void;
  disabled: boolean;
  inFlight: boolean;
  elapsed: number;
}

const CHAINS: { value: "ethereum" | "bsc" | "tron"; label: string }[] = [
  { value: "ethereum", label: "Ethereum" },
  { value: "bsc", label: "BNB Chain" },
  { value: "tron", label: "Tron" },
];

export default function InputBar({ onTrace, disabled, inFlight, elapsed }: Props) {
  const [address, setAddress] = useState("");
  const [chain, setChain] = useState<"ethereum" | "bsc" | "tron">("tron");
  const [depth, setDepth] = useState(2);
  const [tokenFilter, setTokenFilter] = useState("");
  const addrRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!address.trim() || inFlight) return;
    onTrace({
      address: address.trim(),
      chain,
      max_depth: depth,
      max_branches: 25,
      token_filter: tokenFilter.trim() || undefined,
    });
  };

  return (
    <form className="input-bar" onSubmit={handleSubmit}>
      <div className="field" style={{ flex: "1 1 280px", minWidth: 200 }}>
        <label htmlFor="addr">Seed Address</label>
        <input
          id="addr"
          ref={addrRef}
          type="text"
          placeholder="0x... or T..."
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          disabled={disabled || inFlight}
          autoComplete="off"
          spellCheck="false"
        />
      </div>

      <div className="field">
        <label htmlFor="chain">Chain</label>
        <select
          id="chain"
          value={chain}
          onChange={(e) => setChain(e.target.value as any)}
          disabled={disabled || inFlight}
        >
          {CHAINS.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>Max Depth</label>
        <div className="range-wrap">
          <input
            type="range"
            min={1}
            max={6}
            step={1}
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
            disabled={disabled || inFlight}
          />
          <span className="range-val">{depth}</span>
        </div>
        <div className="range-hint">deep traces can take several minutes</div>
      </div>

      <div className="field" style={{ flex: "0 1 180px", minWidth: 140 }}>
        <label htmlFor="token">Token Filter <span className="tiny">(optional)</span></label>
        <input
          id="token"
          type="text"
          placeholder="USDT"
          value={tokenFilter}
          onChange={(e) => setTokenFilter(e.target.value)}
          disabled={disabled || inFlight}
        />
      </div>

      <button
        type="submit"
        className="btn primary"
        disabled={disabled || inFlight || !address.trim()}
      >
        {inFlight ? `TRACING… ${elapsed}s` : "TRACE FUNDS"}
      </button>

      {inFlight && <div className="progress"><span className="dot" /> tracing in progress</div>}
    </form>
  );
}
