# ChainTrace — Investigation Core Brief (for Opus)

You are picking up work on the **investigative-lead engine** of ChainTrace.
This file is your context. Read `MEMORY.md` only if you need the full history.
Do NOT crawl the whole codebase — the file map below is enough.

## SCOPE — what to work on, what to ignore

**WORK ON (the "investigation lead" core):**
- `src/application/trace_service.py` — BFS fund-flow trace engine
- `src/application/investigation_service.py` — pipeline orchestration
- `src/intelligence/` — `label_matcher.py`, `exchange_inference.py`, `mixer_bridge_detector.py`
- `src/analysis/` — `pattern_detector.py`, `path_analyzer.py`, `scoring.py`, `case_summary.py`
- `src/application/report_service.py` — text/JSON report
- `src/config/settings.py` — thresholds
- `data/labels/`, `data/sanctions/`, `data/entities/` — entity intelligence datasets
- `src/domain/models/investigation.py` — `CandidateEndpoint`, `InvestigationResult` (extend if needed)

**DO NOT TOUCH / DO NOT READ:**
- `web/` — the React SPA (separate parallel build)
- `api/` — the FastAPI layer (separate parallel build)
- `app/graph_view.py` — the graph visualization (done, working)
- `app/streamlit_app.py` — only touch if you add a new result field that must render
- The visual/UX layer in general

If you change the shape of `InvestigationResult` / `CandidateEndpoint` / the
`case_summary` dict, note it at the end of this file so the API/UI can follow.

---

## WHAT CHAINTRACE PROMISES

A fraud victim reports a wallet address. ChainTrace (read-only, public data only)
must turn that address into an **investigative lead**:

```
reported wallet
  -> reconstruct where the funds moved (multi-hop)
  -> identify known entities / exchanges / sanctioned addresses
  -> detect suspicious movement patterns (layering, structuring, rapid hops)
  -> rank candidate endpoints with an EXPLAINABLE confidence score
  -> hand the investigator: "the money most likely went here, N hops, X% of it
     arrived, here is the evidence, here is what to do next"
```

It is an *investigative lead*, NOT a verdict / attribution / legal determination.
Language: "Investigative Lead", "Potential Exchange Endpoint", "Suspicious
Pattern", "Confidence" — never "Criminal Wallet" / "Launderer".

Chains: Ethereum + BNB Chain (Etherscan V2), Tron/USDT-TRC20 (TronGrid).

---

## THE PIPELINE (`investigation_service.run_investigation`)

```python
result = await trace_engine.trace(seed, chain, max_depth=4, max_branches=25, token_filter=None)
await _enrich_with_intelligence(result)   # label_matcher + exchange_inference + mixer_bridge_detector
_detect_patterns(result)                   # pattern_detector.detect_all -> node.pattern_flags, is_suspicious
_analyze_paths(result)                     # path_analyzer -> result.candidate_endpoints
_score_endpoints(result)                   # scoring.calculate per endpoint
result.case_summary = build_case_summary(result)   # analysis/case_summary.py
```

### 1. Trace (`trace_service.py`)
BFS from the seed, OUTGOING transfers only.
- Seed key normalized (lowercase for EVM, base58 as-is for Tron).
- `visited` gates only queue expansion; **every real transfer becomes an edge**
  (repeat A->B transfers all get edges).
- `max_branches` = max *distinct new wallets* expanded per node.
- Drops transfers with amount `0` or `>= settings.max_plausible_transfer_amount`
  (1e9 units) — kills scam/airdrop tokens.
- `settings.trace_time_budget_seconds` (120) bounds the whole BFS.
- `settings.per_address_fetch_timeout_seconds` (40) bounds one address's fetch.
- Produces `InvestigationResult` with `.graph` (nodes + edges), `.investigation`,
  `.seed_address`, `.warnings`.

