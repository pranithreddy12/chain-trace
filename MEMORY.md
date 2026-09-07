# ChainTrace Project Memory

> NOTE (2026-09-08): the original MEMORY.md was truncated by a parallel tool to a
> placeholder. This file was reconstructed from the working session transcript.
> Structure follows CLAUDE.md. Sections at top = current state; ACTION LOG below.

---

## CURRENT STATUS
Backend pipeline complete and hardened. Streamlit UI works with a custom
"Fund Flow Constellation" force-graph view (flow layout, velocity-driven
particles, fullscreen). Investigative synthesis layer (`case_summary`) added.
A separate FastAPI + React SPA is being built in parallel (`api/`, `web/`) by
another tool as a read-only layer over the same `src/` backend — do not let it
and the Streamlit app diverge in behaviour; `src/` is the single source of truth.

## CURRENT PHASE
Phase 15 — demo hardening + FastAPI/React SPA (parallel).

## NEXT ACTION
1. Load real Tron exchange hot-wallet labels into `data/entities/tron_exchanges.json`
   (TronScan public tags, `source: tronscan_public`, confidence ~0.78) so Tron
   traces produce VERIFIED endpoint leads instead of only behavioural findings.
2. Manual click-through of the Streamlit app end-to-end on the synthetic case +
   one real Tron trace; confirm the constellation, case summary, tables, report.
3. Finish/verify the FastAPI + React SPA (`api/main.py`, `web/`).

## BLOCKED
None. Real Etherscan V2 + TronGrid APIs validated. Free Etherscan V2 key does
NOT cover BSC (needs a BscScan V1 key or a paid plan).

---

## ARCHITECTURE
Layered, no business logic in the UI:
`domain -> application -> blockchain -> intelligence -> analysis -> graph -> persistence -> reports`
- `src/domain/` — Pydantic models (Address, Transfer, Transaction, Investigation,
  Graph) + enums. Chain enum has `.is_evm` and `.native_symbol` properties.
- `src/blockchain/` — provider interface (`base.py`), `normalizer.py`,
  `etherscan.py` (also serves BSC), `trongrid.py`.
- `src/persistence/` — `database.py` (SQLite schema), `repositories.py`.
- `src/application/` — `trace_service.py` (BFS engine), `investigation_service.py`
  (orchestration), `report_service.py`.
- `src/intelligence/` — `label_matcher.py`, `exchange_inference.py`,
  `mixer_bridge_detector.py`.
- `src/analysis/` — `pattern_detector.py`, `path_analyzer.py`, `scoring.py`,
  `case_summary.py` (NEW 2026-09-08).
- `app/streamlit_app.py` — UI. `app/graph_view.py` — the constellation component.
- `api/`, `web/` — FastAPI + React SPA (parallel build).

## ENVIRONMENT / RUN
- TWO Python installs on this machine. Use **Python 3.14** (`python` on PATH ->
  `C:\Users\prani\AppData\Local\Programs\Python\Python314`). All deps live there
  via `python -m pip install -r requirements.txt`.
- Run the app with `python -m streamlit run app/streamlit_app.py` — NOT the bare
  `streamlit` command (that resolves to a separate Python 3.11 install missing
  plotly/etc.).
- `app/streamlit_app.py` prepends the project root to `sys.path` so `from src...`
  works from any launch dir.
- **Streamlit does NOT hot-reload imported modules** (`src/*`, `app/graph_view.py`).
  After editing any of those, FULLY RESTART the server or you test stale code.
- `.env` at repo root: `ETHERSCAN_API_KEY`, `TRONGRID_API_KEY`, `BSCSCAN_API_KEY`,
  `TRACE_TIME_BUDGET_SECONDS`, `PER_ADDRESS_FETCH_TIMEOUT_SECONDS`,
  `MAX_PLAUSIBLE_TRANSFER_AMOUNT`, `PEEL_FORWARD_RATIO_MIN`, `SUPPORTED_CHAINS`.

## DATA SOURCES
- Ethereum + BSC: Etherscan API **V2** (`https://api.etherscan.io/v2/api`,
  multichain via `chainid`). BSC also supported via BscScan V1
  (`https://api.bscscan.com/api`, no chainid) when `BSCSCAN_API_KEY` is set —
  because the free Etherscan V2 tier rejects BSC.
- Tron / USDT-TRC20: TronGrid (`https://api.trongrid.io`). Pagination uses the
  opaque `meta.fingerprint` token (NOT a numeric offset).
