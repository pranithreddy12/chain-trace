# ChainTrace - Blockchain Forensics & Cryptocurrency Fund-Flow Investigation Platform

A read-only blockchain forensics application for Smart India Hackathon that reconstructs observable fund flows through blockchain transactions, identifies known entities/exchanges, detects suspicious patterns, and presents investigations as interactive cyber-forensics graphs.

## What ChainTrace Is

ChainTrace takes a reported cryptocurrency wallet address and automates the first-pass fund-flow investigation by:
- Retrieving public blockchain transactions
- Normalizing and caching transaction data
- Building a transaction graph with BFS traversal
- Matching addresses against entity/sanctions intelligence
- Detecting suspicious movement patterns (fan-out, hop velocity, micro-fan-out, peel behavior)
- Calculating explainable confidence scores for candidate endpoints
- Presenting results in an interactive cyber-forensics visualization

## Problem Statement

When a fraud victim reports a cryptocurrency wallet address, investigators need to determine:
- Where funds moved
- Which wallets were involved
- How many hops occurred
- Whether suspicious patterns exist
- Whether funds reached known exchanges/entities
- Whether sanctioned entities were encountered
- How much of the original amount reached each endpoint
- How quickly funds moved
- Which path is the strongest investigative lead

ChainTrace automates this first-pass investigation, producing an **investigative lead** - not a criminal verdict, identity attribution, or automatic fund recovery tool.

## Architecture

```
src/
├── domain/           # Core domain models (Address, Transfer, Transaction, Investigation, Graph)
├── application/      # Application services (TraceService, InvestigationService, ReportService)
├── blockchain/       # Blockchain adapters (Etherscan, TronGrid, Base interface, Normalizer)
├── intelligence/     # Entity intelligence (LabelMatcher, ExchangeInference, MixerBridgeDetector)
├── analysis/         # Pattern detection & scoring (PatternDetector, PathAnalyzer, Scoring)
├── graph/            # Graph construction & visualization (Builder, Traversal, Visualization)
├── persistence/      # Database & repositories (SQLite, TransactionRepository, CacheRepository)
├── reports/          # Investigative summary generation
└── config/           # Configuration management

app/
└── streamlit_app.py  # Streamlit web application
```

## Supported Chains

**Primary:**
- Ethereum Mainnet (via Etherscan API V2)

**Secondary:**
- Tron / USDT-TRC20 (via TronGrid)

**Planned:**
- BSC, Polygon, Base, Arbitrum

**Not in v1:**
- Bitcoin (UTXO model)
- Cross-chain tracing through bridges/mixers

## Data Sources

- **Etherscan API V2** - Ethereum normal transactions & ERC-20 transfers
- **TronGrid API** - Tron transactions & TRC-20 transfers
- **Local Intelligence** - eth-labels, etherscan-labels, OFAC SDN crypto addresses, verified exchange addresses

## Setup

1. Clone the repository
2. Create virtual environment: `python -m venv venv`
3. Activate: `venv\Scripts\activate` (Windows) or `source venv/bin/activate` (Linux/Mac)
4. Install dependencies: `pip install -r requirements.txt`
5. Copy `.env.example` to `.env` and add your API keys
6. Run the application: `streamlit run app/streamlit_app.py`

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| ETHERSCAN_API_KEY | Etherscan API key for Ethereum data | Yes (for Ethereum) |
| TRONGRID_API_KEY | TronGrid API key for Tron data | Yes (for Tron) |
| DEFAULT_TRACE_DEPTH | Maximum BFS trace depth (default: 4) | No |
| MAX_BRANCHES | Maximum branches per node (default: 25) | No |
| MIXER_SCORE_CAP | Confidence cap for mixer/bridge paths (default: 0.40) | No |
| DATABASE_PATH | SQLite database path (default: ./data/chain_trace.db) | No |

## How to Run

```bash
# Install dependencies
pip install -r requirements.txt

# Run the Streamlit app
streamlit run app/streamlit_app.py
```

## How to Run Tests

```bash
# Run all tests
pytest

# Run unit tests only
pytest tests/unit/

# Run integration tests only
pytest tests/integration/

# Run with coverage
pytest --cov=src --cov-report=html
```

## Scoring

ChainTrace calculates an explainable confidence score (0-1) for each candidate endpoint:

```
score = 0.30 × path_directness + 0.35 × amount_concentration + 0.35 × label_confidence
```

Where:
- **path_directness** = 1 / (1 + unlabeled_hops)
- **amount_concentration** = amount_reaching_endpoint / original_seed_amount
- **label_confidence**: OFAC/direct match = 1.0, verified exchange = 0.85, sweep inference = 0.60, curated = 0.50

**Important:** This is an investigative confidence score, NOT probability of guilt, criminal ownership, legal evidence, or identity confidence.

Paths crossing unresolved mixers/bridges receive a confidence cap (default 0.40).

## Known Limitations

- Read-only: never signs, sends, or moves funds
- No private key/seed phrase handling
- No exchange account access or automated freezing
- No unauthorized identity attribution
- Mixer/bridge paths are flagged but not resolved cross-chain
- Exchange inference requires connection to known infrastructure
- Unknown addresses remain unknown - no silent classification
- Single heuristics never constitute proof

## Safety / Read-Only Boundary

ChainTrace is strictly read-only. It:
- Only uses public blockchain data and locally stored public intelligence
- Never modifies blockchain state
- Never interacts with exchange accounts
- Never requests private exchange information
- Provides investigative leads only

**Disclaimer:** ChainTrace provides public-data-based investigative leads and does not constitute a legal determination or definitive attribution of ownership.

## Project Structure

```
ChainTrace/
├── app/
│   └── streamlit_app.py
├── src/
│   ├── domain/
│   ├── application/
│   ├── blockchain/
│   ├── intelligence/
│   ├── analysis/
│   ├── graph/
│   ├── persistence/
│   ├── reports/
│   └── config/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── data/
│   ├── labels/
│   ├── sanctions/
│   ├── entities/
│   └── synthetic/
├── scripts/
├── MEMORY.md
├── README.md
├── requirements.txt
├── .env.example
└── .gitignore
```

## Validation Methodology

- Unit tests with mocked providers (no network required)
- Integration tests with mocked API responses
- Real data validation using public sanctioned addresses and known exchange addresses
- Synthetic demo case with known ground truth
- Validation reports comparing expected vs actual paths, amounts, endpoints, patterns, and scores