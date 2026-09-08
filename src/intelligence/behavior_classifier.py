"""Label-independent wallet classification.

Entity labels are sparse (and absent entirely on Tron), so the pipeline must be
able to say something useful about an address from its BEHAVIOUR alone.

Every classification requires at least TWO independent signals before it asserts
a type (spec 18: no single heuristic as proof), and everything produced here is
Tier-3/Tier-4 evidence: confidence is capped well below a real label and the
wording always carries UNVERIFIED. This narrows the search; it never claims
ownership.

The strongest label-free signal is the SHARED SWEEP DESTINATION: several traced
wallets each sweeping their balance to the same address is the on-chain shape of
exchange deposit infrastructure, and it needs no external data at all.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from ..domain.models.graph import TransactionGraph

EXCHANGE_DEPOSIT = "exchange_deposit"
COLLECTOR = "collector"
DISTRIBUTOR = "distributor"
PASS_THROUGH = "pass_through"
HOLDING = "holding"

_PRIORITY = [EXCHANGE_DEPOSIT, COLLECTOR, DISTRIBUTOR, PASS_THROUGH, HOLDING]

SWEEP_DOMINANCE = 0.85
SWEEP_SHARED_SENDERS = 3
FAST_TURNAROUND_S = 3600
COLLECTOR_MIN_SENDERS = 4
DISTRIBUTOR_MIN_RECIPIENTS = 8
HOLDING_MIN_TAINT = 0.02
COLLECTOR_MIN_TAINT = 0.05


@dataclass
class WalletProfile:
    address: str
    behavior: str
    confidence: float
    signals: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_exchange_deposit(self) -> bool:
        return self.behavior == EXCHANGE_DEPOSIT


def classify_wallets(
    graph: TransactionGraph,
    pattern_detections: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, WalletProfile]:
    """Classify every non-seed wallet in the graph from behaviour alone."""
    pattern_detections = pattern_detections or {}

    senders: Dict[str, Set[str]] = defaultdict(set)
    recipients: Dict[str, Set[str]] = defaultdict(set)
    in_value: Dict[str, float] = defaultdict(float)
    out_value: Dict[str, float] = defaultdict(float)
    out_to: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for e in graph.edges:
        f, t = e.transfer.normalized_from(), e.transfer.normalized_to()
        if f not in graph.nodes or t not in graph.nodes:
            continue
        amt = e.transfer.amount_float
        senders[t].add(f)
        recipients[f].add(t)
        in_value[t] += amt
        out_value[f] += amt
        out_to[f][t] += amt

    turnaround = {
        d["address"]: d["received_to_forwarded_seconds"]
        for d in pattern_detections.get("hop_velocity", [])
    }
    fan_out = {d["address"] for d in pattern_detections.get("fan_out", [])}
    micro_fan = {d["address"] for d in pattern_detections.get("micro_fan_out", [])}
    peeling = {d["address"] for d in pattern_detections.get("peel_behavior", [])}

    profiles: Dict[str, WalletProfile] = {}
    for addr, node in graph.nodes.items():
        if node.is_seed:
            continue
        p = _classify_one(
            addr, node, senders, recipients, in_value, out_value, out_to,
            turnaround, fan_out, micro_fan, peeling,
        )
        if p:
            profiles[addr] = p
    return profiles


def _classify_one(
    addr, node, senders, recipients, in_value, out_value, out_to,
    turnaround, fan_out, micro_fan, peeling,
) -> Optional[WalletProfile]:
    n_in = len(senders.get(addr, ()))
    n_out = len(recipients.get(addr, ()))
    v_in = in_value.get(addr, 0.0)
    v_out = out_value.get(addr, 0.0)
    terminal = n_out == 0
    taint = node.taint_fraction
    taint_tok = node.taint_token or "traced token"
    gap = turnaround.get(addr)

    dests = out_to.get(addr, {})
    top_dest, top_amt = (
        max(dests.items(), key=lambda kv: kv[1]) if dests else (None, 0.0)
    )
    dominance = (top_amt / v_out) if v_out > 0 else 0.0
    co_senders = len(senders.get(top_dest, ())) if top_dest else 0

    metrics = {
        "distinct_senders": n_in,
        "distinct_recipients": n_out,
        "value_in": round(v_in, 2),
        "value_out": round(v_out, 2),
        "taint_fraction": taint,
        "dominant_out_share": round(dominance, 3),
        "shared_destination_senders": co_senders,
        "turnaround_seconds": gap,
    }

    candidates: List[WalletProfile] = []

    # exchange deposit: sweeps to one place AND that place is shared with other
    # traced wallets. Both required - either alone is far too common.
    sig = []
    if dominance >= SWEEP_DOMINANCE and n_out <= 2 and top_dest:
        sig.append(
            f"forwards {dominance * 100:.0f}% of its outflow to a single destination"
        )
    if co_senders >= SWEEP_SHARED_SENDERS:
        sig.append(
            f"that destination is also swept to by {co_senders - 1} other traced wallets"
        )
    if len(sig) >= 2:
        conf = 0.45
        if gap is not None and gap <= FAST_TURNAROUND_S:
            sig.append(f"swept onward {int(gap)}s after receiving")
            conf += 0.10
        if n_in >= 3:
            sig.append(f"receives from {n_in} distinct wallets")
            conf += 0.05
        candidates.append(
            WalletProfile(addr, EXCHANGE_DEPOSIT, min(0.60, conf), sig, metrics)
        )

    # collector / consolidation point
    sig = []
    if n_in >= COLLECTOR_MIN_SENDERS:
        sig.append(f"receives from {n_in} distinct traced wallets")
    if terminal or n_out <= 2:
        sig.append(
            "no onward transfers observed"
            if terminal
            else f"only {n_out} onward destination(s)"
        )
    if taint >= COLLECTOR_MIN_TAINT:
        sig.append(f"holds {taint * 100:.1f}% of the traced {taint_tok}")
    if len(sig) >= 2 and n_in >= COLLECTOR_MIN_SENDERS:
        candidates.append(
            WalletProfile(
                addr, COLLECTOR, min(0.55, 0.35 + 0.05 * len(sig)), sig, metrics
            )
        )

    # distributor / structuring hub
    sig = []
    if addr in fan_out:
        sig.append(f"disperses to {n_out} distinct recipients")
    if addr in micro_fan:
        sig.append("many small outgoing transfers (micro fan-out)")
    if n_out >= DISTRIBUTOR_MIN_RECIPIENTS:
        sig.append(f"{n_out} onward destinations")
    if len(sig) >= 2:
        candidates.append(
            WalletProfile(
                addr, DISTRIBUTOR, min(0.55, 0.35 + 0.05 * len(sig)), sig, metrics
            )
        )

    # pass-through / mule
    sig = []
    if v_in > 0 and (addr in peeling or v_out / v_in >= 0.7):
        sig.append(f"forwards {min(999.0, v_out / v_in * 100):.0f}% of what it receives")
    if gap is not None and gap <= 300:
        sig.append(f"forwards within {int(gap)}s of receiving")
    if n_in <= 3 and 0 < n_out <= 3:
        sig.append("narrow in/out fan (relay-shaped)")
    if len(sig) >= 2:
        candidates.append(
            WalletProfile(
                addr, PASS_THROUGH, min(0.50, 0.30 + 0.05 * len(sig)), sig, metrics
            )
        )

    # holding / dead end
    sig = []
    if terminal:
        sig.append("funds arrived and were not observed leaving")
    if taint >= HOLDING_MIN_TAINT:
        sig.append(f"holds {taint * 100:.1f}% of the traced {taint_tok}")
    if len(sig) >= 2:
        candidates.append(
            WalletProfile(
                addr, HOLDING, min(0.45, 0.25 + 0.10 * len(sig)), sig, metrics
            )
        )

    if not candidates:
        return None
    order = {b: i for i, b in enumerate(_PRIORITY)}
    candidates.sort(key=lambda p: (order.get(p.behavior, 99), -p.confidence))
    return candidates[0]