- Entity intel: every `*.json` in `data/labels/`, `data/sanctions/`,
  `data/entities/` is auto-loaded by `LabelMatcher._load_labels`. See
  `data/entities/README.md` for schema + confidence tiers + where to get real
  data. Currently loaded: 3 ETH exchanges (verified_exchange), 6 ETH sanctions
  (Tornado Cash + Lazarus, ofac_sdn), 2 synthetic. NO Tron labels yet.

## DATABASE (SQLite, `./data/chain_trace.db`)
Tables: `transactions`, `addresses`, `investigations`, `investigation_nodes`,
`investigation_edges`, `candidate_endpoints`. Explicit schema in
`persistence/database.py`. The `transactions` table is a write-only audit record
during tracing — the trace engine does NOT read it back (a partial earlier trace
must never shadow the live API).

## GRAPH MODEL
`TransactionGraph` (Pydantic) holding `nodes: dict[str, GraphNode]`,
`edges: list[GraphEdge]`, plus a lazily built NetworkX `DiGraph` (`nx_graph`).
Node keys: lowercased hex for EVM, base58 as-is for Tron.
- `GraphNode`: address, node_type, depth, incoming/outgoing_amount (str),
  first/last_seen, pattern_flags, is_seed / is_endpoint / is_suspicious /
  is_obfuscation_point.
- `GraphEdge`: transfer, edge_type, is_highlighted, is_suspicious, path_rank.
- Every observed transfer = one edge (repeated A->B transfers are NOT collapsed
  in the model; the UI aggregates them per pair for display).

## SCORING (`analysis/scoring.py`)
`score = 0.30*path_directness + 0.35*amount_concentration + 0.35*label_confidence`
- `path_directness = 1/(1+unlabeled_hops)`
- `amount_concentration = amount_reaching_endpoint / seed_original_amount` (<=1)
- `label_confidence`: OFAC/sanctioned 1.0, verified exchange 0.85, sweep
  inference 0.60, curated 0.50, else `address.confidence`.
- If `obfuscation_points > 0`: final score capped at `mixer_score_cap` (0.40).
- `calculate()` prefers components already computed by the path analyzer when
  they're > 0; otherwise derives them. Returns `{score, components, reasons,
  pattern_flags}`.

## PATTERN DETECTION (`analysis/pattern_detector.py`) — pure functions
- `fan_out` — >=5 distinct recipients from one address.
- `hop_velocity` — per address, shortest gap between an inflow and the first
  later outflow moving >=50% of it. Reports `received_to_forwarded_seconds` +
  amounts. (REWRITTEN 2026-09-08 — was min gap between any two timestamps.)
- `micro_fan_out` — many small (<100) transfers to many unique recipients
  (threshold `micro_fanout_threshold`, default 10).
- `rapid_multi_hop` — path of >=3 hops with avg hop time small.
- `peel_behavior` — one dominant outgoing transfer carries
  `peel_forward_ratio_min (0.7) <= ratio < 1.0` of incoming, `peeled > 0`, and
  `|total_out - incoming| <= 15% of incoming` (excludes pure relays and
  aggregation hubs). (TIGHTENED 2026-09-08.)

## CASE SUMMARY (`analysis/case_summary.py`) — NEW 2026-09-08
`build_case_summary(result) -> dict`: `{seed, chain, generated_at, headline,
overview, findings[], recommended_leads[], limitations[], disclaimer}`.
- overview: total_sent_by_seed, direct_recipients, first/last_activity,
  wallets_in_graph, transfers_in_graph, max_depth_traced,
  value_left_observed_window (funds that entered un-expanded frontier wallets).
- findings (ranked high/medium/low): `structuring` (dispersal bursts within a
  1h window), `rapid_forwarding` (from hop_velocity), `convergence` (>=4 distinct
  in-graph senders re-converge at one wallet — the strongest lead type when
  labels are absent), `entity_hit` (exchange/sanctioned/mixer/bridge label).
- recommended_leads: verified (labelled) or unverified (convergence) with a
  score; falls back to highest-value frontier wallets when nothing labelled or
  converged. Behavioural — works with zero entity labels.
- Wired: `InvestigationResult.case_summary`, `investigation_service` (after
  scoring), `report_service` (INVESTIGATIVE FINDINGS + RECOMMENDED LEADS), and
  `app/streamlit_app.py::render_case_summary` ("Investigative Assessment"
  section, above the graph).

## TRACE ENGINE (`application/trace_service.py`)
BFS from a seed. Key behaviours:
- Seed address normalized (lowercased for EVM) at entry so its node key matches
  downstream lookups.
- `visited` gates only QUEUE EXPANSION; every real transfer still becomes an
  edge (repeated transfers to an already-added wallet all get edges).
- Branch limit = max distinct NEW wallets expanded from a node (default 25).
- Always fetches live; drops transfers not originating from the queried address,
  and drops transfers with amount 0 or `>= max_plausible_transfer_amount`
  (1e9 units — kills scam/airdrop tokens with inflated nominal amounts).
- `trace_time_budget_seconds` (120): checked between queue pops -> partial
  results + warning.
- `per_address_fetch_timeout_seconds` (40): `asyncio.wait_for` around each
  address's provider fetch so one slow/flaky response can't hang for minutes.

## CONSTELLATION GRAPH (`app/graph_view.py`) — replaces the old plotly graph
Self-contained HTML/JS via `st.components.v1.html`, running **force-graph**
(2D canvas, UMD) from `https://cdn.jsdelivr.net/npm/force-graph@1.43.5`.
- `_build_payload(result, highlighted_path)` -> `{nodes, links, pruned, total}`.
  Prunes to `MAX_RENDER_NODES = 110` (keeps seed / endpoints / suspicious /
  obfuscation / on-path / labelled entities / case-summary leads, then fills by
  wallet value). Each node also carries `fromN`/`toN` (neighbour counts) +
  `fromEx`/`toEx` (up to 6 short ids). Each link carries `speed`/`parts` derived
  from the destination wallet's `hop_velocity` gap, plus `count`/`total`/`tokens`.
