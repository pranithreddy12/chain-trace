from typing import List, Dict, Any, Optional
from collections import deque

from ..domain.models.graph import TransactionGraph, GraphNode
from ..domain.models.investigation import CandidateEndpoint
from ..domain.models.address import Address
from ..domain.models.transfer import Transfer
from ..domain.enums import EntityCategory, Chain
from ..config.settings import get_settings
from .taint import dominant_path


class PathAnalyzer:
    def __init__(self):
        self.settings = get_settings()

    def find_candidate_endpoints(
        self, graph: TransactionGraph, seed_address: Address
    ) -> List[CandidateEndpoint]:
        """Where did the money end up?

        A candidate is any wallet that is (a) a labelled exchange/sanctioned
        entity, (b) already flagged as an endpoint, or (c) TERMINAL in the traced
        window (the money arrived and we did not observe it leave) while holding
        a material share of the seed's tainted value. (c) is what makes
        unlabelled chains produce leads at all.
        """
        seed_addr = (
            seed_address.address.lower()
            if seed_address.chain.is_evm
            else seed_address.address
        )

        senders = {e.transfer.normalized_from() for e in graph.edges}
        min_taint = self.settings.min_endpoint_taint_fraction

        endpoints = []
        for addr, node in graph.nodes.items():
            if addr == seed_addr:
                continue
            labelled = node.address.entity_type in (
                EntityCategory.EXCHANGE,
                EntityCategory.SANCTIONED,
            )
            terminal = addr not in senders
            material = node.taint_fraction >= min_taint
            if not (labelled or node.is_endpoint or (terminal and material)):
                continue
            endpoint = self._analyze_endpoint(graph, seed_addr, addr, node, terminal)
            if endpoint:
                endpoints.append(endpoint)

        # rank by how much of the victim's money got here, then by hop distance
        endpoints.sort(
            key=lambda e: (e.taint_fraction, -e.hop_count), reverse=True
        )
        return endpoints[: self.settings.max_candidate_endpoints]

    def _analyze_endpoint(
        self,
        graph: TransactionGraph,
        seed_addr: str,
        endpoint_addr: str,
        endpoint_node: GraphNode,
        is_terminal: bool = False,
    ) -> Optional[CandidateEndpoint]:
        # Prefer the path the MONEY took (highest taint carried at each hop)
        # over the topologically shortest one; fall back to nx when there is no
        # taint data (e.g. graphs built directly in unit tests).
        shortest_path = dominant_path(graph, seed_addr, endpoint_addr)
        if not shortest_path:
            paths = self._find_paths_to(graph, seed_addr, endpoint_addr)
            if not paths:
                return None
            shortest_path = min(paths, key=len)
        path_transfers = self._get_path_transfers(graph, shortest_path)

        # Amount actually arriving at the endpoint = the final transfer into it,
        # not the sum of every hop along the path.
        total_amount = path_transfers[-1].amount_float if path_transfers else 0.0
        original_amount = self._estimate_original_amount(
            graph, seed_addr, shortest_path
        )

        hop_count = len(shortest_path) - 1
        unlabeled_hops = sum(
            1
            for addr in shortest_path[1:-1]
            if addr in graph.nodes
            and graph.nodes[addr].address.entity_type == EntityCategory.UNKNOWN
        )

        obfuscation_points = sum(
            1
            for addr in shortest_path
            if addr in graph.nodes and graph.nodes[addr].is_obfuscation_point
        )

        elapsed = None
        if path_transfers:
            elapsed = int(
                (
                    path_transfers[-1].timestamp - path_transfers[0].timestamp
                ).total_seconds()
            )

        # Taint fraction IS the concentration when we have it: it already
        # accounts for splits along the way. Fall back to the raw ratio.
        if endpoint_node.taint_fraction > 0:
            amount_concentration = endpoint_node.taint_fraction
        else:
            amount_concentration = (
                total_amount / original_amount if original_amount > 0 else 0
            )

        return CandidateEndpoint(
            address=endpoint_node.address,
            total_amount_received=str(
                endpoint_node.tainted_value or total_amount
            ),
            hop_count=hop_count,
            unlabeled_hop_count=unlabeled_hops,
            amount_concentration=min(1.0, amount_concentration),
            path_directness=1.0 / (1.0 + unlabeled_hops),
            label_confidence=endpoint_node.address.confidence,
            tainted_value=endpoint_node.tainted_value,
            taint_fraction=endpoint_node.taint_fraction,
            is_terminal=is_terminal,
            path=path_transfers,
            obfuscation_points=obfuscation_points,
            elapsed_seconds=elapsed,
            evidence=self._generate_evidence(
                endpoint_node, path_transfers, obfuscation_points, is_terminal
            ),
        )

    def _find_paths_to(
        self, graph: TransactionGraph, from_addr: str, to_addr: str
    ) -> List[List[str]]:
        try:
            import networkx as nx

            return list(
                nx.all_simple_paths(graph.nx_graph, from_addr, to_addr, cutoff=10)
            )
        except Exception:
            return []

    def _get_path_transfers(
        self, graph: TransactionGraph, path: List[str]
    ) -> List[Transfer]:
        transfers = []
        for i in range(len(path) - 1):
            from_addr = path[i]
            to_addr = path[i + 1]
            for edge in graph.edges:
                if (
                    edge.transfer.normalized_from() == from_addr
                    and edge.transfer.normalized_to() == to_addr
                ):
                    transfers.append(edge.transfer)
                    break
        return transfers

    def _estimate_original_amount(
        self, graph: TransactionGraph, seed_addr: str, path: List[str]
    ) -> float:
        seed_outgoing = sum(
            e.transfer.amount_float
            for e in graph.edges
            if e.transfer.normalized_from() == seed_addr
        )
        return seed_outgoing

    def _generate_evidence(
        self,
        node: GraphNode,
        path_transfers: List[Transfer],
        obfuscation_points: int,
        is_terminal: bool = False,
    ) -> List[str]:
        evidence = []

        if node.taint_fraction > 0:
            evidence.append(
                f"{node.taint_fraction * 100:.1f}% of the reported funds reached here"
            )
        if is_terminal and node.address.entity_type == EntityCategory.UNKNOWN:
            evidence.append(
                "Funds arrived and were not observed leaving within the traced window"
            )

        if node.address.entity_type == EntityCategory.EXCHANGE:
            evidence.append(
                "Verified exchange label"
                if node.address.confidence >= 0.8
                else "Inferred exchange deposit"
            )
        elif node.address.entity_type == EntityCategory.SANCTIONED:
            evidence.append("OFAC/sanctions list match")

        if node.address.label:
            evidence.append(f"Label: {node.address.label}")

        if obfuscation_points > 0:
            evidence.append(f"Path crosses {obfuscation_points} obfuscation point(s)")

        if path_transfers:
            tokens = set(t.token_symbol for t in path_transfers if t.token_symbol)
            if tokens:
                evidence.append(f"Tokens: {', '.join(tokens)}")

        return evidence
