import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock

from src.analysis.pattern_detector import PatternDetector
from src.analysis.scoring import ScoringEngine
from src.domain.models.graph import TransactionGraph, GraphNode, GraphEdge
from src.domain.models.transfer import Transfer
from src.domain.models.address import Address
from src.domain.models.investigation import CandidateEndpoint
from src.domain.enums import (
    Chain,
    EntityCategory,
    NodeType,
    TransferDirection,
    EdgeType,
    PatternType,
)


class TestPatternDetector:
    @pytest.fixture
    def detector(self):
        return PatternDetector()

    @pytest.fixture
    def sample_graph(self):
        graph = TransactionGraph()
        now = datetime.now()

        # Seed node
        seed_addr = Address(
            address="0xseed",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.VICTIM_SEED,
        )
        seed_node = GraphNode(
            address=seed_addr, node_type=NodeType.SEED, depth=0, is_seed=True
        )
        graph.add_node(seed_node)

        # Intermediate nodes
        for i in range(5):
            addr = Address(address=f"0xinter{i}", chain=Chain.ETHEREUM)
            node = GraphNode(
                address=addr, node_type=NodeType.INTERMEDIATE_WALLET, depth=1
            )
            graph.add_node(node)

        # Transfers for fan-out from seed
        for i in range(5):
            transfer = Transfer(
                transaction_hash=f"0xfanout{i}",
                chain=Chain.ETHEREUM,
                from_address="0xseed",
                to_address=f"0xinter{i}",
                token_symbol="ETH",
                amount="1.0",
                timestamp=now,
                block_number=1000,
                direction=TransferDirection.OUTGOING,
            )
            edge = GraphEdge(transfer=transfer)
            graph.add_edge(edge)

        return graph

    def test_detect_fan_out(self, detector, sample_graph):
        detections = detector.detect_fan_out(sample_graph)
        assert len(detections) == 1
        assert detections[0]["address"] == "0xseed"
        assert detections[0]["unique_recipients"] == 5
        assert detections[0]["pattern"] == PatternType.FAN_OUT.value

    def test_detect_hop_velocity(self, detector):
        graph = TransactionGraph()
        now = datetime.now()

        addr = Address(address="0xfast", chain=Chain.ETHEREUM)
        node = GraphNode(address=addr, node_type=NodeType.INTERMEDIATE_WALLET, depth=1)
        graph.add_node(node)

        # 0xfast receives 10, then forwards ~10 a few seconds later
        graph.add_edge(
            GraphEdge(
                transfer=Transfer(
                    transaction_hash="0xin",
                    chain=Chain.ETHEREUM,
                    from_address="0xsource",
                    to_address="0xfast",
                    token_symbol="ETH",
                    amount="10.0",
                    timestamp=now,
                    block_number=1000,
                    direction=TransferDirection.INCOMING,
                )
            )
        )
        graph.add_edge(
            GraphEdge(
                transfer=Transfer(
                    transaction_hash="0xout",
                    chain=Chain.ETHEREUM,
                    from_address="0xfast",
                    to_address="0xdest",
                    token_symbol="ETH",
                    amount="9.8",
                    timestamp=now + timedelta(seconds=15),
                    block_number=1001,
                    direction=TransferDirection.OUTGOING,
                )
            )
        )

        detections = detector.detect_hop_velocity(graph)
        assert len(detections) == 1
        assert detections[0]["address"] == "0xfast"
        assert detections[0]["received_to_forwarded_seconds"] <= 20
        assert detections[0]["forwarded_amount"] == 9.8

    def test_detect_micro_fan_out(self, detector):
        graph = TransactionGraph()
        now = datetime.now()

        addr = Address(address="0xmicro", chain=Chain.ETHEREUM)
        node = GraphNode(address=addr, node_type=NodeType.INTERMEDIATE_WALLET, depth=1)
        graph.add_node(node)

        # Many small transfers
        for i in range(15):
            transfer = Transfer(
                transaction_hash=f"0xmicro{i}",
                chain=Chain.ETHEREUM,
                from_address="0xmicro",
                to_address=f"0xdest{i}",
                token_symbol="USDT",
                amount="10.0",
                timestamp=now,
                block_number=1000,
                direction=TransferDirection.OUTGOING,
            )
            edge = GraphEdge(transfer=transfer)
            graph.add_edge(edge)

        detections = detector.detect_micro_fan_out(graph)
        assert len(detections) == 1
        assert detections[0]["address"] == "0xmicro"
        assert detections[0]["transfer_count"] == 15
        assert detections[0]["avg_amount"] == 10.0

    def test_detect_peel_behavior(self, detector):
        graph = TransactionGraph()
        now = datetime.now()

        # Setup: seed -> A (large) -> B (most of it) -> C (most of it)
        for addr_str, depth in [("0xseed", 0), ("0xA", 1), ("0xB", 2), ("0xC", 3)]:
            addr = Address(address=addr_str, chain=Chain.ETHEREUM)
            node = GraphNode(
                address=addr, node_type=NodeType.INTERMEDIATE_WALLET, depth=depth
            )
            if depth == 0:
                node.node_type = NodeType.SEED
                node.is_seed = True
            graph.add_node(node)

        # seed -> A: 100 ETH
        edge1 = GraphEdge(
            transfer=Transfer(
                transaction_hash="0x1",
                chain=Chain.ETHEREUM,
                from_address="0xseed",
                to_address="0xA",
                token_symbol="ETH",
                amount="100.0",
                timestamp=now,
                block_number=1000,
                direction=TransferDirection.OUTGOING,
            )
        )
        graph.add_edge(edge1)

        # A -> B: 95 ETH (peel 5)
        edge2 = GraphEdge(
            transfer=Transfer(
                transaction_hash="0x2",
                chain=Chain.ETHEREUM,
                from_address="0xA",
                to_address="0xB",
                token_symbol="ETH",
                amount="95.0",
                timestamp=now + timedelta(minutes=1),
                block_number=1001,
                direction=TransferDirection.OUTGOING,
            )
        )
        graph.add_edge(edge2)

        # B -> C: 90 ETH (peel 5)
        edge3 = GraphEdge(
            transfer=Transfer(
                transaction_hash="0x3",
                chain=Chain.ETHEREUM,
                from_address="0xB",
                to_address="0xC",
                token_symbol="ETH",
                amount="90.0",
                timestamp=now + timedelta(minutes=2),
                block_number=1002,
                direction=TransferDirection.OUTGOING,
            )
        )
        graph.add_edge(edge3)

        detections = detector.detect_peel_behavior(graph)
        assert len(detections) >= 1
        assert any(d["address"] == "0xa" for d in detections)
        assert any(d["address"] == "0xb" for d in detections)


