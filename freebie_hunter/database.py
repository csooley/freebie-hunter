"""SQLite database schema and CRUD operations."""

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from freebie_hunter.config import DB_PATH, DATA_DIR

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    """Get a database connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize database schema if it doesn't exist."""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                url TEXT UNIQUE,
                title TEXT,
                description TEXT,
                category TEXT,
                region TEXT,
                value_estimate TEXT,
                email_used TEXT,
                status TEXT DEFAULT 'new',
                offer_type TEXT DEFAULT 'freebie',
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                claimed_at TIMESTAMP,
                shipped_at TIMESTAMP,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT,
                session_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                used_for_offer_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS run_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                offers_found INTEGER,
                offers_claimed INTEGER,
                errors TEXT,
                duration_seconds REAL
            );

            CREATE INDEX IF NOT EXISTS idx_offers_url ON offers(url);
            CREATE INDEX IF NOT EXISTS idx_offers_status ON offers(status);
            CREATE INDEX IF NOT EXISTS idx_offers_source ON offers(source);
            CREATE INDEX IF NOT EXISTS idx_offers_category ON offers(category);
            CREATE INDEX IF NOT EXISTS idx_offers_type ON offers(offer_type);
        """)
        conn.commit()
    finally:
        conn.close()


# --- Offer CRUD ---

def offer_exists_by_url(url: str) -> bool:
    """Check if an offer with this URL already exists."""
    conn = get_connection()
    try:
        cur = conn.execute("SELECT 1 FROM offers WHERE url = ?", (url,))
        return cur.fetchone() is not None
    finally:
        conn.close()


def offer_exists_by_title(title: str, threshold: float = 0.85) -> bool:
    """Check if a similar title already exists using simple similarity."""
    conn = get_connection()
    try:
        cur = conn.execute("SELECT title FROM offers")
        for row in cur.fetchall():
            if _title_similarity(title, row["title"]) >= threshold:
                return True
        return False
    finally:
        conn.close()


def _title_similarity(a: str, b: str) -> float:
    """Jaccard-like word similarity between two titles."""
    if not a or not b:
        return 0.0
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    intersection = wa & wb
    union = wa | wb
    return len(intersection) / len(union)


def insert_offer(offer: dict) -> Optional[int]:
    """Insert a new offer. Returns offer ID or None if duplicate."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO offers (source, url, title, description, category, region, value_estimate, offer_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            offer.get("source", ""),
            offer.get("url", ""),
            offer.get("title", ""),
            offer.get("description", ""),
            offer.get("category", "other"),
            offer.get("region", "unknown"),
            offer.get("value_estimate", ""),
            offer.get("offer_type", "freebie"),
        ))
        conn.commit()
        cur = conn.execute("SELECT id FROM offers WHERE url = ?", (offer.get("url", ""),))
        row = cur.fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def update_offer_status(offer_id: int, status: str, email_used: str = None,
                        notes: str = None) -> None:
    """Update an offer's status and optionally email/notes."""
    conn = get_connection()
    try:
        fields = ["status = ?"]
        params = [status]

        if email_used:
            fields.append("email_used = ?")
            params.append(email_used)

        if notes:
            fields.append("notes = ?")
            params.append(notes)

        if status == "claimed":
            fields.append("claimed_at = CURRENT_TIMESTAMP")
        elif status == "shipped":
            fields.append("shipped_at = CURRENT_TIMESTAMP")

        params.append(offer_id)
        conn.execute(f"UPDATE offers SET {', '.join(fields)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def get_offers(status: str = None, limit: int = 100, offset: int = 0) -> list[dict]:
    """Get offers, optionally filtered by status."""
    conn = get_connection()
    try:
        if status:
            cur = conn.execute(
                "SELECT * FROM offers WHERE status = ? ORDER BY discovered_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset)
            )
        else:
            cur = conn.execute(
                "SELECT * FROM offers ORDER BY discovered_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_offer_by_id(offer_id: int) -> Optional[dict]:
    """Get a single offer by ID."""
    conn = get_connection()
    try:
        cur = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_new_offers_count() -> int:
    """Count offers with status 'new'."""
    conn = get_connection()
    try:
        cur = conn.execute("SELECT COUNT(*) as cnt FROM offers WHERE status = 'new'")
        return cur.fetchone()["cnt"]
    finally:
        conn.close()


def get_total_offers() -> int:
    """Total offers in database."""
    conn = get_connection()
    try:
        cur = conn.execute("SELECT COUNT(*) as cnt FROM offers")
        return cur.fetchone()["cnt"]
    finally:
        conn.close()


def get_stats() -> dict:
    """Get overall statistics."""
    conn = get_connection()
    try:
        stats = {}
        for row in conn.execute("SELECT status, COUNT(*) as cnt FROM offers GROUP BY status"):
            stats[row["status"]] = row["cnt"]

        cur = conn.execute("SELECT category, COUNT(*) as cnt FROM offers GROUP BY category ORDER BY cnt DESC")
        categories = {row["category"]: row["cnt"] for row in cur.fetchall()}

        cur = conn.execute("SELECT source, COUNT(*) as cnt FROM offers GROUP BY source ORDER BY cnt DESC")
        sources = {row["source"]: row["cnt"] for row in cur.fetchall()}

        cur = conn.execute("SELECT offer_type, COUNT(*) as cnt FROM offers GROUP BY offer_type ORDER BY cnt DESC")
        types = {row["offer_type"]: row["cnt"] for row in cur.fetchall()}

        return {
            "by_status": stats,
            "by_category": categories,
            "by_source": sources,
            "by_type": types,
            "total": sum(stats.values()),
        }
    finally:
        conn.close()


# --- Email CRUD ---

def save_email(address: str, session_id: str, offer_id: int = None,
               duration_hours: int = 1) -> int:
    """Save an email address record."""
    conn = get_connection()
    try:
        expires_at = datetime.now() + timedelta(hours=duration_hours)
        cur = conn.execute("""
            INSERT INTO emails (address, session_id, expires_at, used_for_offer_id)
            VALUES (?, ?, ?, ?)
        """, (address, session_id, expires_at.isoformat(), offer_id))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_recent_emails(limit: int = 10) -> list[dict]:
    """Get recently created emails."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT * FROM emails ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# --- Run Log CRUD ---

def log_run(offers_found: int, offers_claimed: int, errors: str = "",
            duration_seconds: float = 0) -> None:
    """Log a run to the run_log table."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO run_log (offers_found, offers_claimed, errors, duration_seconds)
            VALUES (?, ?, ?, ?)
        """, (offers_found, offers_claimed, errors, duration_seconds))
        conn.commit()
    finally:
        conn.close()


def get_recent_runs(limit: int = 5) -> list[dict]:
    """Get recent run logs."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT * FROM run_log ORDER BY run_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
