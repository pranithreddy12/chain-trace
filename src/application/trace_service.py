import asyncio
import time
from typing import List, Dict, Set, Optional, Tuple
from datetime import datetime
from collections import deque
from dataclasses import dataclass, field

from ..domain.models.transfer import Transfer
from ..domain.models.address import Address
from ..domain.models.investigation import (
    Investigation,
    InvestigationResult,
    CandidateEndpoint,
)
from ..domain.models.graph import TransactionGraph, GraphNode, GraphEdge
from ..domain.enums import (
    Chain,
    EntityCategory,
    NodeType,
    TransferDirection,
    InvestigationStatus,
)
from ..blockchain.base import BlockchainProvider, ProviderError, ProviderErrorType
from ..blockchain.etherscan import EtherscanProvider
from ..blockchain.trongrid import TronGridProvider
from ..persistence.repositories import (
    TransactionRepository,
    AddressRepository,
    InvestigationRepository,
)
from ..config.settings import get_settings


@dataclass
class TraceContext:
    investigation: Investigation
    graph: TransactionGraph
    visited: Set[str] = field(default_factory=set)
    queue: deque = field(default_factory=deque)
    address_repo: AddressRepository = field(default_factory=AddressRepository)
    tx_repo: TransactionRepository = field(default_factory=TransactionRepository)
    provider: Optional[BlockchainProvider] = None
    settings = get_settings()