### 2. Enrich (`investigation_service._enrich_with_intelligence`)
For each graph node:
- `label_matcher.match(address)` -> if a dataset label exists, replace
  `node.address` with the labelled one; set `node_type` (EXCHANGE/SANCTIONED/
  MIXER/BRIDGE) and `is_endpoint` / `is_obfuscation_point`.
- `exchange_inference.infer(graph)` -> dict `{addr: confidence}` for wallets that
  converge on a wallet **already labelled EXCHANGE**. **Currently near-dead on
  Tron because there are no Tron exchange labels.**
- `mixer_bridge_detector.detect(graph)` -> list of addresses matching a
  hard-coded ETH-only mixer/bridge contract set. **No Tron/BSC coverage.**

### 3. Patterns (`pattern_detector.detect_all`) — pure functions on the graph
Returns `dict[str, list[dict]]` keyed by pattern name. Each detection has an
`"address"` (or `"path"`). `investigation_service` appends the pattern name to
`node.pattern_flags` and marks `is_suspicious` for fan_out / rapid_multi_hop /
peel_behavior.

| pattern | fires when |
|---|---|
| `fan_out` | >= 5 distinct recipients from one address |
| `hop_velocity` | shortest gap between an inflow and the first later outflow moving >= 50% of it, <= `hop_velocity_threshold_seconds` (300). Reports `received_to_forwarded_seconds`, `received_amount`, `forwarded_amount` |
| `micro_fan_out` | >= `micro_fanout_threshold` (10) transfers, avg < 100, many unique recipients |
| `rapid_multi_hop` | path >= 3 hops with small avg hop time (uses `graph.get_paths_to_endpoints`) |
| `peel_behavior` | one dominant outgoing transfer carries `peel_forward_ratio_min` (0.7) <= ratio < 1.0 of incoming, `peeled > 0`, and `|total_out - incoming| <= 15%` of incoming (excludes pure relays + aggregation hubs) |

### 3b. Taint (`analysis/taint.py`) — runs before path analysis
`propagate_taint(graph, seed_addr, origin_amount=None)` — haircut model. Splits
the seed's tainted value across each wallet's outgoing transfers in proportion to
value; a wallet can never forward more taint than value it moved; unforwarded
taint is retained. Depth-ordered, forward-only (cycles terminate; back/side edges
are credited on the edge but not re-propagated). Writes `tainted_value` /
`taint_fraction` on nodes and `tainted_value` on edges.
`dominant_path(graph, seed, target)` walks back along the highest-taint inbound
edge = the path the MONEY took.
Pass `origin_amount` to anchor on a specific reported stolen amount.

### 4. Paths (`path_analyzer.find_candidate_endpoints`)
A candidate is any node that is labelled EXCHANGE/SANCTIONED, OR `is_endpoint`,
OR **terminal in the traced window holding >= `min_endpoint_taint_fraction`
(0.01) of the seed's taint** — the last clause is what makes unlabelled chains
produce leads. Ranked by taint, capped at `max_candidate_endpoints` (25).
- path = `dominant_path` (falls back to `nx.all_simple_paths` shortest when there
  is no taint data, e.g. graphs built directly in unit tests).
- `total_amount_received` = amount of the **final transfer into the endpoint**.
- `amount_concentration` = the node's `taint_fraction` when available (already
  accounts for every split), else the old `received / seed_total_outflow` ratio.
- `unlabeled_hop_count` = intermediate nodes with `entity_type == UNKNOWN`.
- `path_directness = 1/(1+unlabeled_hops)`.
- `label_confidence = endpoint.address.confidence` (0 for unlabelled!).
- `obfuscation_points` = mixers/bridges on the path.
- `elapsed_seconds`, `evidence[]`, `path`, plus `tainted_value`,
  `taint_fraction`, `is_terminal`.

### 5. Scoring (`scoring.calculate(endpoint, graph, seed)`) — TWO AXES
- `flow_confidence = amount_concentration * (0.6 + 0.4*path_directness)` — how
  sure the money went here. Always computable, no labels. Taint already accounts
  for splits, so directness only modulates (never penalise a long chain twice).
