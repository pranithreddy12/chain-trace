import json
from typing import List, Optional, Dict, Any
from datetime import datetime

from .database import get_database
from ..domain.models.transfer import Transfer
from ..domain.models.transaction import Transaction
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
    EdgeType,
    InvestigationStatus,
    TransferDirection,
)


class TransactionRepository:
    def __init__(self):
        self.db = get_database()

    def save_transfer(self, transfer: Transfer) -> None:
        self.db.execute(
            """
            INSERT OR IGNORE INTO transactions
            (chain, tx_hash, block_number, timestamp, from_address, to_address,
             token_contract, token_symbol, amount, amount_raw, decimals, source, raw_reference, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                transfer.chain.value,
                transfer.transaction_hash,
                transfer.block_number,
                int(transfer.timestamp.timestamp()),
                transfer.from_address,
                transfer.to_address,
                transfer.token_contract,
                transfer.token_symbol,
                transfer.amount,
                transfer.amount_raw,
                transfer.decimals,
                transfer.source,
                json.dumps({"original": transfer.model_dump(mode="json")}),
                int(datetime.utcnow().timestamp()),
            ),
        )

    def save_transfers(self, transfers: List[Transfer]) -> int:
        count = 0
        for transfer in transfers:
            try:
                self.save_transfer(transfer)
                count += 1
            except Exception:
                pass
        return count

    def get_transfers_from(
        self, address: str, chain: Chain, limit: int = 1000
    ) -> List[Transfer]:
        rows = self.db.fetch_all(
            """
            SELECT * FROM transactions
            WHERE from_address = ? AND chain = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (
                address.lower() if chain.is_evm else address,
                chain.value,
                limit,
            ),
        )
        return [self._row_to_transfer(row) for row in rows]

    def get_transfers_to(
        self, address: str, chain: Chain, limit: int = 1000
    ) -> List[Transfer]:
        rows = self.db.fetch_all(
            """
            SELECT * FROM transactions
            WHERE to_address = ? AND chain = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (
                address.lower() if chain.is_evm else address,
                chain.value,
                limit,
            ),
        )
        return [self._row_to_transfer(row) for row in rows]

    def get_transfers_between(
        self, from_addr: str, to_addr: str, chain: Chain
    ) -> List[Transfer]:
        rows = self.db.fetch_all(
            """
            SELECT * FROM transactions
            WHERE from_address = ? AND to_address = ? AND chain = ?
            ORDER BY timestamp DESC
        """,
            (
                from_addr.lower() if chain.is_evm else from_addr,
                to_addr.lower() if chain.is_evm else to_addr,
                chain.value,
            ),
        )
        return [self._row_to_transfer(row) for row in rows]

    def _row_to_transfer(self, row: sqlite3.Row) -> Transfer:
        return Transfer(
            transaction_hash=row["tx_hash"],
            chain=Chain(row["chain"]),
            from_address=row["from_address"],
            to_address=row["to_address"] or "",
            token_symbol=row["token_symbol"],
            token_contract=row["token_contract"],
            amount=row["amount"],
            amount_raw=row["amount_raw"],
            decimals=row["decimals"],
            timestamp=datetime.fromtimestamp(row["timestamp"]),
            block_number=row["block_number"],
            direction=TransferDirection.OUTGOING,
            edge_type=EdgeType.TOKEN_TRANSFER
            if row["token_contract"]
            else EdgeType.NATIVE_TRANSFER,
            source=row["source"],
        )


class AddressRepository:
    def __init__(self):
        self.db = get_database()

    def save_address(self, address: Address) -> None:
        self.db.execute(
            """
            INSERT OR REPLACE INTO addresses
            (address, chain, label, category, source, confidence, metadata, first_seen, last_seen, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                address.address,
                address.chain.value,
                address.label,
                address.entity_type.value,
                address.source.value if address.source else None,
                address.confidence,
                json.dumps(address.metadata),
                int(address.first_seen.timestamp()) if address.first_seen else None,
                int(address.last_seen.timestamp()) if address.last_seen else None,
                int(datetime.utcnow().timestamp()),
            ),
        )

    def get_address(self, address: str, chain: Chain) -> Optional[Address]:
        normalized = address.lower() if chain.is_evm else address
        row = self.db.fetch_one(
            """
            SELECT * FROM addresses WHERE address = ? AND chain = ?
        """,
            (normalized, chain.value),
        )
        if row:
            return self._row_to_address(row)
        return None

    def get_addresses_by_category(
        self, chain: Chain, category: EntityCategory
    ) -> List[Address]:
        rows = self.db.fetch_all(
            """
            SELECT * FROM addresses WHERE chain = ? AND category = ?
        """,
            (chain.value, category.value),
        )
        return [self._row_to_address(row) for row in rows]

    def _row_to_address(self, row: sqlite3.Row) -> Address:
        from ..domain.enums import LabelSource

        return Address(
            address=row["address"],
            chain=Chain(row["chain"]),
            label=row["label"],
            entity_type=EntityCategory(row["category"]),
            confidence=row["confidence"],
            source=LabelSource(row["source"]) if row["source"] else None,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            first_seen=datetime.fromtimestamp(row["first_seen"])
            if row["first_seen"]
            else None,
            last_seen=datetime.fromtimestamp(row["last_seen"])
            if row["last_seen"]
            else None,
        )