class TraceEngine:
    def __init__(self):
        self.settings = get_settings()
        self.tx_repo = TransactionRepository()
        self.address_repo = AddressRepository()
        self.inv_repo = InvestigationRepository()

    def _get_provider(self, chain: Chain) -> BlockchainProvider:
        if chain == Chain.BSC and self.settings.bscscan_api_key:
            # Etherscan V2 free tier excludes BSC — use a BscScan V1 key instead.
            return EtherscanProvider(
                api_key=self.settings.bscscan_api_key,
                chain=chain,
                base_url="https://api.bscscan.com/api",
                use_v2=False,
            )
        if chain.is_evm:
            return EtherscanProvider(
                api_key=self.settings.etherscan_api_key, chain=chain
            )
        elif chain == Chain.TRON:
            return TronGridProvider(api_key=self.settings.trongrid_api_key)
        else:
            raise ValueError(f"Unsupported chain: {chain}")

    async def trace(
        self,
        seed_address: str,
        chain: Chain,
        max_depth: int = 4,
        max_branches: int = 25,
        token_filter: Optional[str] = None,
        incident_time: Optional[datetime] = None,
        reported_amount: Optional[float] = None,
    ) -> InvestigationResult:
        seed_address = (
            seed_address.lower() if chain.is_evm else seed_address
        )

        investigation = Investigation(
            seed_address=seed_address,
            chain=chain,
            max_depth=max_depth,
            max_branches=max_branches,
            token_filter=token_filter,
            incident_time=incident_time,
            reported_amount=reported_amount,
        )
        investigation.mark_running()

        graph = TransactionGraph(seed_address=seed_address)
        provider = self._get_provider(chain)

        ctx = TraceContext(investigation=investigation, graph=graph, provider=provider)

        seed_node = GraphNode(
            address=Address(
                address=seed_address,
                chain=chain,
                entity_type=EntityCategory.VICTIM_SEED,
            ),
            node_type=NodeType.SEED,
            depth=0,
            is_seed=True,
        )
        ctx.graph.add_node(seed_node)
        ctx.visited.add(seed_node.address.address)
        ctx.queue.append((seed_address, 0, incident_time))

        try:
            await self._bfs_trace(ctx, max_depth, max_branches, token_filter)
            investigation.mark_completed(graph)
        except Exception as e:
            investigation.mark_failed(str(e))
            raise
        finally:
            if hasattr(provider, "close"):
                await provider.close()

        self.inv_repo.save_investigation(investigation)
        self.inv_repo.save_graph(investigation.investigation_id, graph)

        result = InvestigationResult(
            investigation=investigation,
            graph=graph,
            seed_address=seed_node.address,
            warnings=investigation.warnings,
        )
        return result

    async def _bfs_trace(
        self,
        ctx: TraceContext,
        max_depth: int,
        max_branches: int,
        token_filter: Optional[str],
    ) -> None:
        deadline = time.monotonic() + ctx.settings.trace_time_budget_seconds
        while ctx.queue:
            if time.monotonic() > deadline:
                ctx.investigation.warnings.append(
                    f"Trace time budget ({ctx.settings.trace_time_budget_seconds}s) "
                    f"reached - results are partial ({len(ctx.queue)} addresses not expanded)"
                )
                break

            current_address, depth, not_before = ctx.queue.popleft()

            if depth >= max_depth:
                continue

            transfers = await self._get_outgoing_transfers(
                ctx, current_address, token_filter, not_before
            )

            ctx.investigation.transactions_examined += len(transfers)

            # Branch limit caps how many *distinct new wallets* we expand from
            # this node — but every real transfer still becomes an edge, including
            # repeated transfers to an address we've already added.
            # Largest-value transfers first, so when the branch limit bites it
            # keeps the money path instead of whatever the API returned first.
            transfers = sorted(transfers, key=lambda t: t.amount_float, reverse=True)

            new_nodes = 0
            limit_hit = False
            for transfer in transfers:
                to_address = transfer.normalized_to()
                is_new_node = to_address not in ctx.graph.nodes

                if is_new_node and new_nodes >= max_branches:
                    if not limit_hit:
                        ctx.investigation.warnings.append(
                            f"Branch limit ({max_branches}) reached at depth {depth} "
                            f"for {current_address}"
                        )
                        limit_hit = True
                    continue

                enqueue = (
                    to_address not in ctx.visited and depth + 1 < max_depth
                )
                await self._process_transfer(
                    ctx, transfer, depth, to_address, enqueue=enqueue
                )
                if enqueue:
                    # money can only leave a wallet AFTER it arrived there
                    ctx.queue[-1] = (to_address, depth + 1, transfer.timestamp)
                if is_new_node:
                    new_nodes += 1

    async def _get_outgoing_transfers(
        self,
        ctx: TraceContext,
        address: str,
        token_filter: Optional[str],
        not_before: Optional[datetime] = None,
    ) -> List[Transfer]:
        normalized = (
            address.lower() if ctx.investigation.chain.is_evm else address
        )

        # Always fetch live. The persistent transactions table is an audit record,
        # not a traversal source — a partial earlier trace must never shadow the API.
        # (BFS visits each address at most once per run, so this is one call each.)
        try:
            # Hard per-address timeout so one slow/flaky provider response can't
            # hang the whole trace for minutes (tenacity retries + 30s socket
            # timeouts on 6 paginated calls can otherwise stack up).
            if token_filter:
                coro = ctx.provider.get_token_transfers(
                    address, contract_address=token_filter
                )
            else:
                coro = ctx.provider.get_all_outgoing_transfers(
                    address, max_pages=3, offset=100
                )
            transfers = await asyncio.wait_for(
                coro, timeout=ctx.settings.per_address_fetch_timeout_seconds
            )

            # Guard against providers returning transfers not originating from
            # the queried address (keeps branch limits meaningful).
            transfers = [t for t in transfers if t.normalized_from() == normalized]

            # Drop scam / airdrop tokens: worthless TRC-20/ERC-20 tokens sent with
            # a uint256-max nominal amount to poison analytics.
            cap = self.settings.max_plausible_transfer_amount
            before = len(transfers)
            transfers = [
                t for t in transfers if 0 < t.amount_float < cap
            ]
            dropped = before - len(transfers)
            if dropped:
                ctx.investigation.warnings.append(
                    f"Ignored {dropped} implausible-amount transfer(s) for "
                    f"{address[:12]} (likely scam/airdrop tokens)"
                )

            if not_before is not None:
                before = len(transfers)
                transfers = [t for t in transfers if t.timestamp >= not_before]
                skipped = before - len(transfers)
                if skipped:
                    ctx.investigation.warnings.append(
                        f"Skipped {skipped} transfer(s) from {address[:12]} that "
                        f"predate the traced funds arriving there"
                    )

            for t in transfers:
                self.tx_repo.save_transfer(t)

            return transfers
        except ProviderError as e:
            if e.retryable:
                ctx.investigation.warnings.append(
                    f"Retryable error for {address}: {e.message}"
                )
            else:
                ctx.investigation.warnings.append(
                    f"Provider error for {address}: {e.message}"
                )
            return []
        except asyncio.TimeoutError:
            ctx.investigation.warnings.append(
                f"Fetch timed out for {address[:12]} after "
                f"{ctx.settings.per_address_fetch_timeout_seconds}s — skipped"
            )
            return []
        except Exception as e:
            ctx.investigation.warnings.append(
                f"Unexpected error for {address}: {str(e)}"
            )
            return []

    async def _process_transfer(
        self,
        ctx: TraceContext,
        transfer: Transfer,
        depth: int,
        to_address: str,
        enqueue: bool = True,
    ) -> None:
        to_addr_obj = await self._get_or_create_address(ctx, to_address, depth + 1)
        from_addr_obj = ctx.graph.get_node(transfer.normalized_from())

        if from_addr_obj:
            from_addr_obj.outgoing_amount = str(
                float(from_addr_obj.outgoing_amount) + transfer.amount_float
            )
            from_addr_obj.last_seen = transfer.timestamp

        to_addr_obj.incoming_amount = str(
            float(to_addr_obj.incoming_amount) + transfer.amount_float
        )
        to_addr_obj.first_seen = min(
            to_addr_obj.first_seen or transfer.timestamp, transfer.timestamp
        )
        to_addr_obj.last_seen = transfer.timestamp

        edge = GraphEdge(transfer=transfer)
        ctx.graph.add_edge(edge)

        if enqueue:
            ctx.visited.add(to_address)
            # placeholder timestamp; _bfs_trace rewrites it with the arrival time
            ctx.queue.append((to_address, depth + 1, None))

    async def _get_or_create_address(
        self, ctx: TraceContext, address: str, depth: int
    ) -> GraphNode:
        existing = ctx.graph.get_node(address)
        if existing:
            return existing

        stored = self.address_repo.get_address(address, ctx.investigation.chain)
        if stored:
            addr = stored
        else:
            addr = Address(address=address, chain=ctx.investigation.chain)

        node = GraphNode(
            address=addr, node_type=self._determine_node_type(addr, depth), depth=depth
        )
        ctx.graph.add_node(node)
        return node

    def _determine_node_type(self, address: Address, depth: int) -> NodeType:
        if address.entity_type == EntityCategory.VICTIM_SEED:
            return NodeType.SEED
        elif address.entity_type == EntityCategory.EXCHANGE:
            return NodeType.EXCHANGE
        elif address.entity_type == EntityCategory.SANCTIONED:
            return NodeType.SANCTIONED_ADDRESS
        elif address.entity_type == EntityCategory.MIXER:
            return NodeType.MIXER
        elif address.entity_type == EntityCategory.BRIDGE:
            return NodeType.BRIDGE
        elif address.entity_type == EntityCategory.CONTRACT:
            return NodeType.CONTRACT_SERVICE
        elif address.entity_type == EntityCategory.OBFUSCATION_POINT:
            return NodeType.UNKNOWN
        elif address.is_labeled:
            return NodeType.INTERMEDIATE_WALLET
        else:
            return NodeType.UNKNOWN


async def trace_funds(
    seed_address: str,
    chain: Chain,
    max_depth: int = 4,
    max_branches: int = 25,
    token_filter: Optional[str] = None,
) -> InvestigationResult:
    engine = TraceEngine()
    return await engine.trace(
        seed_address, chain, max_depth, max_branches, token_filter
    )
