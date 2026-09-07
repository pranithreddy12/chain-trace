from typing import Dict, Set, List
from collections import defaultdict

from ..domain.models.graph import TransactionGraph, GraphNode
from ..domain.enums import EntityCategory
from ..config.settings import get_settings


class ExchangeInference:
    def __init__(self):
        self.settings = get_settings()
        self.known_exchange_hot_wallets: Set[str] = set()
        self._load_known_hot_wallets()

    def _load_known_hot_wallets(self) -> None:
        pass

    def infer(self, graph: TransactionGraph) -> Dict[str, float]:
        incoming_counts = defaultdict(int)
        incoming_from = defaultdict(set)

        for edge in graph.edges:
            to_addr = edge.transfer.normalized_to()
            from_addr = edge.transfer.normalized_from()
            incoming_counts[to_addr] += 1
            incoming_from[to_addr].add(from_addr)

        convergence_candidates = {
            addr: count for addr, count in incoming_counts.items() if count >= 5
        }

        inferred = {}
        for addr, count in convergence_candidates.items():
            if addr in graph.nodes:
                node = graph.nodes[addr]
                if node.address.entity_type == EntityCategory.EXCHANGE:
                    for source_addr in incoming_from[addr]:
                        if source_addr in graph.nodes:
                            source_node = graph.nodes[source_addr]
                            if (
                                source_node.address.entity_type
                                == EntityCategory.UNKNOWN
                            ):
                                confidence = min(0.6, count / 50.0)
                                inferred[source_addr] = confidence

        return inferred

    def register_hot_wallet(self, address: str, chain: str) -> None:
        normalized = address.lower() if chain == "ethereum" else address
        self.known_exchange_hot_wallets.add(f"{chain}:{normalized}")