- **Flow layout:** `.dagMode('lr').dagLevelDistance(46/68).onDagError(()=>true)`
  + `.linkDirectionalArrowLength(3.6)` — nodes lay out LEFT -> RIGHT by hop
  (seed at far left = hop 0). Toolbar "flow view" button toggles dagMode vs a
  free force layout.
- **Particle speed = hop velocity**: fast-forwarders (<=30s) race with 3
  particles; dormant / dead-end / unknown crawl with 1. onPath +40% speed +2.
- Stars = glow + white core (the 4-point diffraction "plus" spikes were removed
  per user request). Seed pulses slow, suspicious/sanctioned flicker.
- Animated twinkling starfield on a separate `<canvas>` behind the graph.
- Hover: address / hop / type / in-out / "from N -> to N" / patterns.
  Click a star: zoom + a bottom-left panel with "received from ..." /
  "sent onward to ..." (seed panel says "this is the reported wallet (hop 0)").
- **Fullscreen:** toolbar "full screen" -> `wrap.requestFullscreen()` with a
  `fixed inset:0` overlay fallback; `syncSize()` on fullscreenchange/resize
  re-sizes graph + sky canvas + refits. Streamlit-side `st.toggle("Expand
  graph")` in `render_main_graph` bumps component height 660 -> 1000.
- **CRITICAL ordering:** `Graph.graphData(DATA)` MUST be called BEFORE any
  `Graph.d3Force('charge')` — the d3 forces are only created once data is set;
  touching them earlier throws and the canvas renders 100% blank. A
  `window.addEventListener('error')` at the top of the template now prints
  `graph error: <msg>` into `#graph` so a broken render is never a silent blank.
- Fit logic: fit to the investigative-core nodes (not far-drifting outliers)
  when there are >=5, else fit all; clamp zoom to [0.5, 2.6]; re-fit on a short
  interval while the layout settles + on `onEngineStop`.

## TESTING
- `python -m pytest -q` -> 37 pass. Unit: domain models, normalizer, analysis
  (patterns + scoring), trace engine. Integration: investigation service +
  report service.
- `tests/conftest.py` routes every test to a throwaway SQLite file
  (`DATABASE_PATH` env + singleton reset) so runs never touch the dev DB or leak
  cached transfers.
- Offline E2E: `python scripts/seed_demo_data.py && python scripts/validate_synthetic.py`
  -> 8/8 checks (path reconstruction, edges, endpoint detection + ranking,
  amount-at-endpoint, peel + hop_velocity patterns, explainable score). Uses a
  FakeProvider feeding `data/synthetic/transfers.json` through the real pipeline.
