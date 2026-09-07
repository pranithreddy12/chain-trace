from typing import Dict, List, Any, Set
from collections import defaultdict
from datetime import timedelta

from ..domain.models.graph import TransactionGraph
from ..domain.models.transfer import Transfer
from ..domain.enums import PatternType
from ..config.settings import get_settings


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
        }

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
