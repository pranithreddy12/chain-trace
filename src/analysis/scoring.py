from typing import Dict, Any, List
from dataclasses import dataclass

from ..domain.models.investigation import CandidateEndpoint
from ..domain.models.graph import TransactionGraph
from ..domain.models.address import Address
from ..domain.enums import EntityCategory
from ..config.settings import get_settings


@dataclass
class ScoreResult:
    score: float
    components: Dict[str, float]
    reasons: List[str]
    pattern_flags: List[str]
    warnings: List[str]


class ScoringEngine:
    def __init__(self):
        self.settings = get_settings()

    def calculate(
        self,
        endpoint: CandidateEndpoint,
        graph: TransactionGraph,
        seed_address: Address,
    ) -> Dict[str, Any]:
        # Prefer components already computed by the path analyzer; fall back to
        # deriving them here when the endpoint was built without them.
        path_directness = (
            endpoint.path_directness
            if endpoint.path_directness > 0
            else self._calculate_path_directness(endpoint)
        )
        amount_concentration = (
            endpoint.amount_concentration
            if endpoint.amount_concentration > 0
            else self._calculate_amount_concentration(endpoint)
        )
        label_confidence = (
            endpoint.label_confidence
            if endpoint.label_confidence > 0
            else self._calculate_label_confidence(endpoint)
        )

        raw_score = (
            0.30 * path_directness
            + 0.35 * amount_concentration
            + 0.35 * label_confidence
        )

        final_score = min(1.0, max(0.0, raw_score))

        if endpoint.obfuscation_points > 0:
            final_score = min(final_score, self.settings.mixer_score_cap)

        reasons = self._generate_reasons(
            endpoint, path_directness, amount_concentration, label_confidence
        )

        pattern_flags = self._get_pattern_flags(endpoint, graph)

        return {
            "score": final_score,
            "components": {
                "path_directness": path_directness,
                "amount_concentration": amount_concentration,
                "label_confidence": label_confidence,
            },
            "reasons": reasons,
            "pattern_flags": pattern_flags,
        }

    def _calculate_path_directness(self, endpoint: CandidateEndpoint) -> float:
        return 1.0 / (1.0 + endpoint.unlabeled_hop_count)

    def _calculate_amount_concentration(self, endpoint: CandidateEndpoint) -> float:
        return min(1.0, endpoint.amount_concentration)

    def _calculate_label_confidence(self, endpoint: CandidateEndpoint) -> float:
        addr = endpoint.address

        if addr.entity_type == EntityCategory.SANCTIONED:
            return 1.0
        elif addr.entity_type == EntityCategory.EXCHANGE:
            if addr.source and addr.source.value == "verified_exchange":
                return 0.85
            elif addr.source and addr.source.value == "sweep_inference":
                return 0.60
            else:
                return 0.50
        elif addr.entity_type == EntityCategory.MIXER:
            return 0.10
        elif addr.entity_type == EntityCategory.BRIDGE:
            return 0.20
        else:
            return addr.confidence

    def _generate_reasons(
        self,
        endpoint: CandidateEndpoint,
        path_directness: float,
        amount_concentration: float,
        label_confidence: float,
    ) -> List[str]:
        reasons = []

        if endpoint.unlabeled_hop_count == 0:
            reasons.append("Direct path with no unlabeled hops")
        elif endpoint.unlabeled_hop_count == 1:
            reasons.append("Only 1 unlabeled hop")
        else:
            reasons.append(f"{endpoint.unlabeled_hop_count} unlabeled hops")

        reasons.append(
            f"{amount_concentration * 100:.0f}% of traced amount reached endpoint"
        )

        if endpoint.address.entity_type == EntityCategory.EXCHANGE:
            if (
                endpoint.address.source
                and endpoint.address.source.value == "verified_exchange"
            ):
                reasons.append("Endpoint has verified exchange label")
            else:
                reasons.append("Endpoint inferred as exchange deposit")
        elif endpoint.address.entity_type == EntityCategory.SANCTIONED:
            reasons.append("Endpoint matches sanctions list")
        elif endpoint.address.label:
            reasons.append(f"Label: {endpoint.address.label}")

        if endpoint.obfuscation_points > 0:
            reasons.append(
                f"Path crosses {endpoint.obfuscation_points} mixer/bridge (confidence capped)"
            )

        return reasons

    def _get_pattern_flags(
        self, endpoint: CandidateEndpoint, graph: TransactionGraph
    ) -> List[str]:
        flags = []
        endpoint_addr = endpoint.address.address

        for node in graph.nodes.values():
            if node.address.address == endpoint_addr:
                flags.extend(node.pattern_flags)
                break

        return flags
