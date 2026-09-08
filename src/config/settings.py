from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # API Keys
    etherscan_api_key: str = Field(
        default="", description="Etherscan API key for Ethereum data"
    )
    trongrid_api_key: str = Field(
        default="", description="TronGrid API key for Tron data"
    )
    bscscan_api_key: str = Field(
        default="",
        description="BscScan API key (V1) — Etherscan V2 free tier does not cover BSC",
    )

    # Application Settings
    default_trace_depth: int = Field(
        default=4, ge=1, le=10, description="Maximum BFS trace depth"
    )
    max_branches: int = Field(
        default=25, ge=1, le=100, description="Maximum branches per node"
    )
    mixer_score_cap: float = Field(
        default=0.40,
        ge=0.0,
        le=1.0,
        description="Confidence cap for mixer/bridge paths",
    )
    hop_velocity_threshold_seconds: int = Field(
        default=300, ge=1, description="Threshold for hop velocity detection (seconds)"
    )
    micro_fanout_threshold: int = Field(
        default=10, ge=1, description="Threshold for micro-fanout detection"
    )
    trace_time_budget_seconds: int = Field(
        default=120,
        ge=5,
        description="Wall-clock cap on a single BFS trace; returns partial results",
    )
    per_address_fetch_timeout_seconds: int = Field(
        default=40,
        ge=5,
        description="Hard timeout for one address's provider fetch (all pages)",
    )
    dust_amount_threshold: float = Field(
        default=0.001,
        gt=0,
        description="Transfers below this many token units are dust. They are "
        "kept as edges (an address-poisoning spray is evidence of targeting) "
        "but their recipients are never expanded - following thousands of "
        "1-sun sends exhausts the branch limit and the time budget.",
    )
    poisoning_min_dust_sends: int = Field(
        default=15,
        ge=2,
        description="Dust sends from one wallet before address poisoning is "
        "considered",
    )
    max_plausible_transfer_amount: float = Field(
        default=1e9,
        gt=0,
        description="Drop transfers above this many token units (scam/airdrop "
        "tokens with inflated nominal amounts; no legit ETH/USDT transfer "
        "exceeds ~1e9 units)",
    )
    min_endpoint_taint_fraction: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="A terminal wallet must hold at least this share of the "
        "seed's tainted value to count as a candidate endpoint",
    )
    max_candidate_endpoints: int = Field(
        default=25, ge=1, description="Cap on ranked candidate endpoints"
    )
    convergence_min_senders: int = Field(
        default=4,
        ge=2,
        description="Distinct in-graph senders needed to call a wallet a "
        "convergence point on sender count alone",
    )
    convergence_min_senders_with_value: int = Field(
        default=3,
        ge=2,
        description="Lower sender bar when the wallet also holds a material "
        "share of the traced funds (two weak signals corroborate)",
    )
    convergence_material_taint: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Taint share that makes a convergence point material",
    )
    flow_weight: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description="Weight of flow confidence (did the money go here) in the "
        "combined lead score; entity weight is 1 - this",
    )
    peel_forward_ratio_min: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Min outgoing/incoming ratio for peel/pass-through detection",
    )
    supported_chains: str = Field(
        default="ethereum,bsc,tron", description="Comma-separated supported chains"
    )

    # Database
    database_path: str = Field(
        default="./data/chain_trace.db", description="SQLite database path"
    )

    # Data Paths
    labels_dir: str = Field(
        default="./data/labels", description="Labels data directory"
    )
    sanctions_dir: str = Field(
        default="./data/sanctions", description="Sanctions data directory"
    )
    entities_dir: str = Field(
        default="./data/entities", description="Entities data directory"
    )
    synthetic_dir: str = Field(
        default="./data/synthetic", description="Synthetic data directory"
    )

    @property
    def supported_chains_list(self) -> list[str]:
        return [
            c.strip().lower() for c in self.supported_chains.split(",") if c.strip()
        ]

    @property
    def database_path_obj(self) -> Path:
        return Path(self.database_path)

    @property
    def labels_dir_obj(self) -> Path:
        return Path(self.labels_dir)

    @property
    def sanctions_dir_obj(self) -> Path:
        return Path(self.sanctions_dir)

    @property
    def entities_dir_obj(self) -> Path:
        return Path(self.entities_dir)

    @property
    def synthetic_dir_obj(self) -> Path:
        return Path(self.synthetic_dir)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
