# Entity intelligence data

Every `*.json` file in `data/labels/`, `data/sanctions/` and `data/entities/`
is loaded on startup by `src/intelligence/label_matcher.py`. No code changes are
needed to add data — drop in a file with the schema below.

## Schema

```json
[
  {
    "address": "0x…  or  T…",
    "chain": "ethereum | bsc | tron",
    "label": "Binance 14",
    "category": "exchange | sanctioned | mixer | bridge | contract | victim_seed",
    "source": "ofac_sdn | verified_exchange | tronscan_public | curated_public | eth_labels",
    "confidence": 0.85
  }
]
```

`address` for `ethereum`/`bsc` is lowercased on load; `tron` base58 is kept
as-is (case-sensitive).

## Confidence tiers (see spec §8.4)

| tier | meaning | confidence | example `source` |
|------|---------|-----------|------------------|
| 1 | authoritative / government | 1.0 | `ofac_sdn` |
| 2 | verified public label | 0.80–0.90 | `verified_exchange` |
| 3 | public block-explorer label (scraped) | 0.70–0.80 | `tronscan_public`, `eth_labels` |
| 4 | weak heuristic / hand note | 0.40–0.60 | `curated_public` |

The scoring engine maps these to `label_confidence` in the final score, and the
report marks anything below tier 2 as **UNVERIFIED**.

## Where to get real data (public sources)

- **OFAC SDN crypto addresses** — <https://sanctionssearch.ofac.treas.gov/> /
  the SDN list `SDN_ENHANCED.xml` (Digital Currency Address fields). Government
  data, tier 1. `data/sanctions/ofac_sdn.json` currently holds the Tornado Cash
  + Lazarus ETH set; add Tron (`XBT`/`USDT` designations, e.g. Garantex) from the
  same list.
- **Ethereum exchange / contract labels** — the `brianleect/etherscan-labels`
  and `dawsbot/eth-labels` GitHub repos (scraped Etherscan public tags). Tier 3.
- **Tron exchange hot wallets** — TronScan publishes address tags; export from
  <https://tronscan.org/#/data/stats2/tokens/overview> or the TronScan label
  API. Save as `data/entities/tron_exchanges.json` with
  `"source": "tronscan_public", "confidence": 0.78`. Tier 3.
- **Mixers / bridges** — `src/intelligence/mixer_bridge_detector.py` holds a
  hard-coded ETH set; extend `data/entities/` with a `mixers.json` /
  `bridges.json` using `category: "mixer"` / `"bridge"`.

## Important

Never add an address you are not confident about, and never label something
`exchange` without a real source — a wrong label produces a false
"funds reached Binance" lead. When unsure, leave it out: the behavioural
findings (convergence, structuring, layering) work without labels.