- Standalone graph render test: dump `_TEMPLATE` with real `_build_payload` data
  to a scratch `gtest/index.html`, serve with `python -m http.server`, open it —
  bypasses Streamlit entirely to isolate graph bugs. (`gtest/` is scratch.)

## KNOWN LIMITATIONS
- No Tron entity labels shipped -> Tron traces produce behavioural findings but
  0 VERIFIED endpoint leads until a TronScan label export is added.
- Free Etherscan V2 key excludes BSC.
- Convergence detection needs depth >= 3 to fire on wide fan-out hubs.
- Deep traces on high-fan-out wallets are slow (sequential rate-limited API
  calls); the time budget bounds it but the graph then shows only ~depth 2-3.
- force-graph loads from CDN -> needs internet at demo time (graceful "engine
  failed to load" message otherwise).
- Peel detection is a dominant-forward heuristic, not laundering proof.

## KEY DECISIONS
- async httpx + tenacity for providers; NetworkX for graph ops; Pydantic
  everywhere; SQLite for MVP; explainable scoring with component breakdown.
- Streamlit stays for v1 (custom HTML/JS component carries the "wow"; a
  framework switch days before SIH is not worth the plumbing risk). FastAPI +
  React SPA built in parallel as a separate read-only layer, `src/` unchanged.
- Graph: force-graph 2D canvas (has built-in directional particles + dagMode),
  not a hand-rolled physics engine, not a new heavyweight lib.

## TEST WALLETS
- Synthetic seed (ETH, has ground truth): `0x742d35Cc6634C0532925a3b8D4C0532925a3b8D4`
- ETH Binance hot wallet: `0x28C6c06298d514Db089934071355E5743bf21d60`
- Tron reported wallet (sends USDT repeatedly to one hub):
  `TJrsxY5goqk6nuXcGDUmtu28Wdma1o41Ur`
- Tron fan-out hub (structuring, 12.5M+ USDT observed):
  `TDqSquXBgUCLYvYC4XZgrprLK589dkhSCf`

---

# ACTION LOG

## 2026-09-07 — Phase 14: fix failing tests + build synthetic validation
- normalizer: added `_format_amount()` so amounts always carry a decimal
  ("1" -> "1.0").
- repositories `save_transfer`: `transfer.model_dump(mode="json")` (was crashing
  "datetime is not JSON serializable").
- scoring `calculate()`: prefer path-analyzer-precomputed components; only derive
  when 0.
- pattern_detector `detect_peel_behavior`: first rewrite (was measuring
  retention / inverted).
- path_analyzer: `total_amount_received` = final transfer into the endpoint (was
  summing every hop -> inflated).
- investigation_service `_detect_patterns`: `pattern_type.value` on str keys ->
  use the key directly.
- trace_service: normalize the seed address at entry; filter provider transfers
  to those originating from the queried address (keeps branch limits meaningful).
- settings: added `peel_forward_ratio_min` (0.7).
- label_matcher `_load_labels`: honour each entry's own `source` field (so
  verified_exchange labels score 0.85 instead of falling back to file stem).
- tests/conftest.py NEW: throwaway DB per test.
- tests/integration/test_investigation_service.py: import GraphEdge; use a real
  `Investigation` instead of a `Mock` (pydantic rejected the Mock).
- tests/unit/test_analysis.py: peel assertions use normalized (lowercase) addrs.
- scripts/validate_synthetic.py NEW: FakeProvider -> real pipeline -> compare vs
  `data/synthetic/demo_case.json`. Result: 37 tests pass, 8/8 synthetic checks.

## 2026-09-07 — environment fixes
- Two Python installs: `streamlit` on PATH = Python 3.11 (no plotly). All deps
  are under Python 3.14. Fix: `python -m pip install -r requirements.txt` on
  3.14; run with `python -m streamlit run ...`.
- `app/streamlit_app.py`: prepend project root to `sys.path` (top of file) so
  `from src...` works regardless of launch cwd. `graph_view` import wrapped in a
  `try: from graph_view / except: from app.graph_view` fallback.

## 2026-09-07 — Etherscan adapter + BNB Chain
- `get_token_transfers`: stop sending an empty `contractaddress` param
  (Etherscan V2 -> "Invalid contract address format"); only send when filtering.
- `_make_request`: treat status "0" + "No transactions found" as an empty result
  instead of raising.
- `Chain.is_evm` + `Chain.native_symbol` properties added; swept every
  `chain == Chain.ETHEREUM` address-lowercasing check -> `chain.is_evm` across
  models, repositories, normalizer, label_matcher, mixer_bridge_detector,
  trace_service, path_analyzer.
