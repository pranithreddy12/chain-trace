import pytest
from datetime import datetime
from decimal import Decimal

from src.domain.models.address import Address
from src.domain.models.transfer import Transfer
from src.domain.models.transaction import Transaction
from src.domain.models.investigation import (
    Investigation,
    CandidateEndpoint,
    InvestigationResult,
)
from src.domain.models.graph import TransactionGraph, GraphNode, GraphEdge
from src.domain.enums import (
    Chain,
    EntityCategory,
    NodeType,
    TransferDirection,
    EdgeType,
    InvestigationStatus,
    LabelSource,
    PatternType,
)


class TestAddress:
    def test_address_creation(self):
        addr = Address(
            address="0x1234567890123456789012345678901234567890",
            chain=Chain.ETHEREUM,
            label="Test Exchange",
            entity_type=EntityCategory.EXCHANGE,
            confidence=0.85,
        )
        assert addr.address == "0x1234567890123456789012345678901234567890"
        assert addr.chain == Chain.ETHEREUM
        assert addr.entity_type == EntityCategory.EXCHANGE

    def test_address_normalization(self):
        addr = Address(
            address="0x1234567890123456789012345678901234567890", chain=Chain.ETHEREUM
        )
        normalized = addr.normalize()
        assert (
            normalized.address == "0x1234567890123456789012345678901234567890".lower()
        )

    def test_address_display_name(self):
        addr = Address(
            address="0x1234567890123456789012345678901234567890",
            chain=Chain.ETHEREUM,
            label="Test Label",
        )
        assert "Test Label" in addr.display_name
        assert "0x1234" in addr.display_name
        assert "7890" in addr.display_name


class TestTransfer:
    def test_transfer_creation(self):
        transfer = Transfer(
            transaction_hash="0xabc123",
            chain=Chain.ETHEREUM,
            from_address="0x1111111111111111111111111111111111111111",
            to_address="0x2222222222222222222222222222222222222222",
            token_symbol="USDT",
            token_contract="0xdac17f958d2ee523a2206206994597c13d831ec7",
            amount="1000.0",
            amount_raw="1000000000",
            decimals=6,
            timestamp=datetime.now(),
            block_number=12345678,
            direction=TransferDirection.OUTGOING,
        )
        assert transfer.amount_float == 1000.0
        assert not transfer.is_native

    def test_native_transfer(self):
        transfer = Transfer(
            transaction_hash="0xabc123",
            chain=Chain.ETHEREUM,
            from_address="0x1111111111111111111111111111111111111111",
            to_address="0x2222222222222222222222222222222222222222",
            token_symbol="ETH",
            amount="1.5",
            amount_raw="1500000000000000000",
            decimals=18,
            timestamp=datetime.now(),
            block_number=12345678,
            direction=TransferDirection.OUTGOING,
        )
        assert transfer.is_native

    def test_normalized_addresses(self):
        transfer = Transfer(
            transaction_hash="0xabc123",
            chain=Chain.ETHEREUM,
            from_address="0x1111111111111111111111111111111111111111",
            to_address="0x2222222222222222222222222222222222222222",
            token_symbol="ETH",
            amount="1.5",
            timestamp=datetime.now(),
            block_number=12345678,
            direction=TransferDirection.OUTGOING,
        )
        assert (
            transfer.normalized_from()
            == "0x1111111111111111111111111111111111111111".lower()
        )
        assert (
            transfer.normalized_to()
            == "0x2222222222222222222222222222222222222222".lower()
        )


class TestTransaction:
    def test_transaction_creation(self):
        tx = Transaction(
            tx_hash="0xabc123",
            chain=Chain.ETHEREUM,
            block_number=12345678,
            timestamp=datetime.now(),
            from_address="0x1111111111111111111111111111111111111111",
            to_address="0x2222222222222222222222222222222222222222",
            value="1000000000000000000",
            status=1,
        )
        assert tx.is_success

    def test_failed_transaction(self):
        tx = Transaction(
            tx_hash="0xabc123",
            chain=Chain.ETHEREUM,
            block_number=12345678,
            timestamp=datetime.now(),
            from_address="0x1111111111111111111111111111111111111111",
            status=0,
        )
        assert not tx.is_success


class TestInvestigation:
    def test_investigation_creation(self):
        inv = Investigation(
            seed_address="0x1234567890123456789012345678901234567890",
            chain=Chain.ETHEREUM,
            max_depth=4,
            max_branches=25,
        )
        assert inv.investigation_id is not None
        assert inv.status == InvestigationStatus.PENDING

    def test_investigation_lifecycle(self):
        inv = Investigation(
            seed_address="0x1234567890123456789012345678901234567890",
            chain=Chain.ETHEREUM,
        )
        inv.mark_running()
        assert inv.status == InvestigationStatus.RUNNING

        graph = TransactionGraph()
        inv.mark_completed(graph)
        assert inv.status == InvestigationStatus.COMPLETED
        assert inv.completed_at is not None


class TestCandidateEndpoint:
    def test_confidence_percentage(self):
        ep = CandidateEndpoint(
            address=Address(address="0x1234", chain=Chain.ETHEREUM),
            confidence_score=0.82,
        )
        assert ep.confidence_percentage == "82.0%"


class TestGraph:
    def test_graph_node_creation(self):
        addr = Address(address="0x1234", chain=Chain.ETHEREUM)
        node = GraphNode(address=addr, node_type=NodeType.SEED, depth=0, is_seed=True)
        assert node.is_seed
        assert node.depth == 0

    def test_graph_edge_creation(self):
        transfer = Transfer(
            transaction_hash="0xabc123",
            chain=Chain.ETHEREUM,
            from_address="0x1111",
            to_address="0x2222",
            token_symbol="ETH",
            amount="1.0",
            timestamp=datetime.now(),
            block_number=12345678,
            direction=TransferDirection.OUTGOING,
        )
        edge = GraphEdge(transfer=transfer)
        assert edge.transfer.amount_float == 1.0

    def test_transaction_graph(self):
        graph = TransactionGraph(seed_address="0x1234")
        addr1 = Address(address="0x1234", chain=Chain.ETHEREUM)
        addr2 = Address(address="0x5678", chain=Chain.ETHEREUM)

        node1 = GraphNode(address=addr1, node_type=NodeType.SEED, depth=0, is_seed=True)
        node2 = GraphNode(
            address=addr2, node_type=NodeType.INTERMEDIATE_WALLET, depth=1
        )

        graph.add_node(node1)
        graph.add_node(node2)

        transfer = Transfer(
            transaction_hash="0xabc123",
            chain=Chain.ETHEREUM,
            from_address="0x1234",
            to_address="0x5678",
            token_symbol="ETH",
            amount="1.0",
            timestamp=datetime.now(),
            block_number=12345678,
            direction=TransferDirection.OUTGOING,
        )
        edge = GraphEdge(transfer=transfer)
        graph.add_edge(edge)

        assert graph.node_count == 2
        assert graph.edge_count == 1
