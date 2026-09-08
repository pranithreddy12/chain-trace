from typing import Dict, List, Any, Set
from collections import defaultdict
from datetime import timedelta

from ..domain.models.graph import TransactionGraph
from ..domain.models.transfer import Transfer
from ..domain.enums import PatternType
from ..config.settings import get_settings


def _dur_s(secs: float) -> str:
    secs = int(secs)
    if secs < 120:
        return f"{secs}s"
    if secs < 7200:
        return f"{secs // 60}m"
    return f"{secs // 3600}h"


def _looks_like(candidate: str, genuine: str, edge_chars: int = 4) -> bool:
    """Two addresses a human would confuse when glancing at a truncated form."""
    if len(candidate) < edge_chars * 2 or len(genuine) < edge_chars * 2:
        return False
    return (
        candidate[:edge_chars].lower() == genuine[:edge_chars].lower()
        and candidate[-edge_chars:].lower() == genuine[-edge_chars:].lower()
    )


class PatternDetector:
    def __init__(self):
        self.settings = get_settings()

    def detect_all(self, graph: TransactionGraph) -> Dict[str, List[Dict[str, Any]]]:
        return {
            PatternType.FAN_OUT.value: self.detect_fan_out(graph),
            PatternType.HOP_VELOCITY.value: self.detect_hop_velocity(graph),
            PatternType.MICRO_FAN_OUT.value: self.detect_micro_fan_out(graph),
            PatternType.RAPID_MULTI_HOP.value: self.detect_rapid_multi_hop(graph),
            PatternType.PEEL_BEHAVIOR.value: self.detect_peel_behavior(graph),
            PatternType.ADDRESS_POISONING.value: self.detect_address_poisoning(graph),
        }

    def detect_address_poisoning(
        self, graph: TransactionGraph
    ) -> List[Dict[str, Any]]:
        """Dust sprayed at many lookalike addresses.

        The scam: send a worthless amount from an address crafted to share its
        first and last characters with one the victim really uses, so the
        victim later copies the poisoned address out of their history and sends
        real funds to it.

        Requires two independent signals (spec 18): a burst of dust to many
        distinct recipients, AND at least one of those recipients being a
        lookalike of a genuine counterparty. Volume alone is not enough - a
        legitimate airdrop also sprays.
        """
        cfg = get_settings()
        dust_to: Dict[str, set] = defaultdict(set)
        dust_times: Dict[str, List[Any]] = defaultdict(list)
        dust_amounts: Dict[str, List[str]] = defaultdict(list)
        all_genuine: set = set()

        for edge in graph.edges:
            frm = edge.transfer.normalized_from()
            to = edge.transfer.normalized_to()
            if edge.transfer.amount_float < cfg.dust_amount_threshold:
                dust_to[frm].add(to)
                dust_times[frm].append(edge.transfer.timestamp)
                dust_amounts[frm].append(edge.transfer.amount)
            else:
                all_genuine.add(frm)
                all_genuine.add(to)

        detections = []
        for addr, targets in dust_to.items():
            if len(targets) < cfg.poisoning_min_dust_sends:
                continue

            # The lookalike test compares against every genuine address in the
            # case, not just this wallet's own counterparties: the address being
            # impersonated usually belongs to the victim, not to the sprayer.
            lookalikes = [
                (t, g)
                for t in targets
                for g in all_genuine
                if t != g and _looks_like(t, g)
            ]

            times = sorted(x for x in dust_times[addr] if x)
            span = (times[-1] - times[0]).total_seconds() if len(times) > 1 else 0.0
            amounts = dust_amounts[addr]
            uniform = len(set(amounts)) == 1 and len(amounts) > 1

            signals = [f"{len(targets)} dust transfers to distinct addresses"]
            if lookalikes:
                signals.append(
                    f"{len(lookalikes)} of them impersonate a real address in "
                    f"this case (same leading and trailing characters)"
                )
            if uniform:
                # a spray is scripted: every send is the same token-dust amount.
                # Real payments to hundreds of parties are not byte-identical.
                signals.append(
                    f"every send is exactly {amounts[0]} - a scripted spray, "
                    f"not payments"
                )
            if len(times) > 1 and span <= 86400:
                signals.append(f"all sent within {_dur_s(span)}")
            if len(signals) < 2:
                continue

            detections.append(
                {
                    "address": addr,
                    "dust_targets": len(targets),
                    "lookalike_pairs": [
                        {"impostor": a, "impersonates": b} for a, b in lookalikes[:5]
                    ],
                    "burst_seconds": round(span, 1),
                    "uniform_amount": amounts[0] if uniform else None,
                    "signals": signals,
                }
            )
        return detections

    def detect_fan_out(self, graph: TransactionGraph) -> List[Dict[str, Any]]:
        detections = []
        outgoing_counts = defaultdict(set)
        outgoing_amounts = defaultdict(list)

        for edge in graph.edges:
            from_addr = edge.transfer.normalized_from()
            to_addr = edge.transfer.normalized_to()
            outgoing_counts[from_addr].add(to_addr)
            outgoing_amounts[from_addr].append(edge.transfer.amount_float)

        for addr, recipients in outgoing_counts.items():
            if len(recipients) >= 5:
                amounts = outgoing_amounts[addr]
                detections.append(
                    {
                        "address": addr,
                        "pattern": PatternType.FAN_OUT.value,
                        "unique_recipients": len(recipients),
                        "total_amount": sum(amounts),
                        "avg_amount": sum(amounts) / len(amounts) if amounts else 0,
                        "recipients": list(recipients)[:20],
                    }
                )

        return detections

    def detect_hop_velocity(self, graph: TransactionGraph) -> List[Dict[str, Any]]:
        """How fast an address forwards funds it received.

        Measures, per address, the shortest gap between an incoming transfer and
        the first later outgoing transfer that moves a comparable amount
        (>= 50% of what came in). This is the spec's "received at 12:00,
        forwarded at 12:03" signal — NOT the gap between arbitrary timestamps.
        """
        thr = self.settings.hop_velocity_threshold_seconds
        inflows: Dict[str, list] = defaultdict(list)
        outflows: Dict[str, list] = defaultdict(list)

        for edge in graph.edges:
            t = edge.transfer
            inflows[t.normalized_to()].append((t.timestamp, t.amount_float))
            outflows[t.normalized_from()].append((t.timestamp, t.amount_float))

        detections = []
        for addr, ins in inflows.items():
            outs = sorted(outflows.get(addr, []))
            if not outs:
                continue
            best = None  # (gap_s, in_ts, out_ts, in_amt, out_amt)
            for in_ts, in_amt in sorted(ins):
                for out_ts, out_amt in outs:
                    if out_ts <= in_ts or out_amt < in_amt * 0.5:
                        continue
                    gap = (out_ts - in_ts).total_seconds()
                    if best is None or gap < best[0]:
                        best = (gap, in_ts, out_ts, in_amt, out_amt)
                    break  # first qualifying forward after this inflow
            if best and best[0] <= thr:
                gap, in_ts, out_ts, in_amt, out_amt = best
                detections.append(
                    {
                        "address": addr,
                        "pattern": PatternType.HOP_VELOCITY.value,
                        "received_to_forwarded_seconds": round(gap, 1),
                        "received_amount": round(in_amt, 2),
                        "forwarded_amount": round(out_amt, 2),
                        "received_at": in_ts.isoformat(),
                        "forwarded_at": out_ts.isoformat(),
                    }
                )

        return detections

    def detect_micro_fan_out(self, graph: TransactionGraph) -> List[Dict[str, Any]]:
        detections = []
        outgoing = defaultdict(list)

        for edge in graph.edges:
            from_addr = edge.transfer.normalized_from()
            outgoing[from_addr].append(edge.transfer)

        for addr, transfers in outgoing.items():
            if len(transfers) >= self.settings.micro_fanout_threshold:
                amounts = [t.amount_float for t in transfers]
                avg_amount = sum(amounts) / len(amounts) if amounts else 0
                unique_recipients = len(set(t.normalized_to() for t in transfers))

                if (
                    avg_amount < 100
                    and unique_recipients >= self.settings.micro_fanout_threshold
                ):
                    detections.append(
                        {
                            "address": addr,
                            "pattern": PatternType.MICRO_FAN_OUT.value,
                            "transfer_count": len(transfers),
                            "unique_recipients": unique_recipients,
                            "avg_amount": avg_amount,
                            "total_amount": sum(amounts),
                        }
                    )

        return detections

    def detect_rapid_multi_hop(self, graph: TransactionGraph) -> List[Dict[str, Any]]:
        detections = []
        paths = self._find_paths(graph)

        for path in paths:
            if len(path) >= 3:
                total_time = 0
                valid_hops = 0

                for i in range(len(path) - 1):
                    from_addr = path[i]
                    to_addr = path[i + 1]
                    edges = [
                        e
                        for e in graph.edges
                        if e.transfer.normalized_from() == from_addr
                        and e.transfer.normalized_to() == to_addr
                    ]
                    if edges:
                        timestamps = [e.transfer.timestamp for e in edges]
                        if len(timestamps) >= 2:
                            interval = (
                                max(timestamps) - min(timestamps)
                            ).total_seconds()
                            total_time += interval
                            valid_hops += 1

                if valid_hops >= 2:
                    avg_hop_time = total_time / valid_hops
                    if avg_hop_time <= self.settings.hop_velocity_threshold_seconds * 2:
                        detections.append(
                            {
                                "path": path,
                                "pattern": PatternType.RAPID_MULTI_HOP.value,
                                "hop_count": len(path) - 1,
                                "avg_hop_time_seconds": avg_hop_time,
                                "total_time_seconds": total_time,
                            }
                        )

        return detections

    def detect_peel_behavior(self, graph: TransactionGraph) -> List[Dict[str, Any]]:
        # Pass-through / peel: a wallet forwards most of what it received,
        # shedding only a small fraction at each hop while preserving the bulk
        # of the original value. forward_ratio near (but below) 1.0.
        min_forward = self.settings.peel_forward_ratio_min
        detections = []
        seen: Set[str] = set()

        for edge in graph.edges:
            from_addr = edge.transfer.normalized_from()
            if from_addr in seen:
                continue

            incoming = sum(
                e.transfer.amount_float
                for e in graph.edges
                if e.transfer.normalized_to() == from_addr
            )
            out_edges = [
                e.transfer.amount_float
                for e in graph.edges
                if e.transfer.normalized_from() == from_addr
            ]

            if incoming <= 0 or not out_edges:
                continue

            # Genuine peel: one dominant outgoing transfer carries most of the
            # received value onward AND a smaller amount is actually shed AND the
            # wallet is a pass-through, not an aggregation hub (total out ~= total
            # in). Excludes pure relays (ratio 1.0, nothing peeled) and hubs
            # whose outflow far exceeds what we observed coming in.
            total_out = sum(out_edges)
            main_forward = max(out_edges)
            forward_ratio = main_forward / incoming
            peeled = incoming - main_forward
            is_pass_through = abs(total_out - incoming) <= incoming * 0.15
            if (
                min_forward <= forward_ratio < 1.0
                and peeled > 0
                and is_pass_through
            ):
                seen.add(from_addr)
                detections.append(
                    {
                        "address": from_addr,
                        "pattern": PatternType.PEEL_BEHAVIOR.value,
                        "incoming_amount": round(incoming, 2),
                        "outgoing_amount": round(total_out, 2),
                        "main_forward_amount": round(main_forward, 2),
                        "value_retained": round(forward_ratio, 3),
                        "peeled_amount": round(peeled, 2),
                    }
                )

        return detections

    def _find_paths(self, graph: TransactionGraph) -> List[List[str]]:
        paths = []
        for node in graph.nodes.values():
            if node.is_seed:
                paths.extend(graph.get_paths_to_endpoints(max_paths=5))
        return paths