- `EtherscanProvider`: `chain` / `base_url` / `use_v2` args; `_CHAIN_IDS` map;
  V2 `chainid` param only sent when `use_v2`. Passes `chain=self._chain` to the
  normalizer; native token symbol now chain-aware.
- `_get_provider`: BSC + `BSCSCAN_API_KEY` -> BscScan V1
  (`https://api.bscscan.com/api`, `use_v2=False`); other EVM chains -> Etherscan
  V2. UI dropdown: ETHEREUM / BNB CHAIN / TRON.
- Settings: `bscscan_api_key`, `SUPPORTED_CHAINS=ethereum,bsc,tron`.
  `.env.example` updated. LIMITATION: free Etherscan V2 key -> BSC returns
  "Free API access is not supported for this chain" (needs a BscScan key or a
  paid plan). Fails gracefully.

## 2026-09-07 — graph render deps + node cap
- Installed `scipy` (nx `spring_layout` needs it for >=500-node graphs). Added
  to requirements.txt.
- Node-cap pruning so a 500-node hairball doesn't render as an unreadable/slow
  blob (later superseded by the constellation's `MAX_RENDER_NODES`).

## 2026-09-07/08 — Etherscan live validation + UI crash fixes
- Live-verified Etherscan V2 against a Binance hot wallet (ETH/USDT/UNI
  transfers fetch + normalize).
- `render_main_graph`: `GraphNode` has no `is_highlighted` (only `GraphEdge`
  does) -> node border highlight derived from `highlighted_path`. Was crashing
  on TRACE FUNDS.
- plotly selection points come back as dicts -> `point.get("curve_number", ...)`
  not `point.point_index`. Was crashing on node click.

## 2026-09-08 — TronGrid adapter fixes (Tron traces returned 0 transactions)
- Pagination: was sending `fingerprint=(page-1)*offset` (a number) -> TronGrid
  400. TronGrid uses an opaque `meta.fingerprint` token; page 1 omits it,
  `get_all_outgoing_transfers` threads `self._last_fingerprint`.
- Native TRX direction: compared hex `owner_address` (41...) to base58 seed
  (T...) -> always INCOMING -> filtered out. Added `_hex_to_b58()` (stdlib
  hashlib base58check); native from/to now converted to base58.
- `normalizer.normalize_tron_transfer`: stop `.lower()`-ing Tron base58 (case
  sensitive!) -> was corrupting node keys so the seed never matched its edges.
- TRC20 direction: `.lower()` on both sides -> plain `==` (base58).
- `get_token_transfers`: pass `contract_address` param only when given (was
  computing a var and never using it).

## 2026-09-08 — trace robustness
- `trace_time_budget_seconds` (120): BFS breaks the queue loop at the deadline
  with a "results are partial" warning.
- `per_address_fetch_timeout_seconds` (40): `asyncio.wait_for` around each
  address's provider fetch (Etherscan/TronGrid can stack 30s socket timeouts x
  tenacity retries on 6 paginated calls and hang a depth-1 trace for minutes).

## 2026-09-08 — BFS correctness: edges vs expansion + cache
- Symptom: a wallet with 32 outgoing USDT txns (all to one address over a year)
  rendered as ONE edge showing only the latest amount.
- `_bfs_trace` was skipping any transfer whose recipient was already
  `visited` -> collapsed all repeat transfers into a single edge. Fixed:
  `visited` gates only queue expansion; every transfer becomes an edge.
  `_process_transfer(..., enqueue: bool)` added.
- Branch limit now counts DISTINCT NEW wallets expanded (default 25), not raw
  transfers.
- Removed the persistent-cache read in `_get_outgoing_transfers`: "some rows
  exist" was treated as "have everything", so a partial/interrupted earlier
  trace permanently shadowed the live API (traces returned in 0.4s from stale
  cache). Now always fetches live; the `transactions` table is write-only audit.

## 2026-09-08 — detection substance pass
- `detect_hop_velocity` rewritten (see PATTERN DETECTION above) — real
  receive->forward latency; test updated (now needs an inflow).
- `detect_peel_behavior` tightened (see PATTERN DETECTION above) — key renamed
  `forward_ratio` -> `value_retained`.
- Scam-token filter: `settings.max_plausible_transfer_amount` (1e12 -> later
  1e9). Drops transfers with amount 0 or >= cap in `_get_outgoing_transfers`
  with a warning (one trace summed 1.15e71 "TRX" from a uint256-max airdrop).
