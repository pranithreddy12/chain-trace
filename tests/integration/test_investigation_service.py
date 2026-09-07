import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from src.application.investigation_service import InvestigationService
from src.domain.models.investigation import (
    Investigation,
    InvestigationResult,
    CandidateEndpoint,
)
from src.domain.models.graph import TransactionGraph, GraphNode, GraphEdge
from src.domain.models.address import Address
from src.domain.models.transfer import Transfer
from src.domain.enums import (
    Chain,
    EntityCategory,
    NodeType,
    TransferDirection,
    EdgeType,
)


class TestInvestigationService:
    @pytest.fixture
    def service(self):
        return InvestigationService()

    @pytest.fixture
    def mock_trace_result(self):
        graph = TransactionGraph(seed_address="0xseed")

        seed = Address(
            address="0xseed",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.VICTIM_SEED,
        )
        seed_node = GraphNode(
            address=seed, node_type=NodeType.SEED, depth=0, is_seed=True
        )
        graph.add_node(seed_node)

        addr1 = Address(address="0xaddr1", chain=Chain.ETHEREUM)
        node1 = GraphNode(
            address=addr1, node_type=NodeType.INTERMEDIATE_WALLET, depth=1
        )
        graph.add_node(node1)

        addr2 = Address(
            address="0xexchange",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.EXCHANGE,
            confidence=0.85,
            label="Binance",
        )
        node2 = GraphNode(
            address=addr2, node_type=NodeType.EXCHANGE, depth=2, is_endpoint=True
        )
        graph.add_node(node2)

        # seed -> addr1 -> exchange
        transfer1 = Transfer(
            transaction_hash="0x1",
            chain=Chain.ETHEREUM,
            from_address="0xseed",
            to_address="0xaddr1",
            token_symbol="USDT",
            amount="1000.0",
            timestamp=datetime.now(),
            block_number=1000,
            direction=TransferDirection.OUTGOING,
        )
        transfer2 = Transfer(
            transaction_hash="0x2",
            chain=Chain.ETHEREUM,
            from_address="0xaddr1",
            to_address="0xexchange",
            token_symbol="USDT",
            amount="990.0",
            timestamp=datetime.now(),
            block_number=1001,
            direction=TransferDirection.OUTGOING,
        )
        graph.add_edge(GraphEdge(transfer=transfer1))
        graph.add_edge(GraphEdge(transfer=transfer2))

        inv = Investigation(
            seed_address="0xseed", chain=Chain.ETHEREUM, max_depth=4, max_branches=25
        )
        inv.investigation_id = "test-id"
        inv.transactions_examined = 2
        inv.nodes_found = 3
        inv.edges_found = 2

        return InvestigationResult(investigation=inv, graph=graph, seed_address=seed)

    @pytest.mark.asyncio
    async def test_run_investigation_enriches_with_intelligence(
        self, service, mock_trace_result
    ):
        with patch.object(
            service.trace_engine, "trace", new=AsyncMock(return_value=mock_trace_result)
        ):
            result = await service.run_investigation(
                seed_address="0xseed", chain=Chain.ETHEREUM
            )

            assert isinstance(result, InvestigationResult)
            assert result.graph.node_count == 3
            assert len(result.candidate_endpoints) >= 1

    @pytest.mark.asyncio
    async def test_exchange_inference_marks_endpoint(self, service):
        graph = TransactionGraph(seed_address="0xseed")

        seed = Address(
            address="0xseed",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.VICTIM_SEED,
        )
        seed_node = GraphNode(
            address=seed, node_type=NodeType.SEED, depth=0, is_seed=True
        )
        graph.add_node(seed_node)

        # Many addresses sending to one (convergence pattern)
        for i in range(10):
            addr = Address(address=f"0xdeposit{i}", chain=Chain.ETHEREUM)
            node = GraphNode(
                address=addr, node_type=NodeType.INTERMEDIATE_WALLET, depth=1
            )
            graph.add_node(node)

            transfer = Transfer(
                transaction_hash=f"0x{i}",
                chain=Chain.ETHEREUM,
                from_address=f"0xdeposit{i}",
                to_address="0xhotwallet",
                token_symbol="USDT",
                amount="100.0",
                timestamp=datetime.now(),
                block_number=1000,
                direction=TransferDirection.OUTGOING,
            )
            graph.add_edge(GraphEdge(transfer=transfer))

        # Hot wallet (known exchange)
        hot = Address(
            address="0xhotwallet",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.EXCHANGE,
            confidence=0.9,
        )
        hot_node = GraphNode(
            address=hot, node_type=NodeType.EXCHANGE, depth=2, is_endpoint=True
        )
        graph.add_node(hot_node)

        # Now test inference
        inferred = service.exchange_inference.infer(graph)
        # Should infer deposit addresses as exchange
        assert len(inferred) > 0