- `entity_confidence` = label tier only — how sure we are WHAT this address is.
- `score = flow_weight(0.65)*flow + 0.35*entity`, capped at `mixer_score_cap`
  when the path crosses a mixer/bridge.
- prefers components already on the endpoint when > 0, else derives.
- `label_confidence`: sanctioned 1.0 / verified exchange 0.85 / sweep 0.60 /
  curated 0.50 / else `address.confidence` (0 unlabelled).
- if `obfuscation_points > 0`: `score = min(score, mixer_score_cap=0.40)`.
- Returns `{score, flow_confidence, entity_confidence, components{...5 keys},
  reasons:[...], pattern_flags:[...]}`.
- flow 0.9 / entity 0.0 = "the money is here, owner unknown" — the most
  actionable lead type. Reported separately so it is never hidden by a blend.

### 6. Case summary (`analysis/case_summary.py::build_case_summary`)
Behavioural synthesis — works with ZERO labels. Returns:
```python
{seed, chain, generated_at, headline,
 overview: {total_sent_by_seed, direct_recipients, first_activity, last_activity,
            wallets_in_graph, transfers_in_graph, max_depth_traced,
            value_left_observed_window},
 findings: [ {severity: high|medium|low, type, title, detail, addresses[], metrics{}} ],
 recommended_leads: [ {address, reason, score, verified} ],
 limitations: [...], disclaimer: "..." }
```
Finding types produced today: `structuring` (from fan_out + burst-window check),
`rapid_forwarding` (from hop_velocity), `convergence` (>= 4 distinct in-graph
senders re-converge at one wallet), `entity_hit` (labelled exchange/sanctioned/
mixer/bridge on the graph).
`recommended_leads`: verified (labelled) or unverified (convergence, score
`min(0.75, 0.35 + 0.05*n_senders)`); falls back to highest-value frontier
wallets when nothing labelled/converged.

---

## CURRENT STATE — what works

- Ethereum + Tron tracing is correct and validated against live APIs.
- 37 unit/integration tests pass (`python -m pytest -q`).
- Offline ground-truth check passes 8/8:
  `python scripts/seed_demo_data.py && python scripts/validate_synthetic.py`
  (a synthetic 5-hop USDT->Binance case; proves path/amount/endpoint/pattern/
  score reconstruction).
- On the synthetic case (labels present): headline "Path reaches exchange:
  Binance Hot Wallet", lead 0.70 VERIFIED. Good.
- On a real Tron hub (`TDqSquXBgUCLYvYC4XZgrprLK589dkhSCf`, no labels): headline
  "Seed moved N USDT into a network of 121 wallets; dispersed in bursts
  (layering); [frontier leads]". Behaviourally useful, but **0 verified
  endpoints, no exchange identified.**

---

## WHERE THE INVESTIGATION LOGIC IS WEAK — the actual work

Ranked by impact on lead quality:

> STATUS 2026-09-08: items #2-#8 are all DONE, plus case anchoring. #1 (real
> entity datasets) is now an ENHANCEMENT rather than a blocker — unlabelled
> chains produce ranked, typed leads on their own. That is the only item left,
> and it is a data-sourcing task, not an engineering one: follow
> `data/entities/README.md` and never hand-write an address you cannot source.

1. **No real entity data for Tron (and thin for ETH).** `data/entities/` has 3
   ETH exchange addresses + Tornado Cash. `label_matcher` auto-loads any JSON in
   `data/labels|sanctions|entities/` (schema in `data/entities/README.md`).
   Every Tron trace produces 0 verified leads because we can't recognise a
   single exchange. Highest-leverage fix: ship a curated set of well-known Tron
   exchange hot/deposit wallets (Binance/OKX/Bybit/HTX/Gate on Tron), OFAC SDN
   Tron addresses, and top ETH exchange/mixer labels — sourced from public
   block-explorer tags — with correct confidence tiers. NEVER invent an address.

