import { useState, useMemo } from "react";
import type { TransactionRow } from "../api";

interface Props {
  transactions: TransactionRow[];
}

function shorten(h: string): string {
  if (h.startsWith("0x")) return `${h.slice(0, 8)}…${h.slice(-4)}`;
  return `${h.slice(0, 6)}…${h.slice(-4)}`;
}

const TYPE_CLASS: Record<string, string> = {
  exchange: "exchange",
  sanctioned_address: "sanctioned",
  mixer: "mixer",
  bridge: "bridge",
  suspicious_wallet: "suspicious",
  intermediate_wallet: "intermediate",
  contract_service: "intermediate",
  unknown: "unknown",
};

export default function TransactionsTable({ transactions }: Props) {
  const [tokenFilter, setTokenFilter] = useState("");
  const [minAmount, setMinAmount] = useState("");

  const tokens = useMemo(() => {
    const s = new Set<string>();
    for (const t of transactions) s.add(t.token);
    return Array.from(s).sort();
  }, [transactions]);

  const filtered = useMemo(() => {
    const min = parseFloat(minAmount);
    return transactions.filter((t) => {
      if (tokenFilter && t.token !== tokenFilter) return false;
      if (!isNaN(min) && t.token !== "native") {
        const amt = parseFloat(t.amount);
        if (isNaN(amt) || amt < min) return false;
      }
      return true;
    });
  }, [transactions, tokenFilter, minAmount]);

  return (
    <div>
      <div
        style={{
          display: "flex",
          gap: 12,
          marginBottom: 12,
          flexWrap: "wrap",
          alignItems: "flex-end",
        }}
      >
        <div className="field" style={{ flex: "0 1 140px", minWidth: 120 }}>
          <label htmlFor="tk-filter">Token</label>
          <select
            id="tk-filter"
            value={tokenFilter}
            onChange={(e) => setTokenFilter(e.target.value)}
          >
            <option value="">All tokens</option>
            {tokens.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ flex: "0 1 140px", minWidth: 120 }}>
          <label htmlFor="min-amount">Min Amount</label>
          <input
            id="min-amount"
            type="text"
            placeholder="0"
            value={minAmount}
            onChange={(e) => setMinAmount(e.target.value)}
          />
        </div>
        <div className="tiny" style={{ marginLeft: "auto", alignSelf: "center" }}>
          {filtered.length} of {transactions.length} transfers
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="empty-table">No transfers match the current filters.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Depth</th>
                <th>From</th>
                <th>To</th>
                <th>Amount</th>
                <th>Token</th>
                <th>Tx Hash</th>
                <th>On Path</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t, i) => (
                <tr key={i}>
                  <td>{new Date(t.date).toLocaleString()}</td>
                  <td>{t.depth}</td>
                  <td className="mono">
                    <span className="addr">{shorten(t.from)}</span>
                  </td>
                  <td className="mono">
                    <span className="addr">{shorten(t.to)}</span>
                  </td>
                  <td className="mono">{t.amount}</td>
                  <td>{t.token}</td>
                  <td className="mono" style={{ color: "var(--text-dim)", fontSize: 11 }}>
                    {shorten(t.hash)}
                  </td>
                  <td>{t.onPath ? "●" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
