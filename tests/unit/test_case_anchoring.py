"""Case anchoring: a real report is "X stolen on DATE", not "trace forever"."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.application.trace_service import TraceEngine
from src.analysis.taint import propagate_taint
from src.domain.models.graph import TransactionGraph, GraphNode, GraphEdge
from src.domain.models.address import Address
from src.domain.models.transfer import Transfer
from src.domain.enums import Chain, NodeType, TransferDirection

INCIDENT = datetime(2026, 5, 1, 12, 0, 0)


def _t(frm, to, amount, when):
    return Transfer(
        transaction_hash=f"0x{frm}{to}{amount}{when.timestamp()}",
        chain=Chain.ETHEREUM,
        from_address=frm,
        to_address=to,
        token_symbol="USDT",
        amount=str(amount),
        timestamp=when,
        block_number=1,
        direction=TransferDirection.OUTGOING,
    )


def _provider(by_from):
    p = Mock()
    p.chain = Chain.ETHEREUM

    async def get_all(address, **kw):
        return list(by_from.get(address.lower(), []))

    p.get_all_outgoing_transfers = get_all
    p.close = AsyncMock()
    return p


class TestIncidentTimeFilter:
    @pytest.mark.asyncio
    async def test_transfers_before_the_incident_are_ignored(self):
        by_from = {
            "0xseed": [
                _t("0xseed", "0xold", 500, INCIDENT - timedelta(days=30)),
                _t("0xseed", "0xnew", 500, INCIDENT + timedelta(minutes=5)),
            ]
        }
        engine = TraceEngine()
        with patch.object(engine, "_get_provider", return_value=_provider(by_from)):
            r = await engine.trace(
                "0xseed", Chain.ETHEREUM, max_depth=1, max_branches=10,
                incident_time=INCIDENT,
            )
        assert "0xnew" in r.graph.nodes
        assert "0xold" not in r.graph.nodes, "pre-incident funds are not the case"

    @pytest.mark.asyncio
    async def test_hop_cannot_forward_before_funds_arrived(self):
        """0xa sent money out BEFORE the tainted funds reached it - not our money."""
        arrival = INCIDENT + timedelta(hours=1)
        by_from = {
            "0xseed": [_t("0xseed", "0xa", 500, arrival)],
            "0xa": [
                _t("0xa", "0xbefore", 400, arrival - timedelta(hours=2)),
                _t("0xa", "0xafter", 400, arrival + timedelta(minutes=10)),
            ],
        }
        engine = TraceEngine()
        with patch.object(engine, "_get_provider", return_value=_provider(by_from)):
            r = await engine.trace(
                "0xseed", Chain.ETHEREUM, max_depth=3, max_branches=10,
                incident_time=INCIDENT,
            )
        assert "0xafter" in r.graph.nodes
        assert "0xbefore" not in r.graph.nodes

    @pytest.mark.asyncio
    async def test_no_incident_time_keeps_everything(self):
        by_from = {
            "0xseed": [
                _t("0xseed", "0xold", 500, INCIDENT - timedelta(days=30)),
                _t("0xseed", "0xnew", 500, INCIDENT + timedelta(minutes=5)),
            ]
        }
        engine = TraceEngine()
        with patch.object(engine, "_get_provider", return_value=_provider(by_from)):
            r = await engine.trace(
                "0xseed", Chain.ETHEREUM, max_depth=1, max_branches=10
            )
        assert {"0xold", "0xnew"} <= set(r.graph.nodes)


class TestReportedAmountAnchor:
    def test_reported_amount_anchors_taint_fractions(self):
        """Seed moved 1000 total but only 200 was reported stolen."""
        g = TransactionGraph(seed_address="seed")
        for a, d in (("seed", 0), ("a", 1), ("b", 1)):
            g.add_node(
                GraphNode(
                    address=Address(address=a, chain=Chain.ETHEREUM),
                    node_type=NodeType.SEED if a == "seed" else NodeType.UNKNOWN,
                    depth=d,
                    is_seed=(a == "seed"),
                )
            )
        g.add_edge(GraphEdge(transfer=_t("seed", "a", 800, INCIDENT)))
        g.add_edge(GraphEdge(transfer=_t("seed", "b", 200, INCIDENT)))

        propagate_taint(g, "seed", origin_amount=200.0)
        # fractions are measured against the REPORTED 200, and are capped at 1.0
        assert g.nodes["a"].taint_fraction == pytest.approx(0.8)
        assert g.nodes["b"].taint_fraction == pytest.approx(0.2)
        assert g.nodes["a"].tainted_value == pytest.approx(160.0)