2. **`exchange_inference.infer()` is effectively dead without labels.** It only
   flags convergence sources when the convergence *target* is ALREADY labelled
   EXCHANGE. Spec §13 wants Tier-3 inference: many deposit addresses -> one
   sweep wallet -> that wallet forwards to a small number of destinations =
   "likely exchange deposit infrastructure (UNVERIFIED)". Implement a
   label-independent version: score a wallet as a probable exchange-deposit /
   consolidation point from (a) high in-degree from distinct traced wallets,
   (b) low out-degree / single dominant recipient, (c) fast turnaround
   (hop_velocity), (d) large aggregate value. Require MULTIPLE signals (spec
   §18: no single heuristic as proof). Feed the result into
   `candidate_endpoints` so it gets scored, and into `case_summary` findings.

3. **`path_analyzer` uses shortest-path + a diluted concentration.**
   - `amount_concentration` divides by the seed's TOTAL outflow — if the victim's
     wallet also did legit sends, the ratio is understated. Consider anchoring on
     the specific reported amount / the largest single seed outflow, or a
     value-weighted trace that follows THE money (at each hop, follow the
     outgoing transfer closest to the incoming amount and carry that forward).
   - Shortest path ignores the dominant-value path. Prefer the path that
     preserves the most value.
   - No time anchoring: real cases are "5,000 USDT stolen on 2026-05-01" — should
     be able to trace from a timestamp and follow that amount, not the wallet's
     whole lifetime.

4. **Unused pattern signals.** `micro_fan_out`, `rapid_multi_hop`, `peel_behavior`
   detections are computed and flag nodes but are NOT turned into `case_summary`
   findings. `peel_behavior` + `rapid_multi_hop` across a chain is the classic
   layering signature — surface it as a `layering` finding with the actual hop
   path and % value retained.

5. **Scoring can't rank unlabelled leads.** With `label_confidence = 0` an
   unlabelled convergence endpoint maxes around 0.45. Either (a) let the
   Tier-3/Tier-4 inference set a non-zero `label_confidence` (0.4-0.6, clearly
   marked unverified), or (b) add a 4th scoring component for
   behavioural/convergence evidence, keeping the formula explainable and bounded
   [0,1]. Whatever you do, the score MUST still return `reasons[]` a judge can
   read.

6. **No multi-signal cross-referencing.** A wallet that is simultaneously a
   convergence point AND a fast-forwarder AND forwards to <=2 destinations is a
   much stronger lead than any one signal. Build a small evidence-aggregation
   step that boosts confidence when independent signals stack, and dampens it
   when they conflict (spec §18 false-positive guardrails).

7. **Mixer/bridge coverage is ETH-only, hard-coded.** Move it to
   `data/entities/*.json` (category `mixer`/`bridge`) so it's data-driven and
   multichain, and so `label_matcher` picks it up. `mixer_bridge_detector` can
   then just read the label cache.

8. **`convergence` finding threshold + scoring is arbitrary** (`>= 4` senders,
   `0.35 + 0.05*n`). Tune against the synthetic case + a couple of real traces;
   consider weighting by aggregate value and by how many senders are <= 2 hops
   from the seed.

---

## DOMAIN MODELS YOU NEED

`InvestigationResult` (`src/domain/models/investigation.py`):
`investigation`, `graph: TransactionGraph`, `seed_address: Address`,
`candidate_endpoints: list[CandidateEndpoint]`, `suspicious_addresses`,
`pattern_detections: dict[str, list[dict]]`, `case_summary: dict`, `warnings`.

`CandidateEndpoint`: `address: Address`, `total_amount_received: str`,
`hop_count`, `unlabeled_hop_count`, `amount_concentration`, `path_directness`,
`label_confidence`, `confidence_score`, `path: list[Transfer]`, `pattern_flags`,
`obfuscation_points`, `elapsed_seconds`, `evidence: list[str]`.

