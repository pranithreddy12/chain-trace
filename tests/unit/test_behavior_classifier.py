from datetime import datetime, timedelta

from src.intelligence.behavior_classifier import (
    classify_wallets,
    EXCHANGE_DEPOSIT,
    COLLECTOR,
    DISTRIBUTOR,
    HOLDING,
)
from src.analysis.taint import propagate_taint
from src.domain.models.graph import TransactionGraph, GraphNode, GraphEdge
from src.domain.models.address import Address
from src.domain.models.transfer import Transfer
from src.domain.enums import Chain, NodeType, TransferDirection

NOW = datetime(2026, 1, 1, 12, 0, 0)


def _node(g, addr, depth, seed=False):
    g.add_node(
        GraphNode(
            address=Address(address=addr, chain=Chain.ETHEREUM),
            node_type=NodeType.SEED if seed else NodeType.UNKNOWN,
            depth=depth,
            is_seed=seed,
        )
    )


def _edge(g, frm, to, amount, minutes=0):
    g.add_edge(
        GraphEdge(
            transfer=Transfer(
                transaction_hash=f"0x{frm}{to}{amount}{minutes}",
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


class TestExchangeDeposit:
    def test_shared_sweep_destination_is_detected(self):
        """4 deposit wallets each sweeping everything to one hot wallet."""
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "hot", 2)
        for i in range(4):
            d = f"dep{i}"
            _node(g, d, 1)
            _edge(g, "seed", d, 100, 0)
            _edge(g, d, "hot", 100, 1)  # sweeps 100% onward
        propagate_taint(g, "seed")
        profiles = classify_wallets(g, {})

        for i in range(4):
            p = profiles[f"dep{i}"]
            assert p.behavior == EXCHANGE_DEPOSIT
            assert p.confidence <= 0.60, "must stay Tier-3, never a hard label"
            assert len(p.signals) >= 2, "needs corroborating signals"

    def test_single_sweeper_is_not_an_exchange_deposit(self):
        """One wallet forwarding onward is far too common to assert on."""
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "a", 1)
        _node(g, "b", 2)
        _edge(g, "seed", "a", 100)
        _edge(g, "a", "b", 100, 1)
        propagate_taint(g, "seed")
        profiles = classify_wallets(g, {})
        assert profiles.get("a") is None or profiles["a"].behavior != EXCHANGE_DEPOSIT


class TestOtherBehaviours:
    def test_collector_needs_many_senders(self):
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "pot", 2)
        for i in range(5):
            m = f"m{i}"
            _node(g, m, 1)
            _edge(g, "seed", m, 100)
            _edge(g, m, "pot", 100, 1)
        propagate_taint(g, "seed")
        p = classify_wallets(g, {})["pot"]
        # terminal + 5 senders + holds everything
        assert p.behavior in (COLLECTOR, HOLDING)
        assert p.metrics["distinct_senders"] == 5

    def test_distributor_needs_two_signals(self):
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "hub", 1)
        _edge(g, "seed", "hub", 1000)
        for i in range(10):
            _node(g, f"x{i}", 2)
            _edge(g, "hub", f"x{i}", 100, 1)
        propagate_taint(g, "seed")
        p = classify_wallets(g, {"fan_out": [{"address": "hub"}]})["hub"]
        assert p.behavior == DISTRIBUTOR
        assert p.metrics["distinct_recipients"] == 10

    def test_holding_wallet_flagged_when_material(self):
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "end", 1)
        _edge(g, "seed", "end", 100)
        propagate_taint(g, "seed")
        p = classify_wallets(g, {})["end"]
        assert p.behavior == HOLDING
        assert p.metrics["taint_fraction"] == 1.0

    def test_seed_is_never_classified(self):
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "a", 1)
        _edge(g, "seed", "a", 100)
        propagate_taint(g, "seed")
        assert "seed" not in classify_wallets(g, {})
