from typing import Set, List, Dict
from pathlib import Path
import json

from ..domain.models.graph import TransactionGraph
from ..domain.enums import Chain, EntityCategory
from ..config.settings import get_settings


class MixerBridgeDetector:
    def __init__(self):
        self.settings = get_settings()
        self.mixer_contracts: Dict[Chain, Set[str]] = {chain: set() for chain in Chain}
        self.bridge_contracts: Dict[Chain, Set[str]] = {chain: set() for chain in Chain}
        self._load_known_contracts()

    def _load_known_contracts(self) -> None:
        """Mixer/bridge contracts come from the entity datasets, not code.

        Anything in data/labels|sanctions|entities with category `mixer` or
        `bridge` is picked up here, so coverage is multichain and extendable
        without a code change (see data/entities/README.md).
        """
        from .label_matcher import LabelMatcher

        for entry in LabelMatcher().all_entries():
            try:
                chain = Chain(entry.get("chain", "ethereum"))
            except ValueError:
                continue
            addr = entry.get("address", "")
            addr = addr.lower() if chain.is_evm else addr
            if not addr:
                continue
            if entry.get("category") == "mixer":
                self.mixer_contracts[chain].add(addr)
            elif entry.get("category") == "bridge":
                self.bridge_contracts[chain].add(addr)

    def detect(self, graph: TransactionGraph) -> List[str]:
        mixer_addresses = []

        for edge in graph.edges:
            to_addr = edge.transfer.normalized_to()
            from_addr = edge.transfer.normalized_from()
            token_contract = edge.transfer.token_contract

            if token_contract and token_contract.lower() in self.mixer_contracts.get(
                edge.transfer.chain, set()
            ):
                if to_addr in graph.nodes:
                    mixer_addresses.append(to_addr)
                if from_addr in graph.nodes:
                    mixer_addresses.append(from_addr)

            if token_contract and token_contract.lower() in self.bridge_contracts.get(
                edge.transfer.chain, set()
            ):
                if to_addr in graph.nodes:
                    mixer_addresses.append(to_addr)
                if from_addr in graph.nodes:
                    mixer_addresses.append(from_addr)

        for addr, node in graph.nodes.items():
            chain = node.address.chain
            key = addr.lower() if chain.is_evm else addr
            if (
                key in self.mixer_contracts.get(chain, set())
                or key in self.bridge_contracts.get(chain, set())
                or node.address.entity_type
                in (EntityCategory.MIXER, EntityCategory.BRIDGE)
            ):
                mixer_addresses.append(addr)

        return list(set(mixer_addresses))

    def is_mixer(self, address: str, chain: Chain) -> bool:
        normalized = address.lower() if chain.is_evm else address
        return normalized in self.mixer_contracts.get(chain, set())

    def is_bridge(self, address: str, chain: Chain) -> bool:
        normalized = address.lower() if chain.is_evm else address
        return normalized in self.bridge_contracts.get(chain, set())

    def load_custom_list(self, file_path: Path, list_type: str) -> int:
        count = 0
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
                for entry in data:
                    addr = entry.get("address", "").lower()
                    chain = Chain(entry.get("chain", "ethereum"))
                    if list_type == "mixer":
                        self.mixer_contracts[chain].add(addr)
                    elif list_type == "bridge":
                        self.bridge_contracts[chain].add(addr)
                    count += 1
        except Exception:
            pass
        return count