class TestScoringEngine:
    @pytest.fixture
    def scoring(self):
        return ScoringEngine()

    @pytest.fixture
    def sample_endpoint(self):
        addr = Address(
            address="0xexchange",
            chain=Chain.ETHEREUM,
            label="Binance",
            entity_type=EntityCategory.EXCHANGE,
            confidence=0.85,
        )
        return CandidateEndpoint(
            address=addr,
            total_amount_received="95.0",
            hop_count=3,
            unlabeled_hop_count=1,
            amount_concentration=0.95,
            path_directness=0.5,
            label_confidence=0.85,
        )

    @pytest.fixture
    def sample_graph(self):
        graph = TransactionGraph()
        # Add seed
        seed = Address(
            address="0xseed",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.VICTIM_SEED,
        )
        seed_node = GraphNode(
            address=seed, node_type=NodeType.SEED, depth=0, is_seed=True
        )
        graph.add_node(seed_node)
        # Add endpoint
        ep_addr = Address(
            address="0xexchange",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.EXCHANGE,
            confidence=0.85,
        )
        ep_node = GraphNode(
            address=ep_addr, node_type=NodeType.EXCHANGE, depth=3, is_endpoint=True
        )
        graph.add_node(ep_node)
        return graph

    def test_calculate_exchange_endpoint(self, scoring, sample_endpoint, sample_graph):
        seed = Address(address="0xseed", chain=Chain.ETHEREUM)
        result = scoring.calculate(sample_endpoint, sample_graph, seed)

        assert "score" in result
        assert "components" in result
        assert "reasons" in result
        assert result["components"]["path_directness"] == 0.5
        assert result["components"]["amount_concentration"] == 0.95
        assert result["components"]["label_confidence"] == 0.85
        assert 0 <= result["score"] <= 1.0

    def test_calculate_sanctioned_endpoint(self, scoring, sample_graph):
        addr = Address(
            address="0xsanctioned",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.SANCTIONED,
            confidence=1.0,
        )
        endpoint = CandidateEndpoint(
            address=addr,
            total_amount_received="50.0",
            hop_count=2,
            unlabeled_hop_count=0,
            amount_concentration=1.0,
            path_directness=1.0,
            label_confidence=1.0,
        )
        seed = Address(address="0xseed", chain=Chain.ETHEREUM)
        result = scoring.calculate(endpoint, sample_graph, seed)

        assert result["components"]["label_confidence"] == 1.0
        assert result["score"] > 0.8

    def test_mixer_cap(self, scoring, sample_graph):
        addr = Address(
            address="0xmixer",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.MIXER,
            confidence=0.1,
        )
        endpoint = CandidateEndpoint(
            address=addr,
            total_amount_received="50.0",
            hop_count=2,
            unlabeled_hop_count=0,
            amount_concentration=1.0,
            path_directness=1.0,
            label_confidence=0.1,
            obfuscation_points=1,
        )
        seed = Address(address="0xseed", chain=Chain.ETHEREUM)
        result = scoring.calculate(endpoint, sample_graph, seed)

        assert result["score"] <= 0.40  # mixer cap
