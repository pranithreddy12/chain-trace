import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Generator, Optional

from ..config.settings import get_settings


class Database:
    def __init__(self, db_path: Optional[str] = None):
        self.settings = get_settings()
        self.db_path = Path(db_path or self.settings.database_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain TEXT NOT NULL,
                    tx_hash TEXT NOT NULL,
                    block_number INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    from_address TEXT NOT NULL,
                    to_address TEXT,
                    token_contract TEXT,
                    token_symbol TEXT,
                    amount TEXT NOT NULL,
                    amount_raw TEXT,
                    decimals INTEGER,
                    source TEXT NOT NULL,
                    raw_reference TEXT,
                    fetched_at INTEGER NOT NULL,
                    UNIQUE(chain, tx_hash, from_address, to_address, token_contract)
                );

                CREATE INDEX IF NOT EXISTS idx_transactions_from ON transactions(from_address);
                CREATE INDEX IF NOT EXISTS idx_transactions_to ON transactions(to_address);
                CREATE INDEX IF NOT EXISTS idx_transactions_chain_hash ON transactions(chain, tx_hash);
                CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON transactions(timestamp);

                CREATE TABLE IF NOT EXISTS addresses (
                    address TEXT NOT NULL,
                    chain TEXT NOT NULL,
                    label TEXT,
                    category TEXT NOT NULL,
                    source TEXT,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    metadata TEXT,
                    first_seen INTEGER,
                    last_seen INTEGER,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (address, chain)
                );

                CREATE INDEX IF NOT EXISTS idx_addresses_chain ON addresses(chain);
                CREATE INDEX IF NOT EXISTS idx_addresses_category ON addresses(category);
                CREATE INDEX IF NOT EXISTS idx_addresses_label ON addresses(label);

                CREATE TABLE IF NOT EXISTS investigations (
                    investigation_id TEXT PRIMARY KEY,
                    seed_address TEXT NOT NULL,
                    chain TEXT NOT NULL,
                    max_depth INTEGER NOT NULL,
                    max_branches INTEGER NOT NULL,
                    token_filter TEXT,
                    started_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    status TEXT NOT NULL,
                    transactions_examined INTEGER DEFAULT 0,
                    nodes_found INTEGER DEFAULT 0,
                    edges_found INTEGER DEFAULT 0,
                    warnings TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS investigation_nodes (
                    investigation_id TEXT NOT NULL,
                    address TEXT NOT NULL,
                    chain TEXT NOT NULL,
                    depth INTEGER NOT NULL,
                    node_type TEXT NOT NULL,
                    score REAL DEFAULT 0.0,
                    incoming_amount TEXT DEFAULT '0',
                    outgoing_amount TEXT DEFAULT '0',
                    pattern_flags TEXT,
                    is_seed INTEGER DEFAULT 0,
                    is_endpoint INTEGER DEFAULT 0,
                    is_suspicious INTEGER DEFAULT 0,
                    is_obfuscation_point INTEGER DEFAULT 0,
                    PRIMARY KEY (investigation_id, address, chain),
                    FOREIGN KEY (investigation_id) REFERENCES investigations(investigation_id)
                );

                CREATE TABLE IF NOT EXISTS investigation_edges (
                    investigation_id TEXT NOT NULL,
                    tx_hash TEXT NOT NULL,
                    from_address TEXT NOT NULL,
                    to_address TEXT NOT NULL,
                    chain TEXT NOT NULL,
                    token_contract TEXT,
                    token_symbol TEXT,
                    amount TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    is_highlighted INTEGER DEFAULT 0,
                    is_suspicious INTEGER DEFAULT 0,
                    path_rank INTEGER,
                    PRIMARY KEY (investigation_id, tx_hash, from_address, to_address),
                    FOREIGN KEY (investigation_id) REFERENCES investigations(investigation_id)
                );

                CREATE TABLE IF NOT EXISTS candidate_endpoints (
                    investigation_id TEXT NOT NULL,
                    address TEXT NOT NULL,
                    chain TEXT NOT NULL,
                    total_amount_received TEXT NOT NULL,
                    hop_count INTEGER NOT NULL,
                    unlabeled_hop_count INTEGER NOT NULL,
                    amount_concentration REAL NOT NULL,
                    path_directness REAL NOT NULL,
                    label_confidence REAL NOT NULL,
                    confidence_score REAL NOT NULL,
                    pattern_flags TEXT,
                    obfuscation_points INTEGER DEFAULT 0,
                    elapsed_seconds INTEGER,
                    evidence TEXT,
                    path_json TEXT,
                    PRIMARY KEY (investigation_id, address, chain),
                    FOREIGN KEY (investigation_id) REFERENCES investigations(investigation_id)
                );
            """)

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.connection() as conn:
            return conn.execute(query, params)

    def fetch_one(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self.connection() as conn:
            return conn.execute(query, params).fetchone()

    def fetch_all(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return conn.execute(query, params).fetchall()


_db: Optional[Database] = None


def get_database() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db
