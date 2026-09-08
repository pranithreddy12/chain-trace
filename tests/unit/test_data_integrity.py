"""Regression tests for the data-integrity audit findings.

Each class pins one bug that corrupted amounts or rankings.
"""

from datetime import datetime, timedelta

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


def _transfer(frm, to, amount, token="USDT", minutes=0, tx=None):
    return Transfer(
        transaction_hash=tx or f"0x{frm}{to}{amount}{token}{minutes}",
        chain=Chain.ETHEREUM,
        from_address=frm,
        to_address=to,
        token_symbol=token,
        amount=str(amount),
        timestamp=NOW + timedelta(minutes=minutes),
        block_number=1 + minutes,
        direction=TransferDirection.OUTGOING,
    )


def _edge(g, frm, to, amount, token="USDT", minutes=0, tx=None):
    g.add_edge(GraphEdge(transfer=_transfer(frm, to, amount, token, minutes, tx)))


class TestPerTokenTaint:
    """Bug 1: units of different tokens were summed as one value."""

    def test_a_big_number_in_another_token_cannot_outrank_the_real_one(self):
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "usdt_leg", 1)
        _node(g, "scam_leg", 1)
        _edge(g, "seed", "usdt_leg", 100, "USDT")
        _edge(g, "seed", "scam_leg", 100_000, "SCAMCOIN", minutes=1)
        propagate_taint(g, "seed")

        # each leg took 100% of its OWN token's traced flow
        assert g.nodes["usdt_leg"].taint_fraction == 1.0
        assert g.nodes["scam_leg"].taint_fraction == 1.0
        assert g.nodes["usdt_leg"].taint_token == "USDT"
        assert g.nodes["scam_leg"].taint_token == "SCAMCOIN"

    def test_tokens_are_tracked_separately(self):
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "a", 1)
        _edge(g, "seed", "a", 100, "USDT")
        _edge(g, "seed", "a", 40, "TRX", minutes=1)
        propagate_taint(g, "seed")

        n = g.nodes["a"]
        assert n.tainted_by_token == {"USDT": 100.0, "TRX": 40.0}
        assert n.taint_by_token == {"USDT": 1.0, "TRX": 1.0}
        # never a cross-token sum
        assert n.tainted_value in (100.0, 40.0)

    def test_split_within_a_token_still_haircuts(self):
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "a", 1)
        _node(g, "b", 1)
        _edge(g, "seed", "a", 750, "USDT")
        _edge(g, "seed", "b", 250, "USDT", minutes=1)
        propagate_taint(g, "seed")
        assert g.nodes["a"].taint_fraction == 0.75
        assert g.nodes["b"].taint_fraction == 0.25

    def test_reported_amount_anchors_only_the_primary_token(self):
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "a", 1)
        _node(g, "b", 1)
        # seed moves USDT twice and TRX once -> USDT is the primary token
        _edge(g, "seed", "a", 400, "USDT")
        _edge(g, "seed", "a", 400, "USDT", minutes=1)
        _edge(g, "seed", "b", 900, "TRX", minutes=2)
        propagate_taint(g, "seed", origin_amount=200.0)

        # USDT measured against the reported 200 (capped at 1.0), TRX against
        # the seed's own TRX outflow
        assert g.nodes["a"].taint_by_token["USDT"] == 1.0
        assert g.nodes["b"].taint_by_token["TRX"] == 1.0
        assert g.nodes["a"].tainted_by_token["USDT"] == 200.0


class TestEdgeDeduplication:
    """Bug 2: the same on-chain transfer could be added twice."""

    def test_identical_transfer_is_not_counted_twice(self):
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "a", 1)
        _edge(g, "seed", "a", 100, tx="0xSAME")
        _edge(g, "seed", "a", 100, tx="0xSAME")
        assert len(g.edges) == 1

    def test_batch_payout_keeps_both_legs_of_one_hash(self):
        """One tx hash can legitimately move funds to two different wallets."""
        g = TransactionGraph(seed_address="seed")
        _node(g, "seed", 0, seed=True)
        _node(g, "a", 1)
        _node(g, "b", 1)
        _edge(g, "seed", "a", 100, tx="0xBATCH")
        _edge(g, "seed", "b", 100, tx="0xBATCH")
        assert len(g.edges) == 2


class TestUnusableAmounts:
    """Bugs 3 and 4: unparseable amounts became a silent 0.0; negatives passed."""

    def test_unparseable_amount_is_flagged(self):
        t = _transfer("a", "b", "not-a-number")
        assert t.amount_float == 0.0
        assert t.amount_is_usable is False

    def test_negative_amount_is_flagged(self):
        assert _transfer("a", "b", -500).amount_is_usable is False

    def test_zero_amount_is_flagged(self):
        assert _transfer("a", "b", 0).amount_is_usable is False

    def test_normal_amount_is_usable(self):
        assert _transfer("a", "b", 1.5).amount_is_usable is True


