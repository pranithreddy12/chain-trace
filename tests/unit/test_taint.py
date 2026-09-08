from datetime import datetime, timedelta

import pytest

from src.analysis.taint import propagate_taint, dominant_path
from src.domain.models.graph import TransactionGraph, GraphNode, GraphEdge
from src.domain.models.address import Address
from src.domain.models.transfer import Transfer
from src.domain.enums import Chain, EntityCategory, NodeType, TransferDirection


NOW = datetime(2026, 1, 1, 12, 0, 0)


def _node(graph, addr, depth, seed=False):
    graph.add_node(
        GraphNode(
            address=Address(address=addr, chain=Chain.ETHEREUM),
            node_type=NodeType.SEED if seed else NodeType.UNKNOWN,
            depth=depth,
            is_seed=seed,
        )
    )


def _edge(graph, frm, to, amount, minutes=0):
    graph.add_edge(
        GraphEdge(
            transfer=Transfer(
                transaction_hash=f"0x{frm}{to}{amount}",
                chain=Chain.ETHEREUM,
                from_address=frm,
                to_address=to,
                token_symbol="USDT",
                amount=str(amount),
                timestamp=NOW + timedelta(minutes=minutes),
                block_number=1000 + minutes,
                direction=TransferDirection.OUTGOING,
            )
        )
    )


def _chain_graph():
    """seed -100-> a -95-> b ; a also peels -5-> c"""
    g = TransactionGraph(seed_address="seed")
    _node(g, "seed", 0, seed=True)
    _node(g, "a", 1)
    _node(g, "b", 2)
    _node(g, "c", 2)
    _edge(g, "seed", "a", 100, 0)
    _edge(g, "a", "b", 95, 1)
    _edge(g, "a", "c", 5, 1)
    return g


class TestPropagateTaint:
    def test_linear_chain_carries_full_taint(self):
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "a", 1)
        _edge(g, "seed", "a", 100)
        propagate_taint(g, "seed")
        assert g.nodes["seed"].taint_fraction == 1.0
        assert g.nodes["a"].tainted_value == pytest.approx(100.0)
        assert g.nodes["a"].taint_fraction == pytest.approx(1.0)

    def test_haircut_splits_proportionally(self):
        g = _chain_graph()
        propagate_taint(g, "seed")
        # a forwarded everything it received, split 95/5
        assert g.nodes["b"].tainted_value == pytest.approx(95.0)
        assert g.nodes["c"].tainted_value == pytest.approx(5.0)
        assert g.nodes["b"].taint_fraction == pytest.approx(0.95)
        assert g.nodes["c"].taint_fraction == pytest.approx(0.05)

    def test_terminal_wallet_retains_taint(self):
        g = _chain_graph()
        propagate_taint(g, "seed")
        # b and c are terminal — their taint stays and is reported
        assert g.nodes["b"].tainted_value > 0
        assert sum(
            n.tainted_value for a, n in g.nodes.items() if a in ("b", "c")
        ) == pytest.approx(100.0)

    def test_cannot_forward_more_taint_than_value_moved(self):
        """a receives 100 but only moves 10 onward -> only 10 taint propagates."""
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "a", 1)
        _node(g, "b", 2)
        _edge(g, "seed", "a", 100)
        _edge(g, "a", "b", 10, 1)
        propagate_taint(g, "seed")
        assert g.nodes["b"].tainted_value == pytest.approx(10.0)
        assert g.nodes["b"].taint_fraction == pytest.approx(0.1)

    def test_edges_carry_taint(self):
        g = _chain_graph()
        propagate_taint(g, "seed")
        by_pair = {
            (e.transfer.normalized_from(), e.transfer.normalized_to()): e.tainted_value
            for e in g.edges
        }
        assert by_pair[("seed", "a")] == pytest.approx(100.0)
        assert by_pair[("a", "b")] == pytest.approx(95.0)
        assert by_pair[("a", "c")] == pytest.approx(5.0)

    def test_explicit_origin_amount_anchors_fractions(self):
        """Victim reports 50 stolen even though the wallet moved 100."""
        g = _chain_graph()
        propagate_taint(g, "seed", origin_amount=50.0)
        assert g.nodes["b"].taint_fraction == pytest.approx(0.95)
        assert g.nodes["b"].tainted_value == pytest.approx(47.5)

    def test_unknown_seed_raises_instead_of_silently_zeroing(self):
        """A seed missing from its own graph is a normalisation bug.

        This used to return {} and leave every wallet at taint 0, producing an
        empty ranking with nothing to explain it.
        """
        import pytest

        g = _chain_graph()
        with pytest.raises(ValueError, match="not a node in its own graph"):
            propagate_taint(g, "nope")


class TestDominantPath:
    def test_follows_the_money_not_the_short_hop(self):
        g = _chain_graph()
        propagate_taint(g, "seed")
        assert dominant_path(g, "seed", "b") == ["seed", "a", "b"]
        assert dominant_path(g, "seed", "c") == ["seed", "a", "c"]

    def test_returns_empty_when_unreachable(self):
        g = _chain_graph()
        _node(g, "orphan", 1)
        propagate_taint(g, "seed")
        assert dominant_path(g, "seed", "orphan") == []
