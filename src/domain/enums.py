from enum import Enum


class Chain(str, Enum):
    ETHEREUM = "ethereum"
    TRON = "tron"
    BSC = "bsc"
    POLYGON = "polygon"
    BASE = "base"
    ARBITRUM = "arbitrum"

    @property
    def is_evm(self) -> bool:
        """EVM chains use 0x hex addresses that are safe to lowercase-normalize."""
        return self in (
            Chain.ETHEREUM,
            Chain.BSC,
            Chain.POLYGON,
            Chain.BASE,
            Chain.ARBITRUM,
        )

    @property
    def native_symbol(self) -> str:
        return {
            Chain.ETHEREUM: "ETH",
            Chain.BSC: "BNB",
            Chain.POLYGON: "POL",
            Chain.BASE: "ETH",
            Chain.ARBITRUM: "ETH",
            Chain.TRON: "TRX",
        }.get(self, "ETH")


class EntityCategory(str, Enum):
    VICTIM_SEED = "victim_seed"
    INTERMEDIATE = "intermediate"
    EXCHANGE = "exchange"
    SANCTIONED = "sanctioned"
    MIXER = "mixer"
    BRIDGE = "bridge"
    CONTRACT = "contract"
    UNKNOWN = "unknown"
    OBFUSCATION_POINT = "obfuscation_point"


class NodeType(str, Enum):
    SEED = "seed"
    INTERMEDIATE_WALLET = "intermediate_wallet"
    SUSPICIOUS_WALLET = "suspicious_wallet"
    EXCHANGE = "exchange"
    SANCTIONED_ADDRESS = "sanctioned_address"
    MIXER = "mixer"
    BRIDGE = "bridge"
    CONTRACT_SERVICE = "contract_service"
    UNKNOWN = "unknown"


class EdgeType(str, Enum):
    NATIVE_TRANSFER = "native_transfer"
    TOKEN_TRANSFER = "token_transfer"
    CONTRACT_INTERACTION = "contract_interaction"


class TransferDirection(str, Enum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"


class InvestigationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class LabelSource(str, Enum):
    ETHERSCAN_LABELS = "etherscan_labels"
    ETH_LABELS = "eth_labels"
    OFAC_SDN = "ofac_sdn"
    VERIFIED_EXCHANGE = "verified_exchange"
    SWEEP_INFERENCE = "sweep_inference"
    CURATED = "curated"
    SYNTHETIC = "synthetic"


class PatternType(str, Enum):
    FAN_OUT = "fan_out"
    HOP_VELOCITY = "hop_velocity"
    MICRO_FAN_OUT = "micro_fan_out"
    RAPID_MULTI_HOP = "rapid_multi_hop"
    PEEL_BEHAVIOR = "peel_behavior"


class ProviderErrorType(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    INVALID_ADDRESS = "invalid_address"
    EMPTY_RESPONSE = "empty_response"
    MALFORMED_RESPONSE = "malformed_response"
    API_QUOTA = "api_quota"
    PROVIDER_ERROR = "provider_error"
    NETWORK_ERROR = "network_error"
