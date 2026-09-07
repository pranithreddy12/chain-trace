import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime
from decimal import Decimal

from src.blockchain.normalizer import TransactionNormalizer
from src.domain.models.transfer import Transfer
from src.domain.models.transaction import Transaction
from src.domain.models.address import Address
from src.domain.enums import Chain, TransferDirection, EdgeType, EntityCategory


class TestTransactionNormalizer:
    def test_normalize_ethereum_transfer_erc20(self):
        raw = {
            "hash": "0xabc123",
            "from": "0x1111111111111111111111111111111111111111",
            "to": "0x2222222222222222222222222222222222222222",
            "contractAddress": "0xdac17f958d2ee523a2206206994597c13d831ec7",
            "tokenSymbol": "USDT",
            "tokenDecimal": "6",
            "value": "1000000",
            "timeStamp": "1699999999",
            "blockNumber": "12345678",
        }
        transfer = TransactionNormalizer.normalize_ethereum_transfer(raw)
        assert transfer.chain == Chain.ETHEREUM
        assert transfer.token_symbol == "USDT"
        assert (
            transfer.token_contract
            == "0xdac17f958d2ee523a2206206994597c13d831ec7".lower()
        )
        assert transfer.amount == "1.0"
        assert transfer.amount_raw == "1000000"
        assert transfer.decimals == 6
        assert transfer.edge_type == EdgeType.TOKEN_TRANSFER

    def test_normalize_ethereum_transfer_native(self):
        raw = {
            "hash": "0xabc123",
            "from": "0x1111111111111111111111111111111111111111",
            "to": "0x2222222222222222222222222222222222222222",
            "value": "1000000000000000000",
            "timeStamp": "1699999999",
            "blockNumber": "12345678",
        }
        transfer = TransactionNormalizer.normalize_ethereum_transfer(raw)
        assert transfer.token_symbol == "ETH"
        assert transfer.token_contract is None
        assert transfer.amount == "1.0"
        assert transfer.edge_type == EdgeType.NATIVE_TRANSFER

    def test_normalize_tron_transfer_trc20(self):
        raw = {
            "transaction_id": "0xabc123",
            "from": "T111111111111111111111111111111111",
            "to": "T222222222222222222222222222222222",
            "value": "1000000",
            "token_info": {
                "address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                "symbol": "USDT",
                "decimals": "6",
            },
            "block_timestamp": 1699999999000,
            "block_number": 12345678,
        }
        transfer = TransactionNormalizer.normalize_tron_transfer(raw)
        assert transfer.chain == Chain.TRON
        assert transfer.token_symbol == "USDT"
        assert transfer.token_contract == "tr7nhqjeKQxGTCi8q8ZY4pL8otSzgjLj6t".lower()
        assert transfer.amount == "1.0"
        assert transfer.decimals == 6

    def test_normalize_tron_transfer_native(self):
        raw = {
            "transaction_id": "0xabc123",
            "from": "T111111111111111111111111111111111",
            "to": "T222222222222222222222222222222222",
            "value": "1000000",
            "block_timestamp": 1699999999000,
            "block_number": 12345678,
        }
        transfer = TransactionNormalizer.normalize_tron_transfer(raw)
        assert transfer.token_symbol == "TRX"
        assert transfer.token_contract is None
        assert transfer.amount == "1.0"

    def test_normalize_ethereum_transaction(self):
        raw = {
            "hash": "0xabc123",
            "blockNumber": "12345678",
            "timeStamp": "1699999999",
            "from": "0x1111111111111111111111111111111111111111",
            "to": "0x2222222222222222222222222222222222222222",
            "value": "1000000000000000000",
            "gasUsed": "21000",
            "gasPrice": "20000000000",
            "input": "0x",
            "nonce": "1",
            "transactionIndex": "0",
            "isError": "0",
        }
        tx = TransactionNormalizer.normalize_ethereum_transaction(raw)
        assert tx.tx_hash == "0xabc123"
        assert tx.chain == Chain.ETHEREUM
        assert tx.block_number == 12345678
        assert tx.from_address == "0x1111111111111111111111111111111111111111".lower()
        assert tx.to_address == "0x2222222222222222222222222222222222222222".lower()
        assert tx.value == "1000000000000000000"
        assert tx.is_success

    def test_normalize_address(self):
        addr = TransactionNormalizer.normalize_address(
            address="0x1234567890123456789012345678901234567890",
            chain=Chain.ETHEREUM,
            label="Test Exchange",
            entity_type=EntityCategory.EXCHANGE,
            confidence=0.85,
            source="verified_exchange",
        )
        assert addr.address == "0x1234567890123456789012345678901234567890".lower()
        assert addr.label == "Test Exchange"
        assert addr.entity_type == EntityCategory.EXCHANGE
        assert addr.confidence == 0.85
