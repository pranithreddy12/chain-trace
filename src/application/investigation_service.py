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
from ..domain.enums import TraceMode, Chain, EntityCategory, NodeType, LabelSource
from ..application.trace_service import TraceEngine, trace_funds
from ..intelligence.label_matcher import LabelMatcher
from ..intelligence.exchange_inference import ExchangeInference
from ..intelligence.mixer_bridge_detector import MixerBridgeDetector
from ..intelligence.behavior_classifier import (
    classify_wallets,
    EXCHANGE_DEPOSIT,
)
from ..analysis.pattern_detector import PatternDetector
from ..analysis.path_analyzer import PathAnalyzer
from ..analysis.scoring import ScoringEngine
from ..analysis.case_summary import build_case_summary
from ..analysis.taint import propagate_taint
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
        incident_time: Optional[datetime] = None,
        reported_amount: Optional[float] = None,
        mode: TraceMode = TraceMode.FORENSIC,
    ) -> InvestigationResult:
        result = await self.trace_engine.trace(
            seed_address,
            chain,
            max_depth,
            max_branches,
            token_filter,
            incident_time=incident_time,
            reported_amount=reported_amount,
            mode=mode,
        )

        await self._enrich_with_intelligence(result)
        self._detect_patterns(result)
        # Follow the victim's money before ranking anything: taint gives every
        # wallet a defensible "how much of the reported funds reached here".
        seed_key = (
            result.seed_address.address.lower()
            if result.seed_address.chain.is_evm
            else result.seed_address.address
        )
        # Anchor taint on the reported stolen amount when the victim gave one,
        # so unrelated activity by the same wallet does not dilute the fractions.
        propagate_taint(result.graph, seed_key, origin_amount=reported_amount)
        # Behaviour classification needs taint, so it runs after propagation and
        # before path analysis (it can promote wallets to candidate endpoints).
        self._classify_behaviour(result)
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

    def _classify_behaviour(self, result: InvestigationResult) -> None:
        """Label-independent typing of every wallet (Tier-3/4, UNVERIFIED).

        Only `exchange_deposit` promotes an address to an entity claim, and only
        as a sweep-inference label at capped confidence - everything else is
        recorded as behavioural context, never as ownership.
        """
        profiles = classify_wallets(result.graph, result.pattern_detections)
        result.wallet_profiles = {
            a: {
                "behavior": p.behavior,
                "confidence": p.confidence,
                "signals": p.signals,
                "metrics": p.metrics,
            }
            for a, p in profiles.items()
        }

        for addr, p in profiles.items():
            node = result.graph.nodes.get(addr)
            if node is None:
                continue
            node.behavior = p.behavior
            node.behavior_confidence = p.confidence
            node.behavior_signals = list(p.signals)

            # A behavioural verdict is Tier-3 evidence. It must NOT become an
            # entity_type/node_type of EXCHANGE: that is a hard attribution
            # claim, and asserting it from shape alone is exactly the false
            # positive the guardrails exist to prevent (a mule sweeping into a
            # layering wallet looks identical to a deposit wallet sweeping into
            # an exchange hot wallet). Surface it as a lead instead, and let the
            # behaviour fields carry the UNVERIFIED wording.
            if (
                p.behavior == EXCHANGE_DEPOSIT
                and node.address.entity_type == EntityCategory.UNKNOWN
            ):
                node.address.source = LabelSource.SWEEP_INFERENCE
                node.is_endpoint = True

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

        # Marking a node an endpoint must not erase what it IS. This used to
        # reset everything except exchanges to UNKNOWN, which silently threw
        # away sanctioned and mixer classifications - the highest-severity
        # signals the tool produces - and rendered them as anonymous wallets.
        _BY_ENTITY = {
            EntityCategory.EXCHANGE: NodeType.EXCHANGE,
            EntityCategory.SANCTIONED: NodeType.SANCTIONED_ADDRESS,
            EntityCategory.MIXER: NodeType.MIXER,
            EntityCategory.BRIDGE: NodeType.BRIDGE,
        }
        for node in result.graph.nodes.values():
            if node.is_endpoint:
                node.node_type = _BY_ENTITY.get(
                    node.address.entity_type, NodeType.UNKNOWN
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
            endpoint.flow_confidence = score_result["flow_confidence"]
            endpoint.entity_confidence = score_result["entity_confidence"]
            _n = result.graph.nodes.get(endpoint.address.address)
            if _n is not None:
                endpoint.behavior = _n.behavior
                endpoint.behavior_confidence = _n.behavior_confidence
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
