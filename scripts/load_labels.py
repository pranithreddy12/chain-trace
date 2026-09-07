import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json

from src.intelligence.label_matcher import LabelMatcher
from src.config.settings import get_settings


def load_all_labels():
    settings = get_settings()
    matcher = LabelMatcher()

    # Load labels
    labels_loaded = 0
    for label_file in settings.labels_dir_obj.glob("*.json"):
        try:
            with open(label_file, "r") as f:
                data = json.load(f)
                for entry in data:
                    matcher._labels_cache[
                        f"{entry.get('chain', 'ethereum')}:{entry.get('address', '').lower()}"
                    ] = {
                        "label": entry.get("label"),
                        "category": entry.get("category", "unknown"),
                        "source": entry.get("source", "curated"),
                        "confidence": entry.get("confidence", 0.5),
                    }
                    labels_loaded += 1
        except Exception as e:
            print(f"Error loading {label_file}: {e}")

    # Load sanctions
    sanctions_loaded = 0
    for sanction_file in settings.sanctions_dir_obj.glob("*.json"):
        try:
            sanctions_loaded += matcher.load_sanctions(sanction_file)
        except Exception as e:
            print(f"Error loading {sanction_file}: {e}")

    # Load exchanges
    exchanges_loaded = 0
    for exchange_file in settings.entities_dir_obj.glob("*.json"):
        try:
            exchanges_loaded += matcher.load_exchange_addresses(exchange_file)
        except Exception as e:
            print(f"Error loading {exchange_file}: {e}")

    print(
        f"Loaded: {labels_loaded} labels, {sanctions_loaded} sanctions, {exchanges_loaded} exchanges"
    )

    # Verify
    print("\nVerification:")
    test_addresses = [
        ("0x28C6c06298d514Db089934071355E5743bf21d60", "ethereum"),  # Binance
        ("0x742d35Cc6634C0532925a3b8D4C0532925a3b8D4", "ethereum"),  # Victim
        ("0x722122df12d4e14e13ac3b6895a86e84145b6967", "ethereum"),  # Tornado
    ]

    for addr, chain in test_addresses:
        key = f"{chain}:{addr.lower()}"
        if key in matcher._labels_cache:
            data = matcher._labels_cache[key]
            print(
                f"  {addr}: {data['label']} ({data['category']}) - {data['source']} - conf: {data['confidence']}"
            )
        else:
            print(f"  {addr}: NOT FOUND")


if __name__ == "__main__":
    from pathlib import Path

    load_all_labels()