`GraphNode`: `address`, `node_type`, `depth`, `incoming_amount` (str),
`outgoing_amount` (str), `first_seen`, `last_seen`, `pattern_flags`,
`is_seed/is_endpoint/is_suspicious/is_obfuscation_point`.

`GraphEdge`: `transfer: Transfer`, `edge_type`, `is_highlighted`, `path_rank`.
`Transfer`: `.normalized_from()`, `.normalized_to()`, `.amount_float`,
`token_symbol`, `token_contract`, `timestamp`, `direction`.

`Address`: `address`, `chain`, `label`, `entity_type: EntityCategory`,
`confidence: float`, `source: LabelSource|None`.
`EntityCategory`: victim_seed, intermediate, exchange, sanctioned, mixer, bridge,
contract, unknown, obfuscation_point.
`Chain`: has `.is_evm` and `.native_symbol` properties.

`settings` (`src/config/settings.py`, env-overridable): `default_trace_depth`,
`max_branches`, `mixer_score_cap` (0.40), `hop_velocity_threshold_seconds` (300),
`micro_fanout_threshold` (10), `peel_forward_ratio_min` (0.7),
`max_plausible_transfer_amount` (1e9), `trace_time_budget_seconds` (120),
`per_address_fetch_timeout_seconds` (40).

---

## RUN & TEST

```bash
python -m pytest -q                       # 37 tests, must stay green
python scripts/seed_demo_data.py
python scripts/validate_synthetic.py      # 8/8 ground-truth checks, must stay green
```

Ad-hoc real trace (needs .env with keys):
```python
import asyncio
from src.application.investigation_service import InvestigationService
from src.domain.enums import Chain
r = asyncio.run(InvestigationService().run_investigation(
    "TDqSquXBgUCLYvYC4XZgrprLK589dkhSCf", Chain.TRON, max_depth=2, max_branches=20))
print(r.case_summary["headline"])
for f in r.case_summary["findings"]: print(f["severity"], f["title"])
for l in r.case_summary["recommended_leads"]: print(l["score"], l["verified"], l["address"])
```

Rules: every new detector/heuristic is a pure function with a unit test. Keep the
score explainable and bounded. Never label an unknown address as
exchange/criminal without evidence. Update `MEMORY.md` after meaningful changes.
Env is Python 3.14 (`python` on PATH); restart Streamlit after `src/` edits.

---

## CHANGELOG FOR DOWNSTREAM (append here if you change result/summary shape)

### 2026-09-08 (c) — case anchoring + convergence tuning
- `trace(...)` and `run_investigation(...)` accept `incident_time: datetime|None`
  and `reported_amount: float|None`; both stored on `Investigation`.
- `incident_time` filters transfers PER HOP, not just globally: the BFS queue now
  carries `(address, depth, not_before)` and `not_before` for a hop is the moment
  the tainted funds actually ARRIVED there. Money cannot leave a wallet before it
  got there, so pre-arrival outflows are excluded. Emits a warning per wallet.
- `reported_amount` is passed to `propagate_taint(origin_amount=...)` so taint
  fractions are shares of the REPORTED sum, not the wallet's whole outflow.
- NEW `case_summary` finding type `coverage` (severity `info`): fires when the
  observed outflow is < 95% of the reported amount, stating what fraction of the
  reported sum is actually traceable in this window. Headline wording switches to
  "% of the reported amount" when anchored.
