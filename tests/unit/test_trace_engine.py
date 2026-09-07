import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
from collections import deque

from src.application.trace_service import TraceEngine, TraceContext
from src.domain.models.transfer import Transfer
from src.domain.models.address import Address
from src.domain.models.investigation import Investigation
from src.domain.models.graph import TransactionGraph, GraphNode, GraphEdge
from src.domain.enums import (
    Chain,
    EntityCategory,
    NodeType,
    TransferDirection,
    EdgeType,
    InvestigationStatus,
)
from src.blockchain.base import ProviderError, ProviderErrorType


class TestTraceEngine:
    @pytest.fixture
    def engine(self):
        return TraceEngine()

    @pytest.fixture
    def mock_provider(self):
        provider = Mock()
        provider.chain = Chain.ETHEREUM
        provider.get_all_outgoing_transfers = AsyncMock(return_value=[])
        provider.close = AsyncMock()
        return provider

    @pytest.mark.asyncio
    async def test_trace_creates_investigation(self, engine):
        with patch.object(
            engine,
            "_get_provider",
            return_value=Mock(
                chain=Chain.ETHEREUM,
                get_all_outgoing_transfers=AsyncMock(return_value=[]),
                close=AsyncMock(),
            ),
        ):
            result = await engine.trace(
                seed_address="0xseed",
                chain=Chain.ETHEREUM,
                max_depth=2,
                max_branches=10,
            )

            assert isinstance(result.investigation, Investigation)
            assert result.investigation.seed_address == "0xseed"
            assert result.investigation.chain == Chain.ETHEREUM
            assert result.investigation.status == InvestigationStatus.COMPLETED
            assert result.graph.node_count >= 1  # at least seed

    @pytest.mark.asyncio
    async def test_trace_with_transfers(self, engine):
        transfers = [
            Transfer(
                transaction_hash="0x1",
                chain=Chain.ETHEREUM,
                from_address="0xseed",
                to_address="0xaddr1",
                token_symbol="ETH",
                amount="1.0",
                timestamp=datetime.now(),
                block_number=1000,
                direction=TransferDirection.OUTGOING,
            ),
            Transfer(
                transaction_hash="0x2",
                chain=Chain.ETHEREUM,
                from_address="0xaddr1",
                to_address="0xaddr2",
                token_symbol="ETH",
                amount="0.9",
                timestamp=datetime.now(),
                block_number=1001,
                direction=TransferDirection.OUTGOING,
            ),
        ]

        mock_provider = Mock()
        mock_provider.chain = Chain.ETHEREUM
        mock_provider.get_all_outgoing_transfers = AsyncMock(
            side_effect=[transfers[:1], transfers[1:], []]
        )
        mock_provider.close = AsyncMock()

        with patch.object(engine, "_get_provider", return_value=mock_provider):
            result = await engine.trace(
                seed_address="0xseed",
                chain=Chain.ETHEREUM,
                max_depth=3,
                max_branches=10,
            )

            assert result.graph.node_count >= 2
            assert result.graph.edge_count >= 1

    @pytest.mark.asyncio
    async def test_trace_respects_max_depth(self, engine):
        # Create many levels of transfers
        transfers_by_level = {}
        for level in range(5):
            transfers_by_level[level] = [
                Transfer(
                    transaction_hash=f"0x{level}_{i}",
                    chain=Chain.ETHEREUM,
                    from_address=f"0xaddr{level - 1}" if level > 0 else "0xseed",
                    to_address=f"0xaddr{level}",
                    token_symbol="ETH",
                    amount="1.0",
                    timestamp=datetime.now(),
                    block_number=1000 + level,
                    direction=TransferDirection.OUTGOING,
                )
                for i in range(2)
            ]

        call_count = [0]

        async def mock_get_transfers(address, *args, **kwargs):
            level = 0
            for l, t in transfers_by_level.items():
                if any(x.from_address == address for x in t):
                    level = l
                    break
            call_count[0] += 1
            return transfers_by_level.get(level, [])

        mock_provider = Mock()
        mock_provider.chain = Chain.ETHEREUM
        mock_provider.get_all_outgoing_transfers = mock_get_transfers
        mock_provider.close = AsyncMock()

        with patch.object(engine, "_get_provider", return_value=mock_provider):
            result = await engine.trace(
                seed_address="0xseed",
                chain=Chain.ETHEREUM,
                max_depth=2,
                max_branches=10,
            )

            # Should not go beyond depth 2
            max_depth_found = max(n.depth for n in result.graph.nodes.values())
            assert max_depth_found <= 2

    @pytest.mark.asyncio
    async def test_trace_respects_branch_limit(self, engine):
        # Create many outgoing transfers from seed
        transfers = [
            Transfer(
                transaction_hash=f"0x{i}",
                chain=Chain.ETHEREUM,
                from_address="0xseed",
                to_address=f"0xdest{i}",
                token_symbol="ETH",
                amount="1.0",
                timestamp=datetime.now(),
                block_number=1000,
                direction=TransferDirection.OUTGOING,
            )
            for i in range(30)
        ]

        mock_provider = Mock()
        mock_provider.chain = Chain.ETHEREUM
        mock_provider.get_all_outgoing_transfers = AsyncMock(return_value=transfers)
        mock_provider.close = AsyncMock()

        with patch.object(engine, "_get_provider", return_value=mock_provider):
            result = await engine.trace(
                seed_address="0xseed", chain=Chain.ETHEREUM, max_depth=2, max_branches=5
            )

            # Should only process 5 branches
            seed_edges = result.graph.get_outgoing_edges("0xseed")
            assert len(seed_edges) <= 5


class TestTraceContext:
    def test_context_initialization(self):
        investigation = Investigation(
            seed_address="0xseed", chain=Chain.ETHEREUM, max_depth=4, max_branches=25
        )
        graph = TransactionGraph(seed_address="0xseed")

        ctx = TraceContext(investigation=investigation, graph=graph)

        assert ctx.investigation == investigation
        assert ctx.graph == graph
        assert isinstance(ctx.visited, set)
        assert isinstance(ctx.queue, deque)