class TestReportService:
    def test_generate_summary(self):
        from src.application.report_service import ReportService

        graph = TransactionGraph(seed_address="0xseed")
        seed = Address(
            address="0xseed",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.VICTIM_SEED,
        )
        seed_node = GraphNode(
            address=seed, node_type=NodeType.SEED, depth=0, is_seed=True
        )
        graph.add_node(seed_node)

        inv = Investigation(
            seed_address="0xseed", chain=Chain.ETHEREUM, max_depth=4, max_branches=25
        )
        inv.investigation_id = "test-123"
        inv.transactions_examined = 10
        inv.nodes_found = 5
        inv.edges_found = 4

        result = InvestigationResult(
            investigation=inv,
            graph=graph,
            seed_address=seed,
            candidate_endpoints=[],
            pattern_detections={},
        )

        summary = ReportService.generate_summary(result)
        assert summary["investigation_id"] == "test-123"
        assert summary["seed_address"]["address"] == "0xseed"
        assert summary["statistics"]["transactions_examined"] == 10

    def test_generate_detailed_report(self):
        from src.application.report_service import ReportService

        graph = TransactionGraph(seed_address="0xseed")
        seed = Address(
            address="0xseed",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.VICTIM_SEED,
        )
        seed_node = GraphNode(
            address=seed, node_type=NodeType.SEED, depth=0, is_seed=True
        )
        graph.add_node(seed_node)

        inv = Investigation(
            seed_address="0xseed", chain=Chain.ETHEREUM, max_depth=4, max_branches=25
        )
        inv.investigation_id = "test-123"
        inv.transactions_examined = 10
        inv.nodes_found = 5
        inv.edges_found = 4

        result = InvestigationResult(
            investigation=inv,
            graph=graph,
            seed_address=seed,
            candidate_endpoints=[],
            pattern_detections={},
        )

        report = ReportService.generate_detailed_report(result)
        assert "CHAINTRACE INVESTIGATIVE SUMMARY" in report
        assert "test-123" in report
        assert "0xseed" in report

    def test_export_json(self):
        from src.application.report_service import ReportService

        graph = TransactionGraph(seed_address="0xseed")
        seed = Address(
            address="0xseed",
            chain=Chain.ETHEREUM,
            entity_type=EntityCategory.VICTIM_SEED,
        )
        seed_node = GraphNode(
            address=seed, node_type=NodeType.SEED, depth=0, is_seed=True
        )
        graph.add_node(seed_node)

        inv = Investigation(
            seed_address="0xseed", chain=Chain.ETHEREUM, max_depth=4, max_branches=25
        )
        inv.investigation_id = "test-123"
        inv.transactions_examined = 10
        inv.nodes_found = 5
        inv.edges_found = 4

        result = InvestigationResult(
            investigation=inv,
            graph=graph,
            seed_address=seed,
            candidate_endpoints=[],
            pattern_detections={},
        )

        json_output = ReportService.export_json(result)
        assert "test-123" in json_output
        assert "0xseed" in json_output
