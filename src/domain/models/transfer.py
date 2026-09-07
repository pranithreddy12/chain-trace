from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

from ..enums import Chain, TransferDirection, EdgeType


class Transfer(BaseModel):
    transaction_hash: str = Field(..., description="Transaction hash")
    chain: Chain
    from_address: str = Field(..., description="Sender address")
    to_address: str = Field(..., description="Recipient address")
    token_symbol: Optional[str] = Field(
        default=None, description="Token symbol (e.g., USDT, ETH)"
    )
    token_contract: Optional[str] = Field(
        default=None, description="Token contract address"
    )
    amount: str = Field(..., description="Amount as string to preserve precision")
    amount_raw: Optional[str] = Field(
        default=None, description="Raw amount in smallest unit (wei/satoshi)"
    )
    decimals: Optional[int] = Field(default=None, description="Token decimals")
    timestamp: datetime = Field(..., description="Block timestamp")
    block_number: int = Field(..., description="Block number")
    direction: TransferDirection = Field(..., description="Direction relative to seed")
    edge_type: EdgeType = Field(default=EdgeType.TOKEN_TRANSFER)
    source: str = Field(
        default="api", description="Data source (api, cache, synthetic)"
    )

    @property
    def is_native(self) -> bool:
        return self.token_contract is None or self.token_contract == ""

    @property
    def amount_float(self) -> float:
        try:
            return float(self.amount)
        except (ValueError, TypeError):
            return 0.0

    def normalized_from(self) -> str:
        if self.chain.is_evm:
            return self.from_address.lower()
        return self.from_address

    def normalized_to(self) -> str:
        if self.chain.is_evm:
            return self.to_address.lower()
        return self.to_address
