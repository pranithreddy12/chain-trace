import type { EndpointRow } from "../api";

interface Props {
  endpoints: EndpointRow[];
}

function typeTag(type: string): React.ReactNode {
  const t = type.replace(/_/g, " ");
  const cls = ({
    exchange: "exchange",
    sanctioned_address: "sanctioned",
    mixer: "mixer",
    bridge: "bridge",
    suspicious_wallet: "suspicious",
    seed: "seed",
    intermediate_wallet: "intermediate",
    contract_service: "intermediate",
  } as const)[type] ?? "unknown";
  return <span className={`tag ${cls}`}>{t}</span>;
}

function confClass(v: number): string {
  if (v >= 0.7) return "risk-high";
  if (v >= 0.4) return "risk-mid";
  return "risk-low";
}

export default function EndpointsTable({ endpoints }: Props) {
  if (!endpoints.length) {
    return <div className="empty-table">No candidate endpoints identified.</div>;
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Entity</th>
            <th>Address</th>
            <th>Type</th>
            <th>Amount</th>
            <th>Hops</th>
            <th>Conf.</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {endpoints.map((ep) => (
            <tr key={ep.rank}>
              <td>{ep.rank}</td>
              <td>
                <span className="text-bright">
                  {ep.label || `${ep.address.slice(0, 6)}…${ep.address.slice(-4)}`}
                </span>
              </td>
              <td className="mono">
                <span className="addr">{ep.address}</span>
              </td>
              <td>{typeTag(ep.type)}</td>
              <td>{ep.amount}</td>
              <td>
                {ep.hops}
                {ep.unlabeled_hops > 0 ? (
                  <span className="text-dim"> ({ep.unlabeled_hops} unlabeled)</span>
                ) : null}
              </td>
              <td className={confClass(ep.confidence)}>
                {(ep.confidence * 100).toFixed(1)}%
              </td>
              <td className="text-dim" style={{ maxWidth: 240 }}>
                {ep.evidence.length
                  ? ep.evidence.map((e) => e).join("; ")
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
