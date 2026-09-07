"""Flattened payload helpers — copied from app/graph_view.py (no streamlit import).

Re-exports the same _build_payload shape the React ConstellationGraph
component expects, but without pulling in streamlit at API-process startup.
"""

import math
from src.domain.enums import NodeType

_TYPE_COLOR = {
    NodeType.SEED.value: "#00ff9c",
    NodeType.EXCHANGE.value: "#ff5c7a",
    NodeType.SANCTIONED_ADDRESS.value: "#ff2d2d",
    NodeType.MIXER.value: "#ffb020",
    NodeType.BRIDGE.value: "#ff8f1f",
    NodeType.CONTRACT_SERVICE.value: "#8a7dff",
    NodeType.SUSPICIOUS_WALLET.value: "#ff4d4d",
    NodeType.INTERMEDIATE_WALLET.value: "#4da3ff",
    NodeType.UNKNOWN.value: "#4a5b78",
}


def _build_payload(result, highlighted_path):
    """Return {nodes, links} from an InvestigationResult — same shape as app/graph_view._build_payload.

    highlighted_path: list[str] of node-address ids forming the strongest path.
    """
    graph = result.graph
    hl = set(highlighted_path or [])
    hl_pairs = set()
    if highlighted_path:
        hl_pairs = {
            (highlighted_path[i], highlighted_path[i + 1])
            for i in range(len(highlighted_path) - 1)
        }

    nodes = []
    for nid, n in graph.nodes.items():
        try:
            amt = float(n.incoming_amount) + float(n.outgoing_amount)
        except (TypeError, ValueError):
            amt = 0.0
        if n.is_seed:
            base = 9.0
        elif n.is_endpoint:
            base = 7.0
        elif n.is_suspicious or n.is_obfuscation_point:
            base = 5.5
        else:
            base = 3.0
        nodes.append(
            {
                "id": nid,
                "short": f"{nid[:6]}...{nid[-4:]}",
                "label": n.address.label or "",
                "type": n.node_type.value,
                "color": _TYPE_COLOR.get(n.node_type.value, "#8494a8"),
                "depth": n.depth,
                "inAmt": n.incoming_amount,
                "outAmt": n.outgoing_amount,
                "patterns": list(n.pattern_flags),
                "conf": round(n.address.confidence, 2),
                "isSeed": n.is_seed,
                "isEndpoint": n.is_endpoint,
                "isSuspicious": n.is_suspicious,
                "isObf": n.is_obfuscation_point,
                "onPath": nid in hl,
                "val": round(base + min(6.0, math.log10(amt + 1)), 2),  # noqa: E501
            }
        )

    pairs = {}
    for e in graph.edges:
        f, t = e.transfer.normalized_from(), e.transfer.normalized_to()
        if f not in graph.nodes or t not in graph.nodes:
            continue
        rec = pairs.setdefault(
            (f, t),
            {"source": f, "target": t, "count": 0, "total": 0.0, "tokens": set()},
        )
        rec["count"] += 1
        rec["total"] += e.transfer.amount_float
        if e.transfer.token_symbol:
            rec["tokens"].add(e.transfer.token_symbol)

    links = [
        {
            "source": f,
            "target": t,
            "count": rec["count"],
            "total": round(rec["total"], 2),
            "tokens": "/".join(sorted(rec["tokens"])) or "-",
            "onPath": (f, t) in hl_pairs,
        }
        for (f, t), rec in pairs.items()
    ]

    return {"nodes": nodes, "links": links}


def build_payload(result, highlighted_path):
    """Public alias — same as _build_payload but without the leading underscore."""
    return _build_payload(result, highlighted_path)


def transactions_from_result(result):
    """One row per graph.edge — no aggregation, sorted newest first."""
    rows = []
    for e in result.graph.edges:
        t = e.transfer
        rows.append(
            {
                "date": t.timestamp.isoformat(),
                "depth": 0,  # placeholder; filled below
                "from": t.normalized_from(),
                "to": t.normalized_to(),
                "amount": t.amount,
                "token": t.token_symbol or "native",
                "hash": t.transaction_hash,
                "onPath": e.is_highlighted,
            }
        )
    # attach depth from the source node
    node_depths = {addr: n.depth for addr, n in result.graph.nodes.items()}
    for row in rows:
        row["depth"] = node_depths.get(row["from"], 0)
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def endpoints_from_result(result):
    """Flattened candidate-endpoints list."""
    out = []
    for i, ep in enumerate(result.candidate_endpoints, 1):
        out.append(
            {
                "rank": i,
                "address": ep.address.address,
                "label": ep.address.label or "",
                "type": ep.address.entity_type.value,
                "amount": ep.total_amount_received,
                "hops": ep.hop_count,
                "unlabeled_hops": ep.unlabeled_hop_count,
                "confidence": round(ep.confidence_score, 3),
                "evidence": ep.evidence,
                "patterns": ep.pattern_flags,
            }
        )
    return out


def patterns_from_result(result):
    """Copy pattern_detections dict as-is (already flat dict-of-lists)."""
    return dict(result.pattern_detections)


def warnings_from_result(result):
    """Merge investigation warnings + result-level warnings."""
    seen = set()
    out = []
    for w in result.investigation.warnings + result.warnings:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def stats_from_result(result):
    """Computed stats for the stats row."""
    suspicious = sum(len(v) for v in result.pattern_detections.values())
    return {
        "nodes": result.graph.node_count,
        "edges": result.graph.edge_count,
        "transactions_examined": result.investigation.transactions_examined,
        "max_depth": result.investigation.max_depth,
        "suspicious_patterns": suspicious,
        "endpoints": len(result.candidate_endpoints),
    }
