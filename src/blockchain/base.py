from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime

from ..domain.models.address import Address
from ..domain.models.transfer import Transfer
from ..domain.models.transaction import Transaction
from ..domain.enums import Chain, ProviderErrorType
from ..config.settings import get_settings


class ProviderError(Exception):
    def __init__(
        self,
        provider: str,
        operation: str,
        error_type: ProviderErrorType,
        message: str,
        status_code: Optional[int] = None,
        retryable: bool = False,
    ):
        self.provider = provider
        self.operation = operation
        self.error_type = error_type
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(f"{provider}.{operation}: {error_type.value} - {message}")


class BlockchainProvider(ABC):
    # Addresses whose history was cut off by the page cap. Truncation must be
    # reported: for a forensics tool, "we fetched the first 500 transfers" and
    # "this wallet made 500 transfers" are completely different claims, and
    # silently conflating them hides money.
    @property
    def truncated_addresses(self) -> set:
        if not hasattr(self, "_truncated_addresses"):
            self._truncated_addresses = set()
        return self._truncated_addresses

    @property
    @abstractmethod
    def chain(self) -> Chain:
        pass

    @abstractmethod
    def validate_address(self, address: str) -> bool:
        pass

    @abstractmethod
    def get_native_transfers(
        self,
        address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
        page: int = 1,
        offset: int = 100,
    ) -> List[Transfer]:
        pass

    @abstractmethod
    def get_token_transfers(
        self,
        address: str,
        contract_address: Optional[str] = None,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
        page: int = 1,
        offset: int = 100,
    ) -> List[Transfer]:
        pass

    @abstractmethod
    def get_transactions(
        self,
        address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
        page: int = 1,
        offset: int = 100,
    ) -> List[Transaction]:
        pass

    @abstractmethod
    def get_block_timestamp(self, block_number: int) -> datetime:
        pass

    @abstractmethod
    def normalize_transaction(self, raw_tx: dict) -> Transaction:
        pass

    @abstractmethod
    def get_token_metadata(self, contract_address: str) -> Optional[dict]:
        pass


class BaseProvider(BlockchainProvider):
    def __init__(self, api_key: str = "", base_url: str = "", timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._settings = get_settings()

    def _handle_response(self, response: dict, operation: str) -> dict:
        if response.get("status") == "0" or response.get("message") == "NOTOK":
            error_msg = response.get("result", "Unknown error")
            raise ProviderError(
                provider=self.chain.value,
                operation=operation,
                error_type=ProviderErrorType.PROVIDER_ERROR,
                message=error_msg,
            )
        return response

    def _handle_rate_limit(self, response: dict) -> bool:
        if "rate limit" in str(response).lower():
            raise ProviderError(
                provider=self.chain.value,
                operation="rate_limit_check",
                error_type=ProviderErrorType.RATE_LIMIT,
                message="API rate limit exceeded",
                retryable=True,
            )
        return False