class InvestigationRepository:
    def __init__(self):
        self.db = get_database()

    def save_investigation(self, investigation: Investigation) -> None:
        self.db.execute(
            """
            INSERT OR REPLACE INTO investigations
            (investigation_id, seed_address, chain, max_depth, max_branches, token_filter,
             started_at, completed_at, status, transactions_examined, nodes_found, edges_found,
             warnings, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                investigation.investigation_id,
                investigation.seed_address,
                investigation.chain.value,
                investigation.max_depth,
                investigation.max_branches,
                investigation.token_filter,
                int(investigation.start_time.timestamp()),
                int(investigation.completed_at.timestamp())
                if investigation.completed_at
                else None,
                investigation.status.value,
                investigation.transactions_examined,
                investigation.nodes_found,
                investigation.edges_found,
                json.dumps(investigation.warnings),
                investigation.error,
            ),
        )

    def save_graph(self, investigation_id: str, graph: TransactionGraph) -> None:
        for node in graph.nodes.values():
            self.db.execute(
                """
                INSERT OR REPLACE INTO investigation_nodes
                (investigation_id, address, chain, depth, node_type, score,
                 incoming_amount, outgoing_amount, pattern_flags,
                 is_seed, is_endpoint, is_suspicious, is_obfuscation_point)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    investigation_id,
                    node.address.address,
                    node.address.chain.value,
                    node.depth,
                    node.node_type.value,
                    0.0,
                    node.incoming_amount,
                    node.outgoing_amount,
                    json.dumps(node.pattern_flags),
                    1 if node.is_seed else 0,
                    1 if node.is_endpoint else 0,
                    1 if node.is_suspicious else 0,
                    1 if node.is_obfuscation_point else 0,
                ),
            )

        for edge in graph.edges:
            self.db.execute(
                """
                INSERT OR REPLACE INTO investigation_edges
                (investigation_id, tx_hash, from_address, to_address, chain,
                 token_contract, token_symbol, amount, timestamp,
                 is_highlighted, is_suspicious, path_rank)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    investigation_id,
                    edge.transfer.transaction_hash,
                    edge.transfer.from_address,
                    edge.transfer.to_address,
                    edge.transfer.chain.value,
                    edge.transfer.token_contract,
                    edge.transfer.token_symbol,
                    edge.transfer.amount,
                    int(edge.transfer.timestamp.timestamp()),
                    1 if edge.is_highlighted else 0,
                    1 if edge.is_suspicious else 0,
                    edge.path_rank,
                ),
            )

    def save_candidate_endpoints(
        self, investigation_id: str, endpoints: List[CandidateEndpoint]
    ) -> None:
        for ep in endpoints:
            self.db.execute(
                """
                INSERT OR REPLACE INTO candidate_endpoints
                (investigation_id, address, chain, total_amount_received, hop_count,
                 unlabeled_hop_count, amount_concentration, path_directness, label_confidence,
                 confidence_score, pattern_flags, obfuscation_points, elapsed_seconds, evidence, path_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    investigation_id,
                    ep.address.address,
                    ep.address.chain.value,
                    ep.total_amount_received,
                    ep.hop_count,
                    ep.unlabeled_hop_count,
                    ep.amount_concentration,
                    ep.path_directness,
                    ep.label_confidence,
                    ep.confidence_score,
                    json.dumps(ep.pattern_flags),
                    ep.obfuscation_points,
                    ep.elapsed_seconds,
                    json.dumps(ep.evidence),
                    json.dumps(
                        [
                            {
                                "from": t.from_address,
                                "to": t.to_address,
                                "amount": t.amount,
                                "token": t.token_symbol,
                            }
                            for t in ep.path
                        ]
                    ),
                ),
            )

    def get_investigation(self, investigation_id: str) -> Optional[Investigation]:
        row = self.db.fetch_one(
            "SELECT * FROM investigations WHERE investigation_id = ?",
            (investigation_id,),
        )
        if row:
            return Investigation(
                investigation_id=row["investigation_id"],
                seed_address=row["seed_address"],
                chain=Chain(row["chain"]),
                max_depth=row["max_depth"],
                max_branches=row["max_branches"],
                token_filter=row["token_filter"],
                start_time=datetime.fromtimestamp(row["started_at"]),
                completed_at=datetime.fromtimestamp(row["completed_at"])
                if row["completed_at"]
                else None,
                status=InvestigationStatus(row["status"]),
                transactions_examined=row["transactions_examined"],
                nodes_found=row["nodes_found"],
                edges_found=row["edges_found"],
                warnings=json.loads(row["warnings"]) if row["warnings"] else [],
                error=row["error"],
            )
        return None
