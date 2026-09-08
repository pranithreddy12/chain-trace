from pydantic import BaseModel, Field, PrivateAttr
from typing import Dict, List, Optional, Set
from datetime import datetime
import networkx as nx

from ..enums import Chain, NodeType, EdgeType
from .address import Address
from .transfer import Transfer


class GraphNode(BaseModel):
    address: Address
    node_type: NodeType = NodeType.UNKNOWN
    depth: int = 0
    incoming_amount: str = "0"
    outgoing_amount: str = "0"
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    pattern_flags: List[str] = Field(default_factory=list)
    is_seed: bool = False
    is_endpoint: bool = False
    is_suspicious: bool = False
    is_obfuscation_point: bool = False
    # taint = how much of the SEED's money reached this wallet (haircut model).
    # Tracked PER TOKEN - units of different tokens are not commensurable, so
    # they are never summed. The headline pair below is the strongest single
    # token's share, and taint_token names which token that is.
    taint_by_token: Dict[str, float] = Field(default_factory=dict)
    tainted_by_token: Dict[str, float] = Field(default_factory=dict)
    taint_token: Optional[str] = None
    tainted_value: float = 0.0
    taint_fraction: float = 0.0
    # label-independent behavioural verdict (UNVERIFIED, Tier-3/4)
    behavior: Optional[str] = None
    behavior_confidence: float = 0.0
    behavior_signals: List[str] = Field(default_factory=list)

    @property
    def display_id(self) -> str:
        return self.address.address


class GraphEdge(BaseModel):
    transfer: Transfer
    edge_type: EdgeType = EdgeType.TOKEN_TRANSFER
    is_highlighted: bool = False
    is_suspicious: bool = False
    path_rank: Optional[int] = None
    # portion of the seed's tainted value carried by this transfer
    tainted_value: float = 0.0


class TransactionGraph(BaseModel):
    nodes: Dict[str, GraphNode] = Field(default_factory=dict)
    edges: List[GraphEdge] = Field(default_factory=list)
    seed_address: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, **data):
        super().__init__(**data)
        self._nx_graph: Optional[nx.DiGraph] = None

    @property
    def nx_graph(self) -> nx.DiGraph:
        if self._nx_graph is None:
            self._build_nx_graph()
        return self._nx_graph

    def _build_nx_graph(self) -> None:
        self._nx_graph = nx.DiGraph()
        for node_id, node in self.nodes.items():
            self._nx_graph.add_node(node_id, data=node)
        for edge in self.edges:
            from_addr = edge.transfer.normalized_from()
            to_addr = edge.transfer.normalized_to()
            if from_addr in self.nodes and to_addr in self.nodes:
                self._nx_graph.add_edge(
                    from_addr,
                    to_addr,
                    data=edge,
                    amount=edge.transfer.amount_float,
                    token=edge.transfer.token_symbol or "native",
                    timestamp=edge.transfer.timestamp,
                )

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.address.address] = node
        self._nx_graph = None

    _edge_keys: Set[tuple] = PrivateAttr(default_factory=set)

    def add_edge(self, edge: GraphEdge) -> None:
        # One on-chain transfer must never be counted twice. Paginated provider
        # responses and retries can hand back the same transfer more than once,
        # which silently doubled amounts and taint. A tx hash can legitimately
        # carry several transfers (batch payouts), so the identity is the hash
        # plus the from/to/token it moved.
        t = edge.transfer
        key = (
            t.transaction_hash,
            t.normalized_from(),
            t.normalized_to(),
            t.token_symbol,
            t.amount,
        )
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append(edge)
        self._nx_graph = None

    def get_node(self, address: str) -> Optional[GraphNode]:
        normalized = address.lower() if address.startswith("0x") else address
        return self.nodes.get(normalized)

    def get_outgoing_edges(self, address: str) -> List[GraphEdge]:
        normalized = address.lower() if address.startswith("0x") else address
        return [e for e in self.edges if e.transfer.normalized_from() == normalized]

    def get_incoming_edges(self, address: str) -> List[GraphEdge]:
        normalized = address.lower() if address.startswith("0x") else address
        return [e for e in self.edges if e.transfer.normalized_to() == normalized]

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def get_paths_to_endpoints(self, max_paths: int = 10) -> List[List[str]]:
        if not self.seed_address or self.seed_address not in self.nx_graph:
            return []

        endpoints = [
            addr
            for addr, node in self.nodes.items()
            if node.is_endpoint and addr in self.nx_graph
        ]

        paths = []
        for endpoint in endpoints:
            try:
                for path in nx.all_simple_paths(
                    self.nx_graph, self.seed_address, endpoint, cutoff=10
                ):
                    paths.append(path)
                    if len(paths) >= max_paths:
                        return paths
            except nx.NetworkXNoPath:
                continue
        return paths

    def highlight_path(self, path: List[str]) -> None:
        for i in range(len(path) - 1):
            from_addr = path[i]
            to_addr = path[i + 1]
            for edge in self.edges:
                if (
                    edge.transfer.normalized_from() == from_addr
                    and edge.transfer.normalized_to() == to_addr
                ):
                    edge.is_highlighted = True
                    break

        for addr in path:
            if addr in self.nodes:
                self.nodes[addr].is_suspicious = True