- Convergence tuning (#8): thresholds moved to settings —
  `convergence_min_senders` (4), `convergence_min_senders_with_value` (3),
  `convergence_material_taint` (0.05). A wallet qualifies on sender count alone,
  OR on fewer senders plus a material taint share. Collectors are now ranked by
  taint first, sender count second.
- Streamlit input form gained optional "Incident date" and "Reported amount
  stolen" fields.

### 2026-09-08 (b) — behavioural classifier, layering narrative, data-driven mixers
- NEW `src/intelligence/behavior_classifier.py` — `classify_wallets(graph,
  pattern_detections) -> {addr: WalletProfile}`. Types: `exchange_deposit`,
  `collector`, `distributor`, `pass_through`, `holding`. EVERY type needs >= 2
  independent signals; confidence is capped at 0.60 (Tier-3/4) and wording is
  always UNVERIFIED. The key label-free signal is the SHARED SWEEP DESTINATION
  (several traced wallets sweeping to the same address).
- NEW `InvestigationResult.wallet_profiles: dict[addr, {behavior, confidence,
  signals[], metrics{}}]`.
- NEW `GraphNode.behavior`, `.behavior_confidence`, `.behavior_signals[]`;
  `CandidateEndpoint.behavior`, `.behavior_confidence`.
- Only `exchange_deposit` promotes an UNKNOWN address to an entity claim, as
  `EntityCategory.EXCHANGE` + `LabelSource.SWEEP_INFERENCE` (scoring already maps
  that to entity_confidence 0.60) with label "Likely exchange deposit
  (UNVERIFIED)". Every other type is behavioural context only, never ownership.
- `case_summary.findings` gained two new types: `layering` (one finding for a
  whole chain of peel/fast-forward hops along the money path, with the hop list
  and % value retained) and `behaviour` (per classified wallet, with its
  signals).
- `mixer_bridge_detector` no longer hard-codes contracts: it reads categories
  `mixer` / `bridge` from the loaded entity datasets via
  `LabelMatcher.all_entries()` (NEW method), so coverage is multichain and
  extendable via `data/entities/mixers_bridges.json` with no code change. It now
  also flags by ADDRESS, not only by token_contract.

### 2026-09-08 (a) — taint propagation + two-axis scoring (api/ and web/ should follow)
NEW FIELDS (all additive, nothing removed — existing readers keep working):
- `GraphNode.tainted_value: float`, `GraphNode.taint_fraction: float` (0-1)
- `GraphEdge.tainted_value: float`
- `CandidateEndpoint.flow_confidence`, `.entity_confidence` (both 0-1),
  `.tainted_value`, `.taint_fraction`, `.is_terminal: bool`
- `scoring.calculate()` now also returns top-level `flow_confidence` /
  `entity_confidence`; `components` gained those two keys alongside the
  original three.
- `report_service.generate_summary()["top_endpoints"][i]` gained
  `flow_confidence`, `entity_confidence`, `taint_fraction`, `is_terminal`.
- `case_summary["recommended_leads"][i]` may carry `taint_fraction`,
  `flow_confidence`, `entity_confidence`; the list is now ordered by
  taint_fraction first, then score.
- New settings: `min_endpoint_taint_fraction` (0.01), `max_candidate_endpoints`
  (25), `flow_weight` (0.65).

BEHAVIOUR CHANGES:
- `candidate_endpoints` is no longer gated on entity labels — terminal wallets
  holding >= `min_endpoint_taint_fraction` of the seed's tainted value are now
  candidates. Unlabelled chains produce ranked leads for the first time.
- `confidence_score` = `flow_weight*flow + (1-flow_weight)*entity`, still capped
  at `mixer_score_cap` when the path crosses a mixer/bridge.
- `scripts/seed_demo_data.py` now writes `data/sanctions/synthetic_sanctions.json`
  and NO LONGER overwrites the real `data/sanctions/ofac_sdn.json`.

STILL OPEN (from the weakness list): #2 behavioural exchange-deposit classifier,
#4 unused pattern signals -> layering narrative, #6 multi-signal corroboration,
#7 data-driven mixer/bridge list, plus case anchoring (incident_time /
reported_amount -> `propagate_taint(..., origin_amount=...)` already supports it,
the trace engine does not yet filter by time).
