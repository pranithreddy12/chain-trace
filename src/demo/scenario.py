"""A fabricated laundering case for demos, run through the REAL pipeline.

Nothing here is faked downstream: the transfers below are synthetic, but the
trace, taint propagation, pattern detection, behavioural classification,
scoring and case summary are the production code paths. That is the point -
the demo showcases how the analysis behaves, not a mocked-up screenshot.

The case is built to exercise every capability the tool has, in the order an
investigator would meet them:

  hop 0  victim reports 250,000 USDT stolen on 14 Mar 2026
  hop 1  STRUCTURING - split into 8 near-equal parts across 8 mule wallets
  hop 2  RAPID LAYERING - every mule sweeps onward within minutes into two
         consolidation wallets (hop-velocity + convergence)
  hop 3  BRANCHING -
           - L1 runs a PEEL CHAIN, shaving small amounts into throwaways
           - L2 sends a slice into TORNADO CASH (labelled mixer, obfuscation)
           - L2 sends a slice to a SANCTIONED address (OFAC/Lazarus)
           - the bulk moves into four unlabelled deposit wallets
  hop 4  SHARED SWEEP - all four deposit wallets forward ~100% of their balance
         to the same address. No label exists for them, but that shape is
         exchange deposit infrastructure, and the behaviour classifier calls it
         without any external data.
  hop 5  CASH-OUT - that shared address forwards to a real, labelled Binance
         hot wallet: the off-ramp where a subpoena can actually be served.

A pre-incident transfer is also included and must be excluded by the incident
date filter, demonstrating case anchoring.

Every address is invented except the mixer, sanctioned and exchange addresses,
which are genuine public ones taken from data/ so entity matching runs for
real.
"""

from datetime import datetime, timedelta
from typing import Dict, List
from unittest.mock import patch

from ..domain.enums import Chain, TransferDirection
from ..domain.models.transfer import Transfer

# --- real, publicly documented addresses (so entity matching is exercised) ---
BINANCE_HOT = "0x28c6c06298d514db089934071355e5743bf21d60"
TORNADO_CASH = "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf"
SANCTIONED = "0x098b716b8aaf21512996dc57eb0615e2383e2f96"  # Lazarus Group

# --- invented case addresses ---
DEMO_SEED = "0xdef1000000000000000000000000000000000001"
_MULES = [f"0xdef10000000000000000000000000000000010{i:02d}" for i in range(8)]
_LAYER = ["0xdef1000000000000000000000000000000002001",
          "0xdef1000000000000000000000000000000002002"]
_PEELS = [f"0xdef10000000000000000000000000000000030{i:02d}" for i in range(4)]
_DEPOSITS = [f"0xdef10000000000000000000000000000000040{i:02d}" for i in range(4)]
_HOT_FUNNEL = "0xdef1000000000000000000000000000000005001"
_PRE_INCIDENT = "0xdef1000000000000000000000000000000009001"

INCIDENT = datetime(2026, 3, 14, 9, 0, 0)
REPORTED_AMOUNT = 250_000.0
DEMO_MAX_DEPTH = 6  # the cash-out sits at hop 5; anything less truncates the story

# Shown beside the results so the demo explains what it is proving.
DEMO_WALKTHROUGH = [
    ("Case anchoring",
     "The victim reported 250,000 USDT taken on 14 Mar 2026. An earlier "
     "transfer from the same wallet is ignored: funds cannot move before the "
     "incident, so it is not part of this case."),
    ("Hop 1 - structuring",
     "The theft is immediately split into 8 near-equal parts. Equal splits "
     "across fresh wallets are a deliberate attempt to break the trail."),
    ("Hop 2 - rapid layering",
     "Every mule forwards its share within minutes. Hop velocity that fast "
     "means the wallets are relays, not owners - and all 8 converge on just "
     "two wallets."),
    ("Hop 3 - obfuscation",
     "One branch peels small amounts into throwaway wallets; another sends "
     "slices into Tornado Cash and to an OFAC-sanctioned address. Taint "
     "propagation keeps attributing the remaining value despite the noise."),
    ("Hop 4 - unlabelled deposit cluster",
     "Four wallets with no label anywhere each forward ~100% of their balance "
     "to the same destination. That shared-sweep shape is exchange deposit "
     "infrastructure, and it is identified from behaviour alone."),
    ("Hop 5 - the off-ramp",
     "That destination pays into a labelled Binance hot wallet. This is the "
     "actionable output: a named exchange, the amount that reached it, and the "
     "path it took - what a subpoena or freeze request is built on."),
]


