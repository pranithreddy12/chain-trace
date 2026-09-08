from typing import Optional, Dict, List
from pathlib import Path
import json

from ..domain.models.address import Address
from ..domain.enums import Chain, EntityCategory, LabelSource
from ..persistence.repositories import AddressRepository
from ..config.settings import get_settings


class LabelMatcher:
    def __init__(self):
        self.settings = get_settings()
        self.address_repo = AddressRepository()
        self._labels_cache: Dict[str, Dict] = {}
        self._load_labels()

    def _load_labels(self) -> None:
        # Scan every intel directory so anything the team drops in (a TronScan
        # label export, the OFAC SDN crypto list, curated exchange wallets) is
        # picked up without code changes.
        dirs = [
            self.settings.labels_dir_obj,
            self.settings.sanctions_dir_obj,
            self.settings.entities_dir_obj,
        ]
        for d in dirs:
            if not d.exists():
                continue
            for file_path in d.glob("*.json"):
                try:
                    with open(file_path, "r") as f:
                        data = json.load(f)
                    default_source = file_path.stem
                    for entry in data:
                        raw = entry.get("address", "")
                        chain = entry.get("chain", "ethereum")
                        # EVM addresses normalize to lowercase; Tron base58 does not
                        addr = raw.lower() if chain != "tron" else raw
                        self._labels_cache[f"{chain}:{addr}"] = {
                            "label": entry.get("label"),
                            "category": entry.get("category", "unknown"),
                            "source": entry.get("source", default_source),
                            "confidence": entry.get("confidence", 0.5),
                        }
                except Exception:
                    pass

    async def match(self, address: Address) -> Optional[Address]:
        normalized = (
            address.address.lower()
            if address.chain.is_evm
            else address.address
        )
        key = f"{address.chain.value}:{normalized}"

        if key in self._labels_cache:
            label_data = self._labels_cache[key]
            return Address(
                address=address.address,
                chain=address.chain,
                label=label_data["label"],
                entity_type=EntityCategory(label_data["category"]),
                confidence=label_data["confidence"],
                source=LabelSource(label_data["source"])
                if label_data["source"] in LabelSource.__members__.values()
                else LabelSource.CURATED,
                metadata=address.metadata,
            )

        stored = self.address_repo.get_address(address.address, address.chain)
        if stored and stored.is_labeled:
            return stored

        return None

    def load_sanctions(self, file_path: Path) -> int:
        count = 0
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
                for entry in data:
                    addr = entry.get("address", "").lower()
                    chain = entry.get("chain", "ethereum")
                    key = f"{chain}:{addr}"
                    self._labels_cache[key] = {
                        "label": entry.get("label", "OFAC Sanctioned"),
                        "category": "sanctioned",
                        "source": "ofac_sdn",
                        "confidence": 1.0,
                    }
                    count += 1
        except Exception:
            pass
        return count

    def load_exchange_addresses(self, file_path: Path) -> int:
        count = 0
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
                for entry in data:
                    addr = entry.get("address", "").lower()
                    chain = entry.get("chain", "ethereum")
                    key = f"{chain}:{addr}"
                    self._labels_cache[key] = {
                        "label": entry.get("label", "Exchange"),
                        "category": "exchange",
                        "source": "verified_exchange",
                        "confidence": 0.85,
                    }
                    count += 1
        except Exception:
            pass
        return count

    def all_entries(self) -> List[Dict]:
        """Every loaded label as {address, chain, label, category, source, confidence}."""
        out = []
        for key, data in self._labels_cache.items():
            chain, _, addr = key.partition(":")
            out.append({"address": addr, "chain": chain, **data})
        return out

    def get_all_labeled_addresses(self, chain: Chain) -> List[Dict]:
        results = []
        prefix = f"{chain.value}:"
        for key, data in self._labels_cache.items():
            if key.startswith(prefix):
                results.append({"address": key[len(prefix) :], **data})
        return results
