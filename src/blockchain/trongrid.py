import httpx
from typing import List, Optional, Dict, Any
from datetime import datetime
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .base import BlockchainProvider, BaseProvider, ProviderError, ProviderErrorType
from .normalizer import TransactionNormalizer
from ..domain.models.transfer import Transfer
from ..domain.models.transaction import Transaction
from ..domain.enums import Chain, TransferDirection
from ..config.settings import get_settings


import hashlib

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _hex_to_b58(h: str) -> str:
    """21-byte hex Tron address (41...) -> Base58Check string (T...)."""
    try:
        raw = bytes.fromhex(h[2:] if h.startswith("0x") else h)
        if len(raw) != 21:
            return h
        chk = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
        num = int.from_bytes(raw + chk, "big")
        out = ""
        while num > 0:
            num, rem = divmod(num, 58)
            out = _B58[rem] + out
        for b in raw + chk:
            if b == 0:
                out = "1" + out
            else:
                break
        return out
    except (ValueError, TypeError):
        return h


class TronGridProvider(BaseProvider):
    def __init__(self, api_key: str = "", timeout: float = 30.0):
        super().__init__(api_key, "https://api.trongrid.io", timeout)
        self._client: Optional[httpx.AsyncClient] = None
        self._usdt_contract = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

    @property
    def chain(self) -> Chain:
        return Chain.TRON

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"TRON-PRO-API-KEY": self.api_key} if self.api_key else {}
            self._client = httpx.AsyncClient(timeout=self.timeout, headers=headers)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _handle_response(self, response: dict, operation: str) -> dict:
        if not response.get("success", True) and "data" not in response:
            error_msg = response.get("message", "Unknown error")
            raise ProviderError(
                provider="trongrid",
                operation=operation,
                error_type=ProviderErrorType.PROVIDER_ERROR,
                message=error_msg,
            )
        return response

    def validate_address(self, address: str) -> bool:
        if not address or not isinstance(address, str):
            return False
        address = address.strip()
        return len(address) == 34 and address.startswith("T")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def _make_request(
        self, endpoint: str, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        client = await self._get_client()
        response = await client.get(f"{self.base_url}{endpoint}", params=params or {})
        response.raise_for_status()
        data = response.json()
        return self._handle_response(data, endpoint)

    async def get_native_transfers(
        self,
        address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
        page: int = 1,
        offset: int = 100,
    ) -> List[Transfer]:
        if not self.validate_address(address):
            raise ProviderError(
                provider="trongrid",
                operation="get_native_transfers",
                error_type=ProviderErrorType.INVALID_ADDRESS,
                message=f"Invalid Tron address: {address}",
            )

        params = {"limit": offset}
        if isinstance(page, str) and page:
            params["fingerprint"] = page
        if start_block:
            params["min_block_timestamp"] = start_block * 3000
        if end_block:
            params["max_block_timestamp"] = end_block * 3000

        data = await self._make_request(f"/v1/accounts/{address}/transactions", params)
        self._last_fingerprint = data.get("meta", {}).get("fingerprint")
        transfers = []

        for tx in data.get("data", []):
            if (
                tx.get("raw_data", {}).get("contract", [{}])[0].get("type")
                == "TransferContract"
            ):
                value = (
                    tx.get("raw_data", {})
                    .get("contract", [{}])[0]
                    .get("parameter", {})
                    .get("value", {})
                    .get("amount", 0)
                )
                if value > 0:
                    val = (
                        tx.get("raw_data", {})
                        .get("contract", [{}])[0]
                        .get("parameter", {})
                        .get("value", {})
                    )
                    # raw_data addresses are hex (41...) — convert to base58 (T...)
                    to_addr = _hex_to_b58(val.get("to_address", ""))
                    from_addr = _hex_to_b58(val.get("owner_address", ""))
                    direction = (
                        TransferDirection.OUTGOING
                        if from_addr == address
                        else TransferDirection.INCOMING
                    )

                    raw_transfer = {
                        "transaction_id": tx.get("txID", ""),
                        "from": from_addr,
                        "to": to_addr,
                        "value": str(value),
                        "block_timestamp": tx.get("block_timestamp", 0),
                        "block_number": tx.get("block_number", 0),
                    }
                    transfer = TransactionNormalizer.normalize_tron_transfer(
                        raw_transfer, direction=direction
                    )
                    transfers.append(transfer)

        return transfers

    async def get_token_transfers(
        self,
        address: str,
        contract_address: Optional[str] = None,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
        page: int = 1,
        offset: int = 100,
    ) -> List[Transfer]:
        if not self.validate_address(address):
            raise ProviderError(
                provider="trongrid",
                operation="get_token_transfers",
                error_type=ProviderErrorType.INVALID_ADDRESS,
                message=f"Invalid Tron address: {address}",
            )

        params = {"limit": offset, "only_confirmed": "true"}
        if contract_address:
            params["contract_address"] = contract_address
        if isinstance(page, str) and page:
            params["fingerprint"] = page
        if start_block:
            params["min_block_timestamp"] = start_block * 3000
        if end_block:
            params["max_block_timestamp"] = end_block * 3000

        data = await self._make_request(
            f"/v1/accounts/{address}/transactions/trc20", params
        )
        self._last_fingerprint = data.get("meta", {}).get("fingerprint")
        transfers = []

        for tx in data.get("data", []):
            value = tx.get("value", "0")
            if int(value) > 0:
                direction = (
                    TransferDirection.OUTGOING
                    if tx.get("from", "") == address
                    else TransferDirection.INCOMING
                )
                transfer = TransactionNormalizer.normalize_tron_transfer(
                    tx, direction=direction
                )
                transfers.append(transfer)

        return transfers

    async def get_transactions(
        self,
        address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
        page: int = 1,
        offset: int = 100,
    ) -> List[Transaction]:
        if not self.validate_address(address):
            raise ProviderError(
                provider="trongrid",
                operation="get_transactions",
                error_type=ProviderErrorType.INVALID_ADDRESS,
                message=f"Invalid Tron address: {address}",
            )

        params = {
            "limit": offset,
            "fingerprint": (page - 1) * offset,
        }

        data = await self._make_request(f"/v1/accounts/{address}/transactions", params)
        transactions = []

        for tx in data.get("data", []):
            transactions.append(TransactionNormalizer.normalize_tron_transaction(tx))

        return transactions

    async def get_block_timestamp(self, block_number: int) -> datetime:
        data = await self._make_request(f"/wallet/getblockbynum", {"num": block_number})
        timestamp = data.get("block_header", {}).get("raw_data", {}).get("timestamp", 0)
        if timestamp > 1e12:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp)

    def normalize_transaction(self, raw_tx: Dict[str, Any]) -> Transaction:
        return TransactionNormalizer.normalize_tron_transaction(raw_tx)

    async def get_token_metadata(
        self, contract_address: str
    ) -> Optional[Dict[str, Any]]:
        if not self.validate_address(contract_address):
            return None

        try:
            data = await self._make_request(f"/v1/contracts/{contract_address}")
            result = data.get("data", [{}])[0]
            return {
                "symbol": result.get("symbol", "").upper(),
                "name": result.get("name", ""),
                "decimals": int(result.get("decimals", 6)),
                "total_supply": result.get("total_supply", "0"),
            }
        except ProviderError:
            return None

    async def _paginate_all(
        self, address: str, max_pages: int, offset: int, outgoing_only: bool
    ) -> List[Transfer]:
        """One pagination path for both directions - the cap accounting and the
        truncation signal must not drift between them."""
        all_transfers: List[Transfer] = []
        exhausted = False

        def keep(items):
            if not outgoing_only:
                return items
            return [t for t in items if t.direction == TransferDirection.OUTGOING]

        for fetch in (self.get_token_transfers, self.get_native_transfers):
            fp = None
            for _ in range(max_pages):
                page = await fetch(address, page=fp or 1, offset=offset)
                all_transfers.extend(keep(page))
                fp = getattr(self, "_last_fingerprint", None)
                if not fp or len(page) < offset:
                    break
            else:
                exhausted = True  # ran out of pages, not out of data

        if exhausted:
            self.truncated_addresses.add(address)
        return all_transfers

    async def get_all_outgoing_transfers(
        self, address: str, max_pages: int = 5, offset: int = 100
    ) -> List[Transfer]:
        return await self._paginate_all(address, max_pages, offset, True)

    async def get_all_transfers(
        self, address: str, max_pages: int = 5, offset: int = 100
    ) -> List[Transfer]:
        """Both directions - for the full-activity view of a wallet."""
        return await self._paginate_all(address, max_pages, offset, False)
