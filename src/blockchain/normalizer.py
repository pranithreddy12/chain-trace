from typing import List, Optional, Dict, Any
from datetime import datetime
from decimal import Decimal

from ..domain.models.transfer import Transfer
from ..domain.models.transaction import Transaction
from ..domain.models.address import Address
from ..domain.enums import Chain, TransferDirection, EdgeType, EntityCategory


def _format_amount(value: str, decimals: int) -> str:
    amount = format(Decimal(value) / Decimal(10**decimals), "f")
    if "." not in amount:
        amount += ".0"
    return amount


class TransactionNormalizer:
    @staticmethod
    def normalize_ethereum_transfer(
        raw: Dict[str, Any],
        chain: Chain = Chain.ETHEREUM,
        direction: TransferDirection = TransferDirection.OUTGOING,
        source: str = "etherscan",
    ) -> Transfer:
        value = raw.get("value", "0")
        decimals = int(raw.get("tokenDecimal", "18")) if raw.get("tokenDecimal") else 18
        token_contract = (
            raw.get("contractAddress", "").lower()
            if raw.get("contractAddress")
            else None
        )
        token_symbol = (
            raw.get("tokenSymbol", "").upper() if raw.get("tokenSymbol") else None
        )

        if token_contract:
            amount = _format_amount(value, decimals)
        else:
            amount = _format_amount(value, 18)
            token_symbol = chain.native_symbol

        return Transfer(
            transaction_hash=raw.get("hash", "").lower(),
            chain=chain,
            from_address=raw.get("from", "").lower(),
            to_address=raw.get("to", "").lower(),
            token_symbol=token_symbol,
            token_contract=token_contract,
            amount=amount,
            amount_raw=value,
            decimals=decimals,
            timestamp=datetime.fromtimestamp(int(raw.get("timeStamp", 0))),
            block_number=int(raw.get("blockNumber", 0)),
            direction=direction,
            edge_type=EdgeType.TOKEN_TRANSFER
            if token_contract
            else EdgeType.NATIVE_TRANSFER,
            source=source,
        )

    @staticmethod
    def normalize_tron_transfer(
        raw: Dict[str, Any],
        chain: Chain = Chain.TRON,
        direction: TransferDirection = TransferDirection.OUTGOING,
        source: str = "trongrid",
    ) -> Transfer:
        value = raw.get("value", "0")
        decimals = int(raw.get("token_info", {}).get("decimals", 6))
        token_contract = (
            raw.get("token_info", {}).get("address", "").lower()
            if raw.get("token_info", {}).get("address")
            else None
        )
        token_symbol = (
            raw.get("token_info", {}).get("symbol", "").upper()
            if raw.get("token_info", {}).get("symbol")
            else None
        )

        if token_contract:
            amount = _format_amount(value, decimals)
        else:
            amount = _format_amount(value, 6)
            token_symbol = "TRX"

        timestamp = raw.get("block_timestamp", 0)
        if timestamp > 1e12:
            timestamp = timestamp / 1000

        return Transfer(
            transaction_hash=raw.get("transaction_id", "").lower(),
            chain=chain,
            # Tron base58 addresses are case-sensitive — do NOT lowercase them
            from_address=raw.get("from", ""),
            to_address=raw.get("to", ""),
            token_symbol=token_symbol,
            token_contract=token_contract,
            amount=amount,
            amount_raw=value,
            decimals=decimals,
            timestamp=datetime.fromtimestamp(timestamp),
            block_number=int(raw.get("block_number", 0)),
            direction=direction,
            edge_type=EdgeType.TOKEN_TRANSFER
            if token_contract
            else EdgeType.NATIVE_TRANSFER,
            source=source,
        )

    @staticmethod
    def normalize_ethereum_transaction(raw: Dict[str, Any]) -> Transaction:
        return Transaction(
            tx_hash=raw.get("hash", "").lower(),
            chain=Chain.ETHEREUM,
            block_number=int(raw.get("blockNumber", 0)),
            timestamp=datetime.fromtimestamp(int(raw.get("timeStamp", 0))),
            from_address=raw.get("from", "").lower(),
            to_address=raw.get("to", "").lower() if raw.get("to") else None,
            value=raw.get("value", "0"),
            gas_used=int(raw.get("gasUsed", 0)) if raw.get("gasUsed") else None,
            gas_price=raw.get("gasPrice"),
            input_data=raw.get("input"),
            nonce=int(raw.get("nonce", 0)) if raw.get("nonce") else None,
            transaction_index=int(raw.get("transactionIndex", 0))
            if raw.get("transactionIndex")
            else None,
            status=int(raw.get("isError", "1")) == 0,
            source="etherscan",
        )

    @staticmethod
    def normalize_tron_transaction(raw: Dict[str, Any]) -> Transaction:
        timestamp = raw.get("block_timestamp", 0)
        if timestamp > 1e12:
            timestamp = timestamp / 1000

        return Transaction(
            tx_hash=raw.get("txID", "").lower(),
            chain=Chain.TRON,
            block_number=int(raw.get("block_number", 0)),
            timestamp=datetime.fromtimestamp(timestamp),
            from_address=raw.get("owner_address", "").lower(),
            to_address=raw.get("to_address", "").lower()
            if raw.get("to_address")
            else None,
            value=str(raw.get("amount", 0)),
            source="trongrid",
        )

    @staticmethod
    def normalize_address(
        address: str,
        chain: Chain,
        label: Optional[str] = None,
        entity_type: EntityCategory = EntityCategory.UNKNOWN,
        confidence: float = 0.0,
        source: Optional[str] = None,
    ) -> Address:
        normalized = address.lower() if chain.is_evm else address
        return Address(
            address=normalized,
            chain=chain,
            label=label,
            entity_type=entity_type,
            confidence=confidence,
            source=source,
        )