- NEW `src/analysis/case_summary.py` (see CASE SUMMARY above). Wired into
  InvestigationResult / investigation_service / report_service / streamlit UI.
- `LabelMatcher._load_labels` scans `data/labels/` + `data/sanctions/` +
  `data/entities/` (was labels/ only, so `verified_exchanges.json` and
  `ofac_sdn.json` were dead files). Tron base58 kept case-sensitive. Label cache
  2 -> 10.
- `data/sanctions/ofac_sdn.json` expanded (Tornado Cash + Lazarus ETH,
  confidence 1.0). `data/entities/README.md` NEW (schema, confidence tiers,
  where to pull real Tron/ETH labels).
- Verified: `TDqSquXBgU...` depth 3 -> "Seed moved 30,353 USDT into 447 wallets;
  dispersed in bursts (layering); re-converge at TDii6vao7x... (20 traced
  wallets) — strongest lead [0.75, unverified]". Synthetic case (labels present)
  -> "Path reaches exchange: Binance Hot Wallet", lead 0.70 VERIFIED.

## 2026-09-08 — "Fund Flow Constellation" graph (app/graph_view.py)
- Replaced the plotly graph. force-graph 2D canvas via CDN, glowing stars,
  twinkling starfield, directional particles, hover tooltip, click panel,
  legend, "reset view".
- `_build_payload` prunes to `MAX_RENDER_NODES = 110`.
- BLANK-CANVAS BUG (cost a lot of time): `Graph.graphData(DATA)` had been moved
  AFTER `Graph.d3Force('charge').strength(...)` -> forces don't exist yet ->
  throws -> IIFE aborts before the render loop -> 100% transparent canvas.
  FIXED: graphData first, d3Force tweaks in try/catch. Also `window.onerror`
  reporter added. Also learned Streamlit doesn't hot-reload `graph_view.py` ->
  must fully restart the server (many "still blank" reports were stale servers).
- Deleted from `streamlit_app.py`: `build_plotly_graph`, `_prune_graph_for_display`,
  `_layered_layout`, `MAX_GRAPH_NODES`, `NON_NODE_TRACES`, plotly/networkx
  imports. `render_selected_node` kept but now dead (star graph has its own
  panel).
- NEW `render_transactions_table(result)` — every transfer as a row (Date /
  Depth / From / To / Amount / Token / Tx Hash / On Path) with token +
  min-amount + address-substring filters, newest first.
- User-requested tweaks: slower particle flow tied to hop velocity; removed the
  4-point diffraction "plus" spikes on hero stars.
- Flow layout: `dagMode('lr')` — left->right by hop; arrowheads; "flow view"
  toggle. Flow readout: `fromN`/`toN`/`fromEx`/`toEx` in hover + click panel.
- Fullscreen button + `st.toggle("Expand graph")` (height 660 -> 1000).
- Verified via standalone render: seed at left, hop-1 column, hop-2 stack,
  arrows, flow-view toggle, panel flow lines all working. 37 tests pass.

## 2026-09-08 — FastAPI + React SPA (parallel tool) — Phase 15 (parts)
- `api/main.py` — FastAPI: `POST /api/trace`, `GET /api/report/{id}?format=text|json`,
  `POST /api/highlight`. CORS for localhost:5173. In-memory last-N result cache.
  Invalid address / missing tx -> 400 with message; empty result -> full shape
  with empty arrays.
- `api/payload.py` — copied `_build_payload` + `_TYPE_COLOR` from
  `app/graph_view.py` (no streamlit import). `build_payload` public alias +
  flatteners: `stats_from_result`, `endpoints_from_result`,
  `patterns_from_result`, `warnings_from_result`, `transactions_from_result`.
- `web/` — Vite + React + TS SPA: header, InputBar (address/chain/depth
  1-6/token filter/TRACE FUNDS, elapsed seconds while in flight), StatsRow,
  Fund Flow Constellation (ported from `_TEMPLATE`, force-graph via CDN in
  index.html), EndpointsTable, TransactionsTable, ReportPanel
  (generate/download/copy). Dark cyber-forensics aesthetic.
- Vite proxies `/api` -> `http://localhost:8000`.
- Run: `uvicorn api.main:app --reload --port 8000` + `npm run dev` in `web/`,
  open localhost:5173.
- Do NOT modify `app/streamlit_app.py` or `src/`.
