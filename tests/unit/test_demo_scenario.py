"""The demo case must exercise the real pipeline, not return canned output."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

import graph_view  # noqa: E402

from src.application.investigation_service import InvestigationService
from src.demo.scenario import (  # noqa: E402
    BINANCE_HOT,
    DEMO_SEED,
    SANCTIONED,
    TORNADO_CASH,
    run_demo_investigation,
)


@pytest.fixture(scope="module")
def demo():
    import asyncio

    return asyncio.run(run_demo_investigation(InvestigationService()))


class TestDemoCase:
    def test_reaches_the_labelled_exchange(self, demo):
        assert BINANCE_HOT in demo.graph.nodes
        assert demo.graph.nodes[BINANCE_HOT].is_endpoint

    def test_pre_incident_transfer_is_excluded(self, demo):
        # the decoy send predates the incident by nine days
        assert "0xdef1000000000000000000000000000000000505" not in demo.graph.nodes

    def test_real_detectors_fire(self, demo):
        assert demo.pattern_detections.get("peel_behavior")
        assert demo.pattern_detections.get("hop_velocity")

    def test_taint_reaches_the_endpoint(self, demo):
        assert demo.graph.nodes[BINANCE_HOT].taint_fraction > 0.9
        assert demo.graph.nodes[DEMO_SEED].taint_fraction == 1.0


class TestConfidenceColour:
    def test_ramp_is_monotonic(self):
        colors = [graph_view._conf_color(s) for s in (0.1, 0.5, 0.7, 0.95)]
        assert len(set(colors)) == 4, "each confidence band needs its own colour"

    def test_fact_types_ignore_the_ramp(self, demo):
        payload = graph_view._build_payload(demo, None)
        by_id = {n["id"]: n for n in payload["nodes"]}
        assert by_id[DEMO_SEED]["color"] == graph_view._TYPE_COLOR["seed"]
        assert by_id[BINANCE_HOT]["color"] == graph_view._TYPE_COLOR["exchange"]

    def test_suspects_are_coloured_by_their_score(self, demo):
        payload = graph_view._build_payload(demo, None)
        # the seed is also typed suspicious_wallet once fan-out fires, but it
        # keeps the seed colour - it is the reported wallet, not a lead
        suspects = [
            n for n in payload["nodes"]
            if n["type"] == "suspicious_wallet" and n["id"] != DEMO_SEED
        ]
        assert suspects, "the demo case must produce suspect wallets"
        for n in suspects:
            assert n["score"] is not None
            assert n["color"] == graph_view._conf_color(n["score"])


class TestEntityClassificationSurvives:
    """Marking a node an endpoint must not erase what it is."""

    def test_sanctioned_hit_keeps_its_type(self, demo):
        node = demo.graph.nodes[SANCTIONED]
        assert node.node_type.value == "sanctioned_address"

    def test_mixer_keeps_its_type(self, demo):
        assert demo.graph.nodes[TORNADO_CASH].node_type.value == "mixer"

    def test_exchange_keeps_its_type(self, demo):
        assert demo.graph.nodes[BINANCE_HOT].node_type.value == "exchange"

    def test_behaviour_never_asserts_an_exchange_entity(self, demo):
        """Sweep-shaped behaviour is Tier-3 - it must not become a hard label."""
        for addr, n in demo.graph.nodes.items():
            if n.address.source and n.address.source.value == "sweep_inference":
                assert n.address.entity_type.value != "exchange", addr
