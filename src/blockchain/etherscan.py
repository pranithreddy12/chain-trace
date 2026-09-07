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
from ..domain.models.address import Address
from ..domain.enums import Chain, TransferDirection
from ..config.settings import get_settings


# Etherscan V2 is multichain — one API key, chain selected via chainid.
_CHAIN_IDS = {
    Chain.ETHEREUM: "1",
    Chain.BSC: "56",
    Chain.POLYGON: "137",
    Chain.BASE: "8453",
    Chain.ARBITRUM: "42161",
}


class EtherscanProvider(BaseProvider):
    def __init__(
        self,
        api_key: str = "",
        timeout: float = 30.0,
        chain: Chain = Chain.ETHEREUM,
        base_url: Optional[str] = None,
        use_v2: bool = True,
    ):
        super().__init__(
            api_key, base_url or "https://api.etherscan.io/v2/api", timeout
        )
        self._chain = chain
        self._use_v2 = use_v2
        self.chain_id = _CHAIN_IDS.get(chain, "1")
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def chain(self) -> Chain:
        return self._chain

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _build_params(self, module: str, action: str, **kwargs) -> Dict[str, Any]:
        params = {
            "module": module,
            "action": action,
            "apikey": self.api_key,
        }
        if self._use_v2:
            params["chainid"] = self.chain_id
        params.update(kwargs)
        return params

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def _make_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        client = await self._get_client()
        response = await client.get(self.base_url, params=params)
        response.raise_for_status()
        data = response.json()
        # Etherscan returns status "0" + this message for a simply-empty result set.
        if data.get("status") == "0" and "No transactions found" in str(
            data.get("message", "")
        ):
            return {"result": []}
        return self._handle_response(data, f"{params['module']}.{params['action']}")

    def validate_address(self, address: str) -> bool:
        if not address or not isinstance(address, str):
            return False
        address = address.strip().lower()
        return (
            address.startswith("0x")
            and len(address) == 42
            and all(c in "0123456789abcdef" for c in address[2:])
        )

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
                provider="etherscan",
                operation="get_native_transfers",
                error_type=ProviderErrorType.INVALID_ADDRESS,
                message=f"Invalid Ethereum address: {address}",
            )

        params = self._build_params(
            module="account",
            action="txlist",
            address=address,
            startblock=start_block or 0,
            endblock=end_block or 99999999,
            page=page,
            offset=offset,
            sort="desc",
        )

        data = await self._make_request(params)
        transfers = []

        for tx in data.get("result", []):
            if tx.get("value", "0") != "0" and int(tx.get("isError", "1")) == 0:
                direction = (
                    TransferDirection.OUTGOING
                    if tx.get("from", "").lower() == address.lower()
                    else TransferDirection.INCOMING
                )
                transfer = TransactionNormalizer.normalize_ethereum_transfer(
                    tx, chain=self._chain, direction=direction
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
                provider="etherscan",
                operation="get_token_transfers",
                error_type=ProviderErrorType.INVALID_ADDRESS,
                message=f"Invalid Ethereum address: {address}",
            )

        # Etherscan V2 rejects an empty `contractaddress` param — only send it
        # when actually filtering to a specific token.
        extra = {"contractaddress": contract_address} if contract_address else {}
        params = self._build_params(
            module="account",
            action="tokentx",
            address=address,
            startblock=start_block or 0,
            endblock=end_block or 99999999,
            page=page,
            offset=offset,
            sort="desc",
            **extra,
        )

        data = await self._make_request(params)
        transfers = []

        for tx in data.get("result", []):
            direction = (
                TransferDirection.OUTGOING
                if tx.get("from", "").lower() == address.lower()
                else TransferDirection.INCOMING
            )
            transfer = TransactionNormalizer.normalize_ethereum_transfer(
                tx, chain=self._chain, direction=direction
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
                provider="etherscan",
                operation="get_transactions",
                error_type=ProviderErrorType.INVALID_ADDRESS,
                message=f"Invalid Ethereum address: {address}",
            )

        params = self._build_params(
            module="account",
            action="txlist",
            address=address,
            startblock=start_block or 0,
            endblock=end_block or 99999999,
            page=page,
            offset=offset,
            sort="desc",
        )

        data = await self._make_request(params)
        transactions = []

        for tx in data.get("result", []):
            transactions.append(
                TransactionNormalizer.normalize_ethereum_transaction(tx)
            )

        return transactions

    async def get_block_timestamp(self, block_number: int) -> datetime:
        params = self._build_params(
            module="block", action="getblockreward", blockno=block_number
        )
        data = await self._make_request(params)
        timestamp = int(data.get("result", {}).get("timeStamp", 0))
        return datetime.fromtimestamp(timestamp)

    def normalize_transaction(self, raw_tx: Dict[str, Any]) -> Transaction:
        return TransactionNormalizer.normalize_ethereum_transaction(raw_tx)

    async def get_token_metadata(
        self, contract_address: str
    ) -> Optional[Dict[str, Any]]:
        if not self.validate_address(contract_address):
            return None

        params = self._build_params(
            module="token", action="tokeninfo", contractaddress=contract_address
        )

        try:
            data = await self._make_request(params)
            result = data.get("result", [{}])[0]
            return {
                "symbol": result.get("symbol", "").upper(),
                "name": result.get("name", ""),
                "decimals": int(result.get("decimals", 18)),
                "total_supply": result.get("totalSupply", "0"),
            }
        except ProviderError:
            return None

    async def get_all_outgoing_transfers(
        self, address: str, max_pages: int = 5, offset: int = 100
    ) -> List[Transfer]:
        all_transfers = []
        page = 1

        while page <= max_pages:
            native = await self.get_native_transfers(address, page=page, offset=offset)
            token = await self.get_token_transfers(address, page=page, offset=offset)

            page_transfers = [
                t for t in native + token if t.direction == TransferDirection.OUTGOING
            ]
            if not page_transfers:
                break

            all_transfers.extend(page_transfers)

            if len(native) < offset and len(token) < offset:
                break

            page += 1

        return all_transfers
