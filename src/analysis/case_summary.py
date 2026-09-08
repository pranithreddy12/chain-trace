"""Investigative synthesis.

Turns the reconstructed graph + pattern detections + scored endpoints into a
ranked set of plain-English findings an investigator can act on and paste into
a case report. Behavioural — works with or without entity labels.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

from ..domain.models.investigation import InvestigationResult
from ..domain.enums import EntityCategory
from ..config.settings import get_settings
from .taint import dominant_path
# A dispersal burst this many recipients or more, inside the burst window.
BURST_MIN_RECIPIENTS = 6
BURST_WINDOW_SECONDS = 3600


def _fmt_amt(x: float) -> str:
    return f"{x:,.2f}"


def build_case_summary(result: InvestigationResult) -> Dict[str, Any]:
    cfg = get_settings()
    g = result.graph
    seed = result.seed_address.address
    seed_norm = seed.lower() if result.seed_address.chain.is_evm else seed

    out_edges = [e for e in g.edges if e.transfer.normalized_from() == seed_norm]
    all_ts = [e.transfer.timestamp for e in g.edges]

    # per-address in/out aggregates over the observed graph
    recv_from: Dict[str, set] = defaultdict(set)
    recv_amt: Dict[str, float] = defaultdict(float)
    sent_amt: Dict[str, float] = defaultdict(float)
    for e in g.edges:
        f, t = e.transfer.normalized_from(), e.transfer.normalized_to()
        recv_from[t].add(f)
        recv_amt[t] += e.transfer.amount_float
        sent_amt[f] += e.transfer.amount_float

    max_depth = max((n.depth for n in g.nodes.values()), default=0)

    # funds that entered a wallet we did not trace further (frontier leakage)
    frontier_value = sum(
        recv_amt[addr]
        for addr, n in g.nodes.items()
        if addr != seed_norm and sent_amt.get(addr, 0.0) == 0.0
    )
    seed_out_total = sum(e.transfer.amount_float for e in out_edges)

    findings: List[Dict[str, Any]] = []

    # ---- 0. reported vs observed -----------------------------------------
    reported = getattr(result.investigation, "reported_amount", None)
    if reported and seed_out_total < reported * 0.95:
        findings.append(
            {
                "severity": "info",
                "type": "coverage",
                "title": "Only part of the reported amount is traceable here",
                "detail": (
                    f"{_fmt_amt(reported)} {_native(result)} was reported, but only "
                    f"{_fmt_amt(seed_out_total)} left this wallet within the traced "
                    f"window ({seed_out_total / reported * 100:.0f}%). The remainder "
                    f"may have moved before the incident time, through a different "
                    f"wallet, or in a token/chain not covered by this trace. All "
                    f"percentages below are shares of the REPORTED amount."
                ),
                "addresses": [seed_norm],
                "metrics": {
                    "reported_amount": reported,
                    "observed_outflow": round(seed_out_total, 2),
                    "coverage_pct": round(seed_out_total / reported * 100, 1),
                },
            }
        )

    # ---- 1. dispersal / structuring bursts (from fan-out detections) -------
    for d in result.pattern_detections.get("fan_out", []):
        addr = d["address"]
        recips = [
            e for e in g.edges if e.transfer.normalized_from() == addr
        ]
        if not recips:
            continue
        ts = sorted(e.transfer.timestamp for e in recips)
        span = (ts[-1] - ts[0]).total_seconds() if len(ts) > 1 else 0
        # count recipients inside the tightest BURST_WINDOW
        burst = 0
        for i, a in enumerate(ts):
            j = i
            while j < len(ts) and (ts[j] - a).total_seconds() <= BURST_WINDOW_SECONDS:
                j += 1
            burst = max(burst, j - i)
        if burst >= BURST_MIN_RECIPIENTS:
            sev = "high" if addr == seed_norm else "medium"
            findings.append(
                {
                    "severity": sev,
                    "type": "structuring",
                    "title": f"Rapid dispersal to {d['unique_recipients']} wallets",
                    "detail": (
                        f"{addr[:10]}... sent {_fmt_amt(d['total_amount'])} "
                        f"{_native(result)} across {d['unique_recipients']} distinct "
                        f"recipients; {burst} of them within "
                        f"{int(BURST_WINDOW_SECONDS/60)} min "
                        f"(avg {_fmt_amt(d['avg_amount'])}). Consistent with "
                        f"layering / structuring to break the trail."
                    ),
                    "addresses": [addr],
                    "metrics": {
                        "recipients": d["unique_recipients"],
                        "in_burst": burst,
                        "total_amount": round(d["total_amount"], 2),
                        "span_seconds": int(span),
                    },
                }
            )

    # ---- 2. rapid forwarding (from corrected hop-velocity) ----------------
    hv = sorted(
        result.pattern_detections.get("hop_velocity", []),
        key=lambda x: x["received_to_forwarded_seconds"],
    )
    for d in hv[:8]:
        secs = d["received_to_forwarded_seconds"]
        findings.append(
            {
                "severity": "medium" if secs <= 120 else "low",
                "type": "rapid_forwarding",
                "title": f"Funds forwarded {_dur(secs)} after arriving",
                "detail": (
                    f"{d['address'][:10]}... received "
                    f"{_fmt_amt(d['received_amount'])} and forwarded "
                    f"{_fmt_amt(d['forwarded_amount'])} {_dur(secs)} later "
                    f"({d['received_at'][:16]} -> {d['forwarded_at'][:16]}). "
                    f"Pass-through wallet, not a destination."
                ),
                "addresses": [d["address"]],
                "metrics": {
                    "seconds": secs,
                    "received": d["received_amount"],
                    "forwarded": d["forwarded_amount"],
                },
            }
        )

    # ---- 3. convergence / consolidation points --------------------------
    # A convergence point is either (a) many distinct senders, or (b) fewer
    # senders BUT holding a material share of the traced funds. Sender count
    # alone at the threshold is weak evidence; value corroborates it.
    def _is_convergence(addr: str, n_src: int) -> bool:
        if addr == seed_norm:
            return False
        if n_src >= cfg.convergence_min_senders:
            return True
        node = g.nodes.get(addr)
        taint = node.taint_fraction if node else 0.0
        return (
            n_src >= cfg.convergence_min_senders_with_value
            and taint >= cfg.convergence_material_taint
        )

    collectors = sorted(
        (
            (addr, len(srcs), recv_amt[addr])
            for addr, srcs in recv_from.items()
            if _is_convergence(addr, len(srcs))
        ),
        # rank by money first, then by how many wallets funnelled in
        key=lambda x: (
            g.nodes[x[0]].taint_fraction if x[0] in g.nodes else 0.0,
            x[1],
        ),
        reverse=True,
    )
    lead_rows: List[Dict[str, Any]] = []
    for addr, n_src, amt in collectors[:6]:
        node = g.nodes.get(addr)
        label = node.address.label if node else None
        etype = node.address.entity_type.value if node else "unknown"
        fwd = sent_amt.get(addr, 0.0)
        verified = etype in ("exchange", "sanctioned")
        findings.append(
            {
                "severity": "high" if verified else "medium",
                "type": "convergence",
                "title": (
                    f"{n_src} traced wallets re-converge at "
                    f"{label or addr[:10] + '...'}"
                ),
                "detail": (
                    f"{addr} received {_fmt_amt(amt)} {_native(result)} from "
                    f"{n_src} distinct wallets in the traced set and forwarded "
                    f"{_fmt_amt(fwd)} onward. "
                    + (
                        f"Labelled: {label} ({etype})."
                        if verified
                        else "No entity label — candidate consolidation / "
                        "exchange-deposit point (UNVERIFIED); prioritise for "
                        "off-chain (exchange KYC) follow-up."
                    )
                ),
                "addresses": [addr],
                "metrics": {
                    "distinct_senders": n_src,
                    "received": round(amt, 2),
                    "forwarded": round(fwd, 2),
                    "entity_type": etype,
                },
            }
        )
        conv_taint = node.taint_fraction if node else 0.0
        lead_rows.append(
            {
                "address": addr,
                "reason": (
                    f"{label} ({etype})"
                    if verified
                    else f"convergence of {n_src} traced wallets (unverified)"
                    + (
                        f"; {conv_taint * 100:.1f}% of traced funds arrived here"
                        if conv_taint > 0
                        else ""
                    )
                ),
                "score": 0.85
                if verified
                else round(min(0.80, 0.12 + 0.03 * n_src + 0.55 * conv_taint), 2),
                "verified": verified,
                "taint_fraction": conv_taint,
            }
        )

    # ---- 3b. layering chain along the money path ------------------------
    # peel + rapid forwarding over consecutive hops IS the layering signature.
    # One finding for the whole chain beats five disconnected flags.
    peel_addrs = {d["address"] for d in result.pattern_detections.get("peel_behavior", [])}
    fast = {
        d["address"]: d["received_to_forwarded_seconds"]
        for d in result.pattern_detections.get("hop_velocity", [])
    }
    top_taint = max(
        (n for a, n in g.nodes.items() if a != seed_norm),
        key=lambda n: n.taint_fraction,
        default=None,
    )
    if top_taint is not None and top_taint.taint_fraction > 0:
        chain = dominant_path(g, seed_norm, top_taint.address.address)
        hops = [a for a in chain[1:-1] if a in peel_addrs or a in fast]
        if len(chain) >= 4 and len(hops) >= 2:
            gaps = [fast[a] for a in chain[1:-1] if a in fast]
            retained = top_taint.taint_fraction * 100
            findings.append(
                {
                    "severity": "high",
                    "type": "layering",
                    "title": f"Layering chain across {len(chain) - 1} hops",
                    "detail": (
                        f"Funds moved {seed_norm[:10]}... -> "
                        + " -> ".join(a[:8] + "..." for a in chain[1:])
                        + f". {len(hops)} of the intermediate wallets forwarded "
                        f"promptly and/or peeled a small amount, retaining "
                        f"{retained:.1f}% of the traced value to the end of the "
                        f"chain"
                        + (
                            f" (fastest hop {_dur(min(gaps))})."
                            if gaps
                            else "."
                        )
                        + " Consistent with deliberate layering to break the trail."
                    ),
                    "addresses": chain,
                    "metrics": {
                        "hops": len(chain) - 1,
                        "pass_through_hops": len(hops),
                        "value_retained_pct": round(retained, 2),
                        "fastest_hop_seconds": min(gaps) if gaps else None,
                    },
                }
            )

    # ---- 3c. behavioural wallet types (label-independent, UNVERIFIED) ----
    profiles = getattr(result, "wallet_profiles", {}) or {}
    _BEHAVIOUR_TITLES = {
        "exchange_deposit": "Likely exchange deposit infrastructure",
        "collector": "Consolidation point",
        "distributor": "Dispersal hub",
        "pass_through": "Pass-through wallet",
    }
    ranked = sorted(
        (
            (a, p)
            for a, p in profiles.items()
            if p["behavior"] in _BEHAVIOUR_TITLES
        ),
        key=lambda kv: (
            kv[1]["confidence"],
            g.nodes[kv[0]].taint_fraction if kv[0] in g.nodes else 0.0,
        ),
        reverse=True,
    )
    for addr, prof in ranked[:5]:
        node = g.nodes.get(addr)
        taint = node.taint_fraction if node else 0.0
        findings.append(
            {
                "severity": "high" if prof["behavior"] == "exchange_deposit" else "medium",
                "type": "behaviour",
                "title": f"{_BEHAVIOUR_TITLES[prof['behavior']]}: {addr[:10]}...",
                "detail": (
                    f"{addr} - {'; '.join(prof['signals'])}. "
                    f"{taint * 100:.1f}% of the traced funds passed through here. "
                    f"UNVERIFIED behavioural classification "
                    f"(confidence {prof['confidence']:.2f}) - corroborate off-chain "
                    f"before acting."
                ),
                "addresses": [addr],
                "metrics": {**prof["metrics"], "behavior": prof["behavior"]},
            }
        )

    # ---- 4. labelled-entity hits (exchange / sanctioned / mixer) --------
    for addr, n in g.nodes.items():
        et = n.address.entity_type
        if et in (
            EntityCategory.EXCHANGE,
            EntityCategory.SANCTIONED,
            EntityCategory.MIXER,
            EntityCategory.BRIDGE,
        ):
            sev = "high" if et != EntityCategory.EXCHANGE else "medium"
            findings.append(
                {
                    "severity": sev,
                    "type": "entity_hit",
                    "title": f"Path reaches {et.value}: {n.address.label or addr[:10]}",
                    "detail": (
                        f"{addr} is classified {et.value} "
                        f"(source {n.address.source.value if n.address.source else 'n/a'}, "
                        f"confidence {n.address.confidence:.2f}) at depth {n.depth}."
                    ),
                    "addresses": [addr],
                    "metrics": {"entity_type": et.value, "depth": n.depth},
                }
            )

    # ---- 5. scored endpoints become leads ------------------------------
    # Lead priority is driven by how much of the victim's money actually got
    # there, softened by how identifiable the wallet is. A wallet holding 79% of
    # the funds with unknown ownership outranks a weak convergence hit.
    by_addr = {r["address"]: r for r in lead_rows}
    for ep in result.candidate_endpoints[:8]:
        verified = ep.address.entity_type.value in ("exchange", "sanctioned")
        priority = round(0.70 * ep.taint_fraction + 0.30 * ep.entity_confidence, 2)
        bits = [f"{ep.taint_fraction * 100:.1f}% of traced funds arrived here"]
        if ep.address.label:
            bits.append(f"labelled {ep.address.label}")
        elif ep.is_terminal:
            bits.append("trail ends here - unidentified, priority KYC/subpoena target")
        bits.append(f"{ep.hop_count} hop(s) from the seed")
        row = {
            "address": ep.address.address,
            "reason": "; ".join(bits),
            "score": priority,
            "verified": verified,
            "taint_fraction": ep.taint_fraction,
            "flow_confidence": ep.flow_confidence,
            "entity_confidence": ep.entity_confidence,
        }
        prev = by_addr.get(row["address"])
        if prev is None:
            lead_rows.append(row)
            by_addr[row["address"]] = row
        elif priority > prev["score"]:
            prev.update(row)

    # Fallback leads: if nothing labelled or converged, the highest-value wallets
    # on the un-expanded frontier are where the investigation should go next.
    if not lead_rows:
        frontier = sorted(
            (
                (addr, recv_amt[addr])
                for addr, n in g.nodes.items()
                if addr != seed_norm and sent_amt.get(addr, 0.0) == 0.0
            ),
            key=lambda x: x[1],
            reverse=True,
        )
        for addr, amt in frontier[:5]:
            if amt <= 0:
                continue
            lead_rows.append(
                {
                    "address": addr,
                    "reason": (
                        f"received {_fmt_amt(amt)} {_native(result)} and was not "
                        f"traced further (depth/branch limit) — expand next"
                    ),
                    "score": 0.3,
                    "verified": False,
                }
            )

    sev_rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: sev_rank.get(f["severity"], 9))
    # "Where do I look first?" is answered by the money, then by the score.
    lead_rows.sort(
        key=lambda x: (x.get("taint_fraction", 0.0), x["score"]), reverse=True
    )

    headline = _headline(result, findings, seed_out_total, frontier_value)

    return {
        "seed": seed,
        "chain": result.seed_address.chain.value,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "headline": headline,
        "overview": {
            "total_sent_by_seed": round(seed_out_total, 2),
            "seed_tx_count": len(out_edges),
            "direct_recipients": len({e.transfer.normalized_to() for e in out_edges}),
            "first_activity": min(all_ts).isoformat() if all_ts else None,
            "last_activity": max(all_ts).isoformat() if all_ts else None,
            "wallets_in_graph": g.node_count,
            "transfers_in_graph": g.edge_count,
            "max_depth_traced": max_depth,
            "value_left_observed_window": round(frontier_value, 2),
        },
        "findings": findings,
        "recommended_leads": lead_rows[:6],
        "limitations": [
            "Only outgoing fund flow from the seed is traced.",
            f"Traversal stopped at depth {max_depth} / branch limit "
            f"{result.investigation.max_branches}; "
            f"{_fmt_amt(frontier_value)} {_native(result)} entered wallets that "
            "were not expanded further.",
            "Convergence points without an entity label are UNVERIFIED leads, "
            "not confirmed exchange ownership.",
            "Timestamps and amounts are on-chain facts; entity attribution and "
            "intent are not.",
        ],
        "disclaimer": (
            "ChainTrace provides public-data-based investigative leads and does "
            "not constitute a legal determination or definitive attribution of "
            "ownership."
        ),
    }


def _native(result: InvestigationResult) -> str:
    counts: Dict[str, int] = defaultdict(int)
    for e in result.graph.edges:
        if e.transfer.token_symbol:
            counts[e.transfer.token_symbol] += 1
    if not counts:
        return result.seed_address.chain.native_symbol
    return max(counts, key=counts.get)


def _dur(secs: float) -> str:
    secs = int(secs)
    if secs < 90:
        return f"{secs}s"
    if secs < 5400:
        return f"{secs // 60}m"
    return f"{secs // 3600}h"


def _headline(result, findings, seed_out, frontier) -> str:
    n = result.graph.node_count
    struct = [f for f in findings if f["type"] == "structuring"]
    conv = [f for f in findings if f["type"] == "convergence"]
    hits = [f for f in findings if f["type"] == "entity_hit"]
    tok = _native(result)
    parts = [
        f"Seed moved {_fmt_amt(seed_out)} {tok} into a network of {n} wallets."
    ]
    if struct:
        parts.append(
            f"Funds were dispersed in bursts ({struct[0]['metrics']['recipients']} "
            "recipients), consistent with layering."
        )
    if conv:
        c = conv[0]
        parts.append(
            f"They re-converge at {c['addresses'][0][:10]}... "
            f"({c['metrics']['distinct_senders']} traced wallets) — strongest lead."
        )
    if hits:
        parts.append(f"Path reaches a labelled {hits[0]['metrics']['entity_type']}.")

    # Where did the money actually concentrate? This works with zero labels.
    eps = getattr(result, "candidate_endpoints", []) or []
    top = max(eps, key=lambda e: e.taint_fraction, default=None)
    if top is not None and top.taint_fraction > 0:
        who = top.address.label or f"{top.address.address[:10]}..."
        basis = (
            "the reported amount"
            if getattr(result.investigation, "reported_amount", None)
            else "the traced funds"
        )
        parts.append(
            f"{top.taint_fraction * 100:.0f}% of {basis} "
            f"({_fmt_amt(top.tainted_value)} {tok}) concentrated at {who}"
            + (
                " where the trail ends - highest-priority lead."
                if top.is_terminal and not top.address.label
                else "."
            )
        )
    elif not conv and not hits:
        parts.append(
            f"No labelled endpoint reached; {_fmt_amt(frontier)} {tok} left the "
            "observed window across un-expanded wallets."
        )
    return " ".join(parts)
