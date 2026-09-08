from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import uuid4

from ..enums import Chain, InvestigationStatus
from .address import Address
from .transfer import Transfer
from .graph import TransactionGraph


class Investigation(BaseModel):
    investigation_id: str = Field(default_factory=lambda: str(uuid4()))
    seed_address: str
    chain: Chain
    max_depth: int = Field(default=4, ge=1, le=10)
    max_branches: int = Field(default=25, ge=1, le=100)
    token_filter: Optional[str] = Field(
        default=None, description="Optional token contract to filter"
    )
    # Case anchoring: a real report is "X stolen on DATE", not "trace this
    # wallet forever". Both optional; both dramatically cut noise.
    incident_time: Optional[datetime] = Field(
        default=None,
        description="Only follow transfers at/after this time (funds cannot "
        "move backwards from the incident)",
    )
    reported_amount: Optional[float] = Field(
        default=None,
        description="Amount reported stolen; anchors taint instead of using the "
        "seed's entire outflow",
    )
    start_time: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    status: InvestigationStatus = InvestigationStatus.PENDING
    transactions_examined: int = 0
    nodes_found: int = 0
    edges_found: int = 0
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None

    def mark_running(self) -> None:
        self.status = InvestigationStatus.RUNNING

    def mark_completed(self, graph: TransactionGraph) -> None:
        self.status = InvestigationStatus.COMPLETED
        self.completed_at = datetime.utcnow()
        self.nodes_found = graph.node_count
        self.edges_found = graph.edge_count

    def mark_failed(self, error: str) -> None:
        self.status = InvestigationStatus.FAILED
        self.completed_at = datetime.utcnow()
        self.error = error

    def mark_partial(self, error: str, graph: TransactionGraph) -> None:
        self.status = InvestigationStatus.PARTIAL
        self.completed_at = datetime.utcnow()
        self.error = error
        self.nodes_found = graph.node_count
        self.edges_found = graph.edge_count


class CandidateEndpoint(BaseModel):
    address: Address
    total_amount_received: str = Field(default="0")
    hop_count: int = 0
    unlabeled_hop_count: int = 0
    amount_concentration: float = Field(default=0.0, ge=0.0, le=1.0)
    path_directness: float = Field(default=0.0, ge=0.0, le=1.0)
    label_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # two orthogonal axes: how sure the money went here vs what this address is
    flow_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    entity_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    tainted_value: float = 0.0
    taint_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    is_terminal: bool = False
    behavior: Optional[str] = None
    behavior_confidence: float = 0.0
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    path: List[Transfer] = Field(default_factory=list)
    pattern_flags: List[str] = Field(default_factory=list)
    obfuscation_points: int = 0
    elapsed_seconds: Optional[int] = None
    evidence: List[str] = Field(default_factory=list)

    @property
    def confidence_percentage(self) -> str:
        return f"{self.confidence_score * 100:.1f}%"


class InvestigationResult(BaseModel):
    investigation: Investigation
    graph: TransactionGraph
    seed_address: Address
    candidate_endpoints: List[CandidateEndpoint] = Field(default_factory=list)
    suspicious_addresses: List[Address] = Field(default_factory=list)
    pattern_detections: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    case_summary: Dict[str, Any] = Field(default_factory=dict)
    wallet_profiles: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)

    @property
    def top_endpoints(self) -> List[CandidateEndpoint]:
        return sorted(
            self.candidate_endpoints, key=lambda x: x.confidence_score, reverse=True
        )

    @property
    def has_exchange_endpoints(self) -> bool:
        return any(
            ep.address.entity_type.value == "exchange"
            for ep in self.candidate_endpoints
        )

    @property
    def has_sanctioned_endpoints(self) -> bool:
        return any(
            ep.address.entity_type.value == "sanctioned"
            for ep in self.candidate_endpoints
        )
