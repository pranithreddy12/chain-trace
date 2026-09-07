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
        eth_mixers = {
            "0x722122df12d4e14e13ac3b6895a86e84145b6967",  # Tornado Cash
            "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf",  # Tornado Cash
            "0xa6e8772af29b29b98d071709743748d87d84d1b2",  # Tornado Cash
        }
        self.mixer_contracts[Chain.ETHEREUM] = {a.lower() for a in eth_mixers}

        tron_mixers = set()
        self.mixer_contracts[Chain.TRON] = tron_mixers

        eth_bridges = {
            "0x3ee18b2214aff97000d974cf647e7c347e8fa585",  # Wormhole
            "0x83e45b4e8d4b7b9d0c5a7b7f7e8d9c0a1b2c3d4e",  # Multichain
        }
        self.bridge_contracts[Chain.ETHEREUM] = {a.lower() for a in eth_bridges}

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
            if node.address.entity_type == EntityCategory.MIXER:
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