def _t(frm: str, to: str, amount: float, minutes: int) -> Transfer:
    minutes = int(minutes)
    when = INCIDENT + timedelta(minutes=minutes)
    return Transfer(
        transaction_hash=f"0xdemo{frm[-6:]}{to[-6:]}{minutes:+06d}",
        chain=Chain.ETHEREUM,
        from_address=frm,
        to_address=to,
        token_symbol="USDT",
        token_contract="0xdac17f958d2ee523a2206206994597c13d831ec7",
        amount=f"{amount:.2f}",
        timestamp=when,
        block_number=21_000_000 + max(0, minutes),
        direction=TransferDirection.OUTGOING,
    )


def _build_transfers() -> Dict[str, List[Transfer]]:
    out: Dict[str, List[Transfer]] = {}

    def add(t: Transfer) -> None:
        out.setdefault(t.from_address.lower(), []).append(t)

    # --- case anchoring: this one predates the incident and must be dropped ---
    add(_t(DEMO_SEED, _PRE_INCIDENT, 40_000.0, -60 * 24 * 11))

    # --- hop 1: structuring into 8 near-equal parts within 14 minutes ---
    share = REPORTED_AMOUNT / len(_MULES)
    for i, mule in enumerate(_MULES):
        add(_t(DEMO_SEED, mule, share - 25 * i, 2 + i))

    # --- hop 2: every mule sweeps ~99% onward within minutes, converging on 2 ---
    into_layer = [0.0, 0.0]
    for i, mule in enumerate(_MULES):
        li = i % 2
        moved = (share - 25 * i) * 0.99
        into_layer[li] += moved
        add(_t(mule, _LAYER[li], moved, 6 + i * 2))

    # --- hop 3a: L1 runs a peel chain - shave a little, keep the rest moving ---
    l1_left = into_layer[0]
    for i, peel in enumerate(_PEELS):
        cut = 1_400.0 - 150 * i
        add(_t(_LAYER[0], peel, cut, 25 + i * 6))
        l1_left -= cut

    # --- hop 3b: L2 buys obfuscation - a mixer and a sanctioned counterparty ---
    l2_left = into_layer[1]
    add(_t(_LAYER[1], TORNADO_CASH, 9_000.0, 30))
    add(_t(_LAYER[1], SANCTIONED, 4_500.0, 34))
    l2_left -= 13_500.0

    # --- hop 3c: the bulk moves into four unlabelled deposit wallets ---
    for i, dep in enumerate(_DEPOSITS):
        src = _LAYER[i % 2]
        pot = l1_left if i % 2 == 0 else l2_left
        add(_t(src, dep, pot / 2.0, 40 + i * 5))

    # --- hop 4: SHARED SWEEP - all four forward ~100% to the same address ---
    for i, dep in enumerate(_DEPOSITS):
        pot = (l1_left if i % 2 == 0 else l2_left) / 2.0
        add(_t(dep, _HOT_FUNNEL, pot * 0.998, 70 + i * 4))

    # --- hop 5: cash-out at a labelled exchange ---
    swept = sum(
        (l1_left if i % 2 == 0 else l2_left) / 2.0 * 0.998
        for i in range(len(_DEPOSITS))
    )
    add(_t(_HOT_FUNNEL, BINANCE_HOT, swept * 0.999, 95))
    return out


class DemoProvider:
    """Stands in for a blockchain API. Same surface TraceEngine calls."""

    chain = Chain.ETHEREUM

    def __init__(self) -> None:
        self._by_from = _build_transfers()

    async def get_all_outgoing_transfers(self, address, **_kwargs):
        return list(self._by_from.get(address.lower(), []))

    async def close(self) -> None:
        return None


async def run_demo_investigation(service, max_depth: int = DEMO_MAX_DEPTH,
                                 use_incident: bool = True):
    """Run the demo case through the production investigation pipeline."""
    with patch.object(
        service.trace_engine, "_get_provider", return_value=DemoProvider()
    ):
        return await service.run_investigation(
            seed_address=DEMO_SEED,
            chain=Chain.ETHEREUM,
            max_depth=max(max_depth, DEMO_MAX_DEPTH),
            max_branches=25,
            incident_time=INCIDENT if use_incident else None,
            reported_amount=REPORTED_AMOUNT,
        )
