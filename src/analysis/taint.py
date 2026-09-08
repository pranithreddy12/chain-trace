"""Taint propagation — follow the victim's money, not every edge.

Haircut model (the standard first-pass forensic attribution): at each wallet the
tainted value that arrived is split across that wallet's outgoing transfers in
proportion to their value. A wallet can never forward more taint than value it
actually moved; whatever is not forwarded stays as retained taint.

This gives EVERY wallet in the graph a defensible "how much of the reported
funds reached here" number — labelled or not — which is what makes unlabelled
chains rankable.

Propagation is PER TOKEN. Units of different tokens are not commensurable - one
TRX is not one USDT - so summing them produces a meaningless "value" and ranks
wallets by whichever token happens to have the largest unit count. Each token
is therefore propagated independently against its own origin amount, and a
wallet's headline `taint_fraction` is its strongest single-token share, with
`taint_token` naming which token that is.

Deliberate simplification: propagation runs in BFS-depth order and only feeds
forward (target depth > source depth). Back/side edges still get their taint
recorded on the edge and credited to the target, but are not re-propagated, so
cycles terminate. Documented as a known limitation.
"""

from collections import defaultdict
from typing import Dict, List, Optional

from ..domain.models.graph import TransactionGraph, GraphEdge


def _token_of(edge: GraphEdge) -> str:
    return edge.transfer.token_symbol or "?"


def propagate_taint(
    graph: TransactionGraph,
    seed_addr: str,
    origin_amount: Optional[float] = None,
    origin_token: Optional[str] = None,
) -> Dict[str, float]:
    """Distribute the seed's tainted value across the graph, one token at a time.

    Writes per-token `taint_by_token` / `tainted_by_token` onto nodes, plus the
    headline `taint_fraction` / `taint_token` / `tainted_value` (the strongest
    single-token share), and `tainted_value` onto each edge in that edge's own
    token. Returns {address: tainted value in its dominant token}.

    `origin_amount` (the amount the victim reported) anchors ONE token -
    `origin_token` if given, otherwise the token the seed moved in the most
    transfers. Other tokens are anchored on the seed's own outflow in them.
    """
    if seed_addr not in graph.nodes:
        # Silently returning {} left every wallet at taint 0 and every ranking
        # empty, with nothing to say why - the same class of silent failure as
        # a swallowed fetch error. A seed that is not in its own graph is a
        # normalisation bug, so make it loud.
        raise ValueError(
            f"seed {seed_addr!r} is not a node in its own graph "
            f"({len(graph.nodes)} nodes); address normalisation mismatch"
        )

    out_edges: Dict[str, Dict[str, List[GraphEdge]]] = defaultdict(
        lambda: defaultdict(list)
    )
    tokens = set()
    for e in graph.edges:
        f, t = e.transfer.normalized_from(), e.transfer.normalized_to()
        if f in graph.nodes and t in graph.nodes:
            tok = _token_of(e)
            out_edges[tok][f].append(e)
            tokens.add(tok)

    # reset any prior run
    for e in graph.edges:
        e.tainted_value = 0.0
    for n in graph.nodes.values():
        n.taint_by_token = {}
        n.tainted_by_token = {}
        n.tainted_value = 0.0
        n.taint_fraction = 0.0
        n.taint_token = None

    if origin_amount is not None and origin_token is None:
        seed_tok_counts: Dict[str, int] = defaultdict(int)
        for tok in tokens:
            seed_tok_counts[tok] = len(out_edges[tok].get(seed_addr, []))
        origin_token = max(seed_tok_counts, key=seed_tok_counts.get, default=None)

    depth = {a: n.depth for a, n in graph.nodes.items()}
    order = sorted(graph.nodes, key=lambda a: depth[a])

    for tok in sorted(tokens):
        outs_by_addr = out_edges[tok]
        seed_out = sum(
            e.transfer.amount_float for e in outs_by_addr.get(seed_addr, [])
        )
        if origin_amount is not None and tok == origin_token:
            origin = origin_amount
        else:
            origin = seed_out
        if origin <= 0:
            continue

        received: Dict[str, float] = defaultdict(float)
        forwardable: Dict[str, float] = defaultdict(float)
        received[seed_addr] = origin
        forwardable[seed_addr] = origin

        for addr in order:
            avail = forwardable.get(addr, 0.0)
            if avail <= 0:
                continue
            outs = outs_by_addr.get(addr, [])
            total_out = sum(e.transfer.amount_float for e in outs)
            if total_out <= 0:
                continue  # terminal in this token: taint stays put

            carried = min(avail, total_out)
            for e in outs:
                share = e.transfer.amount_float / total_out
                moved = carried * share
                e.tainted_value += moved
                tgt = e.transfer.normalized_to()
                received[tgt] += moved
                if depth.get(tgt, 0) > depth[addr]:
                    forwardable[tgt] += moved

        for addr, value in received.items():
            node = graph.nodes.get(addr)
            if node is None:
                continue
            node.tainted_by_token[tok] = round(value, 8)
            node.taint_by_token[tok] = round(min(1.0, value / origin), 6)

    out: Dict[str, float] = {}
    for addr, node in graph.nodes.items():
        if not node.taint_by_token:
            continue
        # headline = strongest single-token share; never a cross-token sum
        tok = max(node.taint_by_token, key=node.taint_by_token.get)
        node.taint_token = tok
        node.taint_fraction = node.taint_by_token[tok]
        node.tainted_value = node.tainted_by_token[tok]
        out[addr] = node.tainted_value
    return out


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
