import json
from pathlib import Path

SYNTHETIC_CASE = {
    "description": "Synthetic demo case: Multi-hop transfer to exchange with partial splits",
    "seed_address": "0x742d35Cc6634C0532925a3b8D4C0532925a3b8D4",
    "chain": "ethereum",
    "expected_path": [
        "0x742d35Cc6634C0532925a3b8D4C0532925a3b8D4",  # Seed (victim)
        "0x8ba1f109551bD432803012645Hac136c772c3b45",  # Wallet A
        "0x9c12f109551bD432803012645Hac136c772c3b45",  # Wallet B
        "0x1a2b3c4d5e6f78901a2b3c4d5e6f78901a2b3c4d",  # Wallet C
        "0x28C6c06298d514Db089934071355E5743bf21d60",  # Binance Hot Wallet (known exchange)
    ],
    "expected_amounts": ["10000.0", "9800.0", "9600.0", "9500.0", "9450.0"],
    "expected_tokens": ["USDT", "USDT", "USDT", "USDT", "USDT"],
    "suspicious_patterns": [
        {
            "address": "0x8ba1f109551bD432803012645Hac136c772c3b45",
            "pattern": "peel_behavior",
        },
        {
            "address": "0x9c12f109551bD432803012645Hac136c772c3b45",
            "pattern": "hop_velocity",
        },
    ],
    "expected_endpoint": {
        "address": "0x28C6c06298d514Db089934071355E5743bf21d60",
        "label": "Binance",
        "entity_type": "exchange",
        "confidence": 0.85,
    },
}

TRANSFERS = [
    {
        "transaction_hash": "0xseed_a",
        "from_address": "0x742d35Cc6634C0532925a3b8D4C0532925a3b8D4",
        "to_address": "0x8ba1f109551bD432803012645Hac136c772c3b45",
        "token_symbol": "USDT",
        "token_contract": "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "amount": "10000.0",
        "amount_raw": "10000000000",
        "decimals": 6,
        "timestamp": "2024-01-15T10:00:00",
        "block_number": 18500000,
        "direction": "outgoing",
    },
    {
        "transaction_hash": "0xa_b",
        "from_address": "0x8ba1f109551bD432803012645Hac136c772c3b45",
        "to_address": "0x9c12f109551bD432803012645Hac136c772c3b45",
        "token_symbol": "USDT",
        "token_contract": "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "amount": "9800.0",
        "amount_raw": "9800000000",
        "decimals": 6,
        "timestamp": "2024-01-15T10:03:00",
        "block_number": 18500010,
        "direction": "outgoing",
    },
    {
        "transaction_hash": "0xa_split",
        "from_address": "0x8ba1f109551bD432803012645Hac136c772c3b45",
        "to_address": "0xsplit1",
        "token_symbol": "USDT",
        "token_contract": "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "amount": "200.0",
        "amount_raw": "200000000",
        "decimals": 6,
        "timestamp": "2024-01-15T10:03:00",
        "block_number": 18500010,
        "direction": "outgoing",
    },
    {
        "transaction_hash": "0xb_c",
        "from_address": "0x9c12f109551bD432803012645Hac136c772c3b45",
        "to_address": "0x1a2b3c4d5e6f78901a2b3c4d5e6f78901a2b3c4d",
        "token_symbol": "USDT",
        "token_contract": "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "amount": "9600.0",
        "amount_raw": "9600000000",
        "decimals": 6,
        "timestamp": "2024-01-15T10:06:00",
        "block_number": 18500020,
        "direction": "outgoing",
    },
    {
        "transaction_hash": "0xc_exchange",
        "from_address": "0x1a2b3c4d5e6f78901a2b3c4d5e6f78901a2b3c4d",
        "to_address": "0x28C6c06298d514Db089934071355E5743bf21d60",
        "token_symbol": "USDT",
        "token_contract": "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "amount": "9500.0",
        "amount_raw": "9500000000",
        "decimals": 6,
        "timestamp": "2024-01-15T10:09:00",
        "block_number": 18500030,
        "direction": "outgoing",
    },
]

LABELS = [
    {
        "address": "0x28C6c06298d514Db089934071355E5743bf21d60",
        "chain": "ethereum",
        "label": "Binance Hot Wallet",
        "category": "exchange",
        "source": "verified_exchange",
        "confidence": 0.85,
    },
    {
        "address": "0x742d35Cc6634C0532925a3b8D4C0532925a3b8D4",
        "chain": "ethereum",
        "label": "Victim Reported Wallet",
        "category": "victim_seed",
        "source": "synthetic",
        "confidence": 1.0,
    },
]

SANCTIONS = [
    {
        "address": "0x722122df12d4e14e13ac3b6895a86e84145b6967",
        "chain": "ethereum",
        "label": "Tornado Cash",
        "category": "sanctioned",
        "source": "ofac_sdn",
        "confidence": 1.0,
    }
]

EXCHANGES = [
    {
        "address": "0x28C6c06298d514Db089934071355E5743bf21d60",
        "chain": "ethereum",
        "label": "Binance",
        "category": "exchange",
        "source": "verified_exchange",
        "confidence": 0.85,
    },
    {
        "address": "0x21a31Ee1afC51d94C2eFcAAa2092aD1028285549",
        "chain": "ethereum",
        "label": "Binance 2",
        "category": "exchange",
        "source": "verified_exchange",
        "confidence": 0.85,
    },
    {
        "address": "0x3f5CE5FBFe3E9af3971dD833D26bA9b5C936f0bE",
        "chain": "ethereum",
        "label": "Binance 3",
        "category": "exchange",
        "source": "verified_exchange",
        "confidence": 0.85,
    },
]


def write_synthetic_data():
    base = Path(__file__).parent.parent / "data" / "synthetic"
    base.mkdir(parents=True, exist_ok=True)

    with open(base / "demo_case.json", "w") as f:
        json.dump(SYNTHETIC_CASE, f, indent=2)

    with open(base / "transfers.json", "w") as f:
        json.dump(TRANSFERS, f, indent=2)

    labels_dir = Path(__file__).parent.parent / "data" / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    with open(labels_dir / "synthetic_labels.json", "w") as f:
        json.dump(LABELS, f, indent=2)

    sanctions_dir = Path(__file__).parent.parent / "data" / "sanctions"
    sanctions_dir.mkdir(parents=True, exist_ok=True)
    with open(sanctions_dir / "synthetic_sanctions.json", "w") as f:
        json.dump(SANCTIONS, f, indent=2)

    entities_dir = Path(__file__).parent.parent / "data" / "entities"
    entities_dir.mkdir(parents=True, exist_ok=True)
    with open(entities_dir / "verified_exchanges.json", "w") as f:
        json.dump(EXCHANGES, f, indent=2)

    print("Synthetic data written successfully!")


if __name__ == "__main__":
    write_synthetic_data()
