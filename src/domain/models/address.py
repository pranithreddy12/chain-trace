from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

from ..enums import Chain, EntityCategory, LabelSource


class Address(BaseModel):
    address: str = Field(
        ..., description="Blockchain address (checksummed for Ethereum)"
    )
    chain: Chain
    label: Optional[str] = Field(default=None, description="Human-readable label")
    entity_type: EntityCategory = Field(default=EntityCategory.UNKNOWN)
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Label confidence score"
    )
    source: Optional[LabelSource] = Field(
        default=None, description="Source of the label"
    )
    metadata: dict = Field(default_factory=dict, description="Additional metadata")
    first_seen: Optional[datetime] = Field(default=None)
    last_seen: Optional[datetime] = Field(default=None)

    def normalize(self) -> "Address":
        """Return a normalized copy of this address."""
        normalized = self.model_copy()
        if self.chain.is_evm:
            normalized.address = self.address.lower()
        return normalized

    @property
    def is_labeled(self) -> bool:
        return self.label is not None and self.entity_type != EntityCategory.UNKNOWN

    @property
    def display_name(self) -> str:
        if self.label:
            return f"{self.label} ({self.address[:8]}...{self.address[-6:]})"
        return f"{self.address[:8]}...{self.address[-6:]}"
