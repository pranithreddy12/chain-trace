import type { TraceResponse } from "../api";

interface Props {
  data: TraceResponse | null;
}

const ITEMS = [
  { key: "nodes", label: "Nodes", cls: "" },
  { key: "transactions_examined", label: "Transactions Examined", cls: "" },
  { key: "max_depth", label: "Max Depth", cls: "" },
  {
    key: "suspicious_patterns",
    label: "Suspicious Patterns",
    cls: (v: number) => (v > 0 ? "alert" : ""),
  },
  { key: "endpoints", label: "Candidate Endpoints", cls: "" },
] as const;

export default function StatsRow({ data }: Props) {
  if (!data) return null;
  const s = data.stats;
  return (
    <div className="stats">
      {ITEMS.map(({ key, label, cls }) => {
        const val = s[key as keyof typeof s] as number;
        const c = typeof cls === "function" ? cls(val) : cls;
        return (
          <div key={key} className={`stat ${c}`}>
            <div className="val">{val.toLocaleString()}</div>
            <div className="lbl">{label}</div>
          </div>
        );
      })}
    </div>
  );
}