class TestFailuresAreNotFindings:
    """Second audit: a failed lookup must never read as 'no funds moved'."""

    def _engine_with(self, fetch):
        from unittest.mock import Mock, AsyncMock
        from src.application.trace_service import TraceEngine

        p = Mock()
        p.chain = Chain.ETHEREUM
        p.get_all_outgoing_transfers = fetch
        p.close = AsyncMock()
        return TraceEngine(), p

    def _run(self, engine, provider, **kw):
        import asyncio
        from unittest.mock import patch

        with patch.object(engine, "_get_provider", return_value=provider):
            return asyncio.run(
                engine.trace("0xseed", Chain.ETHEREUM, max_depth=2,
                             max_branches=5, **kw)
            )

    def test_total_provider_failure_is_marked_failed(self):
        from src.domain.enums import InvestigationStatus

        async def boom(address, **kw):
            raise RuntimeError("provider exploded")

        e, p = self._engine_with(boom)
        r = self._run(e, p)
        assert r.investigation.status == InvestigationStatus.FAILED
        assert "NOT evidence" in r.investigation.error

    def test_partial_failure_is_marked_partial(self):
        from src.domain.enums import InvestigationStatus

        async def half(address, **kw):
            if address.lower() == "0xa":
                raise RuntimeError("rate limited")
            return [_transfer("0xseed", "0xa", 100)]

        e, p = self._engine_with(half)
        r = self._run(e, p)
        assert r.investigation.status == InvestigationStatus.PARTIAL

    def test_clean_trace_still_completes(self):
        from src.domain.enums import InvestigationStatus

        async def ok(address, **kw):
            return [_transfer("0xseed", "0xa", 100)] if address.lower() == "0xseed" else []

        e, p = self._engine_with(ok)
        r = self._run(e, p)
        assert r.investigation.status == InvestigationStatus.COMPLETED

    def test_timezone_aware_incident_time_is_normalised(self):
        """Used to raise inside the per-hop filter and be swallowed as empty."""
        from datetime import timezone

        async def ok(address, **kw):
            return [_transfer("0xseed", "0xa", 100, minutes=5)] if address.lower() == "0xseed" else []

        e, p = self._engine_with(ok)
        r = self._run(e, p, incident_time=NOW.replace(tzinfo=timezone.utc))
        assert "0xa" in r.graph.nodes

    def test_our_own_bugs_are_not_swallowed(self):
        """A TypeError from our code must surface, not become an empty trace."""
        async def broken(address, **kw):
            return "not a list of transfers".no_such_attribute

        e, p = self._engine_with(broken)
        try:
            self._run(e, p)
        except AttributeError:
            return
        raise AssertionError("programming error was swallowed")


class TestTruncationIsDisclosed:
    """Third audit: a trace cut short by our own limits is not a complete one."""

    def test_page_cap_marks_the_address_truncated(self):
        import asyncio
        from unittest.mock import patch
        from src.blockchain.trongrid import TronGridProvider

        p = TronGridProvider(api_key="x")
        calls = {"n": 0}

        async def token(address, page=1, offset=100, **kw):
            calls["n"] += 1
            p._last_fingerprint = f"fp{calls['n']}"  # always more to fetch
            return [
                _transfer(address, f"Td{calls['n']}_{i}", 10)
                for i in range(offset)
            ]

        async def native(address, page=1, offset=100, **kw):
            p._last_fingerprint = None
            return []

        with patch.object(p, "get_token_transfers", token), \
             patch.object(p, "get_native_transfers", native):
            asyncio.run(p.get_all_outgoing_transfers("Tseed"))
        assert "Tseed" in p.truncated_addresses

    def test_a_short_page_is_not_truncation(self):
        import asyncio
        from unittest.mock import patch
        from src.blockchain.trongrid import TronGridProvider

        p = TronGridProvider(api_key="x")

        async def token(address, page=1, offset=100, **kw):
            p._last_fingerprint = None
            return [_transfer(address, "Td1", 10)]

        async def native(address, page=1, offset=100, **kw):
            p._last_fingerprint = None
            return []

        with patch.object(p, "get_token_transfers", token), \
             patch.object(p, "get_native_transfers", native):
            asyncio.run(p.get_all_outgoing_transfers("Tshort"))
        assert p.truncated_addresses == set()

    def test_time_budget_exhaustion_marks_partial(self):
        """Budget set in the past so the first check trips - no clock race.

        (Patching time.monotonic is not an option here: asyncio's event loop
        reads the same clock.)
        """
        import asyncio
        from unittest.mock import Mock, AsyncMock, patch
        from src.application.trace_service import TraceEngine
        from src.config.settings import get_settings
        from src.domain.enums import InvestigationStatus

        settings = get_settings()
        original = settings.trace_time_budget_seconds
        settings.trace_time_budget_seconds = -1_000_000
        try:
            p = Mock()
            p.chain = Chain.ETHEREUM
            p.truncated_addresses = set()

            async def fetch(address, **kw):
                if address.lower() == "0xseed":
                    return [_transfer("0xseed", f"0xd{i}", 10) for i in range(3)]
                return []

            p.get_all_outgoing_transfers = fetch
            p.close = AsyncMock()
            e = TraceEngine()
            with patch.object(e, "_get_provider", return_value=p):
                r = asyncio.run(
                    e.trace("0xseed", Chain.ETHEREUM, max_depth=4, max_branches=10)
                )
            assert r.investigation.status == InvestigationStatus.PARTIAL
            assert any("time budget" in w for w in r.investigation.warnings)
        finally:
            settings.trace_time_budget_seconds = original
