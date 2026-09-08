"""Taint propagation — follow the victim's money, not every edge.

Haircut model (the standard first-pass forensic attribution): at each wallet the
tainted value that arrived is split across that wallet's outgoing transfers in
proportion to their value. A wallet can never forward more taint than value it
actually moved; whatever is not forwarded stays as retained taint.

This gives EVERY wallet in the graph a defensible "how much of the reported
funds reached here" number — labelled or not — which is what makes unlabelled
chains rankable.

Deliberate simplification: propagation runs in BFS-depth order and only feeds
forward (target depth > source depth). Back/side edges still get their taint
recorded on the edge and credited to the target, but are not re-propagated, so
cycles terminate. Documented as a known limitation.
"""

from collections import defaultdict
from typing import Dict, List, Optional

from ..domain.models.graph import TransactionGraph, GraphEdge


def propagate_taint(
    graph: TransactionGraph,
    seed_addr: str,
    origin_amount: Optional[float] = None,
) -> Dict[str, float]:
    """Distribute the seed's tainted value across the graph.

    Writes `tainted_value` / `taint_fraction` onto nodes and `tainted_value`
    onto edges. Returns {address: tainted_value}.
    """
    if seed_addr not in graph.nodes:
        return {}

    out_edges: Dict[str, List[GraphEdge]] = defaultdict(list)
    for e in graph.edges:
        f = e.transfer.normalized_from()
        if f in graph.nodes and e.transfer.normalized_to() in graph.nodes:
            out_edges[f].append(e)

    if origin_amount is None:
        origin_amount = sum(
            e.transfer.amount_float for e in out_edges.get(seed_addr, [])
        )
    if origin_amount <= 0:
        origin_amount = 1.0  # degenerate graph; keeps fractions well-defined

    received: Dict[str, float] = defaultdict(float)
    forwardable: Dict[str, float] = defaultdict(float)
    received[seed_addr] = origin_amount
    forwardable[seed_addr] = origin_amount

    # reset any prior run
    for e in graph.edges:
        e.tainted_value = 0.0

    depth = {a: n.depth for a, n in graph.nodes.items()}
    for addr in sorted(graph.nodes, key=lambda a: depth[a]):
        avail = forwardable.get(addr, 0.0)
        if avail <= 0:
            continue
        outs = out_edges.get(addr, [])
        total_out = sum(e.transfer.amount_float for e in outs)
        if total_out <= 0:
            continue  # terminal wallet: taint stays put

        carried = min(avail, total_out)
        for e in outs:
            share = e.transfer.amount_float / total_out
            moved = carried * share
            e.tainted_value += moved
            tgt = e.transfer.normalized_to()
            received[tgt] += moved
            if depth.get(tgt, 0) > depth[addr]:
                forwardable[tgt] += moved

    for addr, node in graph.nodes.items():
        node.tainted_value = round(received.get(addr, 0.0), 8)
        node.taint_fraction = round(
            min(1.0, node.tainted_value / origin_amount), 6
        )

    return dict(received)


def dominant_path(
    graph: TransactionGraph, seed_addr: str, target_addr: str, max_hops: int = 12
) -> List[str]:
    """Walk back from `target` along the highest-taint inbound edge each time.

    This is the path the *money* took, not the topologically shortest one.
    Returns [seed, ..., target], or [] if it can't reach the seed.
    """
    if target_addr not in graph.nodes or seed_addr not in graph.nodes:
        return []
    if target_addr == seed_addr:
        return [seed_addr]

    in_edges: Dict[str, List[GraphEdge]] = defaultdict(list)
    for e in graph.edges:
        in_edges[e.transfer.normalized_to()].append(e)

    depth = {a: n.depth for a, n in graph.nodes.items()}
    path = [target_addr]
    seen = {target_addr}
    cur = target_addr

    for _ in range(max_hops):
        candidates = [
            e
            for e in in_edges.get(cur, [])
            if e.transfer.normalized_from() not in seen
            and depth.get(e.transfer.normalized_from(), 0) < depth.get(cur, 0)
        ]
        if not candidates:
            return []
        best = max(
            candidates,
            key=lambda e: (e.tainted_value, e.transfer.amount_float),
        )
        cur = best.transfer.normalized_from()
        seen.add(cur)
        path.append(cur)
        if cur == seed_addr:
            return list(reversed(path))
    return []
