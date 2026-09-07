"""Offline end-to-end validation against the synthetic demo case.

Feeds data/synthetic/transfers.json through the real BFS trace engine (via a
fake in-memory provider), then the real intelligence / pattern / scoring
pipeline, and compares the reconstruction to data/synthetic/demo_case.json.

Run: python scripts/validate_synthetic.py
Exit code 0 = all checks passed.
"""

import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.application.investigation_service import InvestigationService  # noqa: E402
from src.domain.enums import Chain, TransferDirection, EdgeType  # noqa: E402
from src.domain.models.transfer import Transfer  # noqa: E402

SYNTH = ROOT / "data" / "synthetic"


def load_transfers() -> dict:
    by_from = defaultdict(list)
    for raw in json.loads((SYNTH / "transfers.json").read_text()):
        t = Transfer(
            transaction_hash=raw["transaction_hash"],
            chain=Chain.ETHEREUM,
            from_address=raw["from_address"],
            to_address=raw["to_address"],
            token_symbol=raw.get("token_symbol"),
            token_contract=raw.get("token_contract"),
            amount=raw["amount"],
            amount_raw=raw.get("amount_raw"),
            decimals=raw.get("decimals"),
            timestamp=datetime.fromisoformat(raw["timestamp"]),
            block_number=raw["block_number"],
            direction=TransferDirection.OUTGOING,
            edge_type=EdgeType.TOKEN_TRANSFER
            if raw.get("token_contract")
            else EdgeType.NATIVE_TRANSFER,
            source="synthetic",
        )
        by_from[t.normalized_from()].append(t)
    return by_from


class FakeProvider:
    chain = Chain.ETHEREUM

    def __init__(self, by_from: dict):
        self._by_from = by_from

    async def get_all_outgoing_transfers(self, address, **kwargs):
        return list(self._by_from.get(address.lower(), []))

    async def get_token_transfers(self, address, **kwargs):
        return list(self._by_from.get(address.lower(), []))

    async def close(self):
        pass


async def main() -> int:
    case = json.loads((SYNTH / "demo_case.json").read_text())
    by_from = load_transfers()
    provider = FakeProvider(by_from)

    service = InvestigationService()
    with patch.object(service.trace_engine, "_get_provider", return_value=provider):
        result = await service.run_investigation(
            seed_address=case["seed_address"], chain=Chain.ETHEREUM, max_depth=6
        )

    expected_path = [a.lower() for a in case["expected_path"]]
    expected_endpoint = case["expected_endpoint"]["address"].lower()

    checks = []

    # 1. every hop in the expected path is a node in the reconstructed graph
    nodes = set(result.graph.nodes.keys())
    checks.append(
        ("All expected path addresses present as nodes", set(expected_path) <= nodes)
    )

    # 2. every consecutive hop has an edge
    edge_pairs = {
        (e.transfer.normalized_from(), e.transfer.normalized_to())
        for e in result.graph.edges
    }
    path_edges_ok = all(
        (expected_path[i], expected_path[i + 1]) in edge_pairs
        for i in range(len(expected_path) - 1)
    )
    checks.append(("All expected path edges reconstructed", path_edges_ok))

    # 3. endpoint detected as a candidate
    ep_addrs = {ep.address.address.lower() for ep in result.candidate_endpoints}
    checks.append(("Expected exchange endpoint detected", expected_endpoint in ep_addrs))

    # 4. endpoint is the top-ranked candidate
    top = result.candidate_endpoints[0] if result.candidate_endpoints else None
    checks.append(
        (
            "Expected endpoint ranked #1",
            top is not None and top.address.address.lower() == expected_endpoint,
        )
    )

    # 5. amount reaching endpoint matches expectation (last expected amount)
    exp_amount = float(case["expected_amounts"][-1])
    got_amount = float(top.total_amount_received) if top else 0.0
    checks.append(
        (
            f"Amount at endpoint ~{exp_amount} (got {got_amount})",
            abs(got_amount - exp_amount) <= exp_amount * 0.05,
        )
    )

    # 6. expected suspicious patterns detected on the right addresses
    detected = defaultdict(set)
    for ptype, dets in result.pattern_detections.items():
        for d in dets:
            if d.get("address"):
                detected[d["address"].lower()].add(ptype)
    for pat in case["suspicious_patterns"]:
        addr = pat["address"].lower()
        checks.append(
            (
                f"Pattern '{pat['pattern']}' on {addr[:10]}...",
                pat["pattern"] in detected.get(addr, set()),
            )
        )

    # 7. score is explainable and bounded
    checks.append(
        (
            "Top endpoint score in (0, 1] with reasons",
            top is not None
            and 0.0 < top.confidence_score <= 1.0
            and len(top.evidence) > 0,
        )
    )

    print("=" * 64)
    print("CHAINTRACE SYNTHETIC VALIDATION")
    print("=" * 64)
    print(f"Seed:              {case['seed_address']}")
    print(f"Nodes found:       {result.graph.node_count}")
    print(f"Edges found:       {result.graph.edge_count}")
    print(f"Candidate endpoints: {len(result.candidate_endpoints)}")
    if top:
        print(f"Top endpoint:      {top.address.label or top.address.address}")
        print(f"  confidence:      {top.confidence_score * 100:.1f}%")
        print(f"  reasons:         {top.evidence}")
    print("-" * 64)
    passed = 0
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        passed += ok
    print("-" * 64)
    print(f"{passed}/{len(checks)} checks passed")
    print("=" * 64)

    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
