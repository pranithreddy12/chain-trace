const BASE = "";

async function get<T>(url: string): Promise<T> {
  const res = await fetch(BASE + url);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || `GET ${url} failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(BASE + url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || `POST ${url} failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export interface TraceRequest {
  address: string;
  chain: "ethereum" | "bsc" | "tron";
  max_depth: number;
  max_branches: number;
  token_filter?: string;
}

export interface NodePayload {
  id: string;
  short: string;
  label: string;
  type: string;
  color: string;
  depth: number;
  inAmt: string;
  outAmt: string;
  patterns: string[];
  conf: number;
  isSeed: boolean;
  isEndpoint: boolean;
  isSuspicious: boolean;
  isObf: boolean;
  onPath: boolean;
  val: number;
}

export interface LinkPayload {
  source: string;
  target: string;
  count: number;
  total: number;
  tokens: string;
  onPath: boolean;
}

export interface EndpointRow {
  rank: number;
  address: string;
  label: string;
  type: string;
  amount: string;
  hops: number;
  unlabeled_hops: number;
  confidence: number;
  evidence: string[];
  patterns: string[];
}

export interface TransactionRow {
  date: string;
  depth: number;
  from: string;
  to: string;
  amount: string;
  token: string;
  hash: string;
  onPath: boolean;
}

export interface TraceResponse {
  investigation_id: string;
  seed: { address: string; chain: string; label: string | null };
  stats: {
    nodes: number;
    edges: number;
    transactions_examined: number;
    max_depth: number;
    suspicious_patterns: number;
    endpoints: number;
  };
  nodes: NodePayload[];
  links: LinkPayload[];
  endpoints: EndpointRow[];
  transactions: TransactionRow[];
  patterns: Record<string, unknown[]>;
  warnings: string[];
}

export async function traceFunds(req: TraceRequest): Promise<TraceResponse> {
  const url = "/api/trace";
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Trace failed (${res.status})`);
  }
  return res.json() as Promise<TraceResponse>;
}

export async function highlightPath(
  investigation_id: string
): Promise<TraceResponse> {
  const res = await fetch("/api/highlight", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ investigation_id }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Highlight failed (${res.status})`);
  }
  return res.json() as Promise<TraceResponse>;
}

export async function fetchReport(
  investigation_id: string,
  format: "text" | "json" = "text"
): Promise<{ report: string }> {
  const res = await fetch(`/api/report/${investigation_id}?format=${format}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Report failed (${res.status})`);
  }
  return res.json() as Promise<{ report: string }>;
}
