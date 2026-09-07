from typing import List, Optional
from datetime import datetime

from ..domain.models.investigation import (
    Investigation,
    InvestigationResult,
    CandidateEndpoint,
)
from ..domain.models.graph import TransactionGraph, GraphNode
from ..domain.models.address import Address
from ..domain.models.transfer import Transfer
from ..domain.enums import Chain, EntityCategory, NodeType
from ..application.trace_service import TraceEngine, trace_funds
from ..intelligence.label_matcher import LabelMatcher
from ..intelligence.exchange_inference import ExchangeInference
from ..intelligence.mixer_bridge_detector import MixerBridgeDetector
from ..analysis.pattern_detector import PatternDetector
from ..analysis.path_analyzer import PathAnalyzer
from ..analysis.scoring import ScoringEngine
from ..analysis.case_summary import build_case_summary
from ..persistence.repositories import InvestigationRepository


class InvestigationService:
    def __init__(self):
        self.trace_engine = TraceEngine()
        self.label_matcher = LabelMatcher()
        self.exchange_inference = ExchangeInference()
        self.mixer_detector = MixerBridgeDetector()
        self.pattern_detector = PatternDetector()
        self.path_analyzer = PathAnalyzer()
        self.scoring = ScoringEngine()
        self.inv_repo = InvestigationRepository()

    async def run_investigation(
        self,
        seed_address: str,
        chain: Chain,
        max_depth: int = 4,
        max_branches: int = 25,
        token_filter: Optional[str] = None,
    ) -> InvestigationResult:
        result = await self.trace_engine.trace(
            seed_address, chain, max_depth, max_branches, token_filter
        )

        await self._enrich_with_intelligence(result)
        self._detect_patterns(result)
        self._analyze_paths(result)
        self._score_endpoints(result)
        result.case_summary = build_case_summary(result)

        self.inv_repo.save_candidate_endpoints(
            result.investigation.investigation_id, result.candidate_endpoints
        )

        return result

    async def _enrich_with_intelligence(self, result: InvestigationResult) -> None:
        for node in result.graph.nodes.values():
            if not node.address.is_labeled:
                matched = await self.label_matcher.match(node.address)
                if matched:
                    node.address = matched

            if node.address.entity_type == EntityCategory.EXCHANGE:
                node.node_type = NodeType.EXCHANGE
                node.is_endpoint = True
            elif node.address.entity_type == EntityCategory.SANCTIONED:
                node.node_type = NodeType.SANCTIONED_ADDRESS
                node.is_endpoint = True
            elif node.address.entity_type == EntityCategory.MIXER:
                node.node_type = NodeType.MIXER
                node.is_obfuscation_point = True
            elif node.address.entity_type == EntityCategory.BRIDGE:
                node.node_type = NodeType.BRIDGE
                node.is_obfuscation_point = True

        inferred = self.exchange_inference.infer(result.graph)
        for addr, confidence in inferred.items():
            if addr in result.graph.nodes:
                node = result.graph.nodes[addr]
                if node.address.entity_type == EntityCategory.UNKNOWN:
                    node.address.entity_type = EntityCategory.EXCHANGE
                    node.address.confidence = max(node.address.confidence, confidence)
                    node.address.label = (
                        node.address.label or "Inferred Exchange Deposit"
                    )
                    node.node_type = NodeType.EXCHANGE
                    node.is_endpoint = True

        mixer_nodes = self.mixer_detector.detect(result.graph)
        for addr in mixer_nodes:
            if addr in result.graph.nodes:
                node = result.graph.nodes[addr]
                node.address.entity_type = EntityCategory.MIXER
                node.node_type = NodeType.MIXER
                node.is_obfuscation_point = True

    def _detect_patterns(self, result: InvestigationResult) -> None:
        patterns = self.pattern_detector.detect_all(result.graph)
        result.pattern_detections = patterns

        for pattern_type, detections in patterns.items():
            for detection in detections:
                addr = detection.get("address")
                if addr and addr in result.graph.nodes:
                    node = result.graph.nodes[addr]
                    node.pattern_flags.append(pattern_type)
                    if pattern_type in [
                        "fan_out",
                        "rapid_multi_hop",
                        "peel_behavior",
                    ]:
                        node.is_suspicious = True
                        node.node_type = NodeType.SUSPICIOUS_WALLET

    def _analyze_paths(self, result: InvestigationResult) -> None:
        endpoints = self.path_analyzer.find_candidate_endpoints(
            result.graph, result.seed_address
        )
        result.candidate_endpoints = endpoints

        for node in result.graph.nodes.values():
            if node.is_endpoint:
                node.node_type = (
                    NodeType.EXCHANGE
                    if node.address.entity_type == EntityCategory.EXCHANGE
                    else NodeType.UNKNOWN
                )

    def _score_endpoints(self, result: InvestigationResult) -> None:
        for endpoint in result.candidate_endpoints:
            score_result = self.scoring.calculate(
                endpoint, result.graph, result.seed_address
            )
            endpoint.confidence_score = score_result["score"]
            endpoint.path_directness = score_result["components"]["path_directness"]
            endpoint.amount_concentration = score_result["components"][
                "amount_concentration"
            ]
            endpoint.label_confidence = score_result["components"]["label_confidence"]
            endpoint.evidence = score_result["reasons"]
            endpoint.pattern_flags = score_result.get("pattern_flags", [])

        result.candidate_endpoints.sort(key=lambda x: x.confidence_score, reverse=True)

        if result.candidate_endpoints:
            top = result.candidate_endpoints[0]
            paths = result.graph.get_paths_to_endpoints(max_paths=1)
            if paths:
                result.graph.highlight_path(paths[0])
                top.path = self._get_transfers_for_path(result.graph, paths[0])

    def _get_transfers_for_path(
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
