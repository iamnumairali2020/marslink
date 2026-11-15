import sqlite3
import os
from typing import List, Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "marslink.db")

def get_connection() -> sqlite3.Connection:
    """Returns a sqlite3 connection with row factory set to sqlite3.Row for dictionary-like access."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the SQLite database tables and indexes if they do not exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Packets table with UNIQUE constraint on (crew_id, seq) to block duplicate entries
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS packets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                crew_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                ts REAL NOT NULL,
                received_at REAL NOT NULL,
                hr REAL NOT NULL,
                spo2 REAL NOT NULL,
                dose_msv REAL NOT NULL,
                resent INTEGER NOT NULL DEFAULT 0,
                UNIQUE(crew_id, seq)
            )
        """)
        
        # Events table for system logging (corrupted, missing, resent, lost, alert, blackout)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                time REAL NOT NULL,
                type TEXT NOT NULL,
                crew_id TEXT,
                detail TEXT NOT NULL
            )
        """)
        
        # Create indexes for fast historical queries and dashboard recovery
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_packets_crew_seq ON packets(crew_id, seq DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(time DESC)")
        
        conn.commit()

def insert_packet(
    mission_id: str,
    crew_id: str,
    seq: int,
    ts: float,
    received_at: float,
    hr: float,
    spo2: float,
    dose_msv: float,
    resent: int = 0
) -> bool:
    """
    Inserts a telemetry packet into SQLite. 
    Uses OR IGNORE to let the UNIQUE(crew_id, seq) constraint safely drop duplicate packets.
    Returns True if successfully inserted, False if ignored as a duplicate.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO packets 
                (mission_id, crew_id, seq, ts, received_at, hr, spo2, dose_msv, resent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (mission_id, crew_id, seq, ts, received_at, hr, spo2, dose_msv, resent))
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        print(f"Database insertion error (packet): {e}")
        return False

def insert_event(time_val: float, event_type: str, crew_id: Optional[str], detail: str):
    """Inserts a system or telemetry event record into SQLite."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO events (time, type, crew_id, detail)
                VALUES (?, ?, ?, ?)
            """, (time_val, event_type, crew_id, detail))
            conn.commit()
    except Exception as e:
        print(f"Database insertion error (event): {e}")

def get_recent_packets(crew_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Retrieves the most recent packets, optionally filtered by crew member, for dashboard recovery."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if crew_id:
            cursor.execute("""
                SELECT * FROM packets WHERE crew_id = ? ORDER BY seq DESC LIMIT ?
            """, (crew_id, limit))
        else:
            cursor.execute("""
                SELECT * FROM packets ORDER BY received_at DESC LIMIT ?
            """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def get_recent_events(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieves recent system events for the dashboard event log."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM events ORDER BY time DESC LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]