from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from ..enums import Chain


class Transaction(BaseModel):
    tx_hash: str = Field(..., description="Transaction hash")
    chain: Chain
    block_number: int
    timestamp: datetime
    from_address: str
    to_address: Optional[str] = Field(
        default=None, description="To address (None for contract creation)"
    )
    value: str = Field(default="0", description="Native value transferred")
    gas_used: Optional[int] = Field(default=None)
    gas_price: Optional[str] = Field(default=None)
    input_data: Optional[str] = Field(default=None)
    nonce: Optional[int] = Field(default=None)
    transaction_index: Optional[int] = Field(default=None)
    status: Optional[int] = Field(default=None, description="1 = success, 0 = failed")
    source: str = Field(default="api")

    @property
    def is_success(self) -> bool:
        return self.status == 1

    def normalized_from(self) -> str:
        if self.chain.is_evm:
            return self.from_address.lower()
        return self.from_address

    def normalized_to(self) -> Optional[str]:
        if self.to_address and self.chain.is_evm:
            return self.to_address.lower()
        return self.to_address
