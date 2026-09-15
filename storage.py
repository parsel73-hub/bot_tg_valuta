"""Локальное хранилище кошелька (SQLite).

Используется только стандартная библиотека sqlite3, поэтому
бот работает без внешних БД. Каждая таблица связана с Telegram
user_id, так что у каждого пользователя свой набор путешествий.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

DB_PATH = os.getenv("WALLET_DB_PATH", os.path.join(os.path.dirname(__file__), "wallet.db"))


# ---------------------------------------------------------------------------
# Подключение
# ---------------------------------------------------------------------------
@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Открыть соединение с БД, включить FK и авто-коммит/откат."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Создать таблицы, если их ещё нет."""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id      INTEGER PRIMARY KEY,
                active_trip  INTEGER,
                created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (active_trip) REFERENCES trips(id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS trips (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                home_country  TEXT NOT NULL,
                dest_country  TEXT NOT NULL,
                home_ccy      TEXT NOT NULL,
                dest_ccy      TEXT NOT NULL,
                -- rate: сколько home_ccy стоит 1 dest_ccy
                rate          REAL NOT NULL,
                -- баланс хранится в домашней валюте (REAL)
                balance_home  REAL NOT NULL DEFAULT 0,
                balance_dest  REAL NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id      INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                amount_dest  REAL NOT NULL,
                amount_home  REAL NOT NULL,
                rate_used    REAL NOT NULL,
                created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_trips_user ON trips(user_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_trip ON expenses(trip_id, created_at DESC);
            """
        )


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------
def ensure_user(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id) VALUES (?)",
            (user_id,),
        )


def get_active_trip_id(user_id: int) -> int | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT active_trip FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row["active_trip"] if row and row["active_trip"] else None


def set_active_trip(user_id: int, trip_id: int) -> None:
    ensure_user(user_id)
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET active_trip = ? WHERE user_id = ?",
            (trip_id, user_id),
        )


# ---------------------------------------------------------------------------
# Путешествия
# ---------------------------------------------------------------------------
def create_trip(
    user_id: int,
    home_country: str,
    dest_country: str,
    home_ccy: str,
    dest_ccy: str,
    rate: float,
    balance_home: float,
    balance_dest: float,
) -> int:
    ensure_user(user_id)
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO trips
                (user_id, home_country, dest_country,
                 home_ccy, dest_ccy, rate, balance_home, balance_dest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                home_country,
                dest_country,
                home_ccy.upper(),
                dest_ccy.upper(),
                rate,
                balance_home,
                balance_dest,
            ),
        )
        trip_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE users SET active_trip = ? WHERE user_id = ?",
            (trip_id, user_id),
        )
        return trip_id


def list_trips(user_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(
            conn.execute(
                """
                SELECT id, home_country, dest_country,
                       home_ccy, dest_ccy, rate,
                       balance_home, balance_dest, created_at
                FROM trips
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (user_id,),
            ).fetchall()
        )


def get_trip(trip_id: int, user_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id, user_id, home_country, dest_country,
                   home_ccy, dest_ccy, rate,
                   balance_home, balance_dest, created_at
            FROM trips
            WHERE id = ? AND user_id = ?
            """,
            (trip_id, user_id),
        ).fetchone()


def update_trip_rate(trip_id: int, user_id: int, new_rate: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE trips SET rate = ? WHERE id = ? AND user_id = ?",
            (new_rate, trip_id, user_id),
        )


def update_trip_balances(
    trip_id: int, user_id: int, balance_home: float, balance_dest: float
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE trips
            SET balance_home = ?, balance_dest = ?
            WHERE id = ? AND user_id = ?
            """,
            (balance_home, balance_dest, trip_id, user_id),
        )


# ---------------------------------------------------------------------------
# Расходы
# ---------------------------------------------------------------------------
def add_expense(
    trip_id: int,
    user_id: int,
    amount_dest: float,
    amount_home: float,
    rate_used: float,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO expenses
                (trip_id, user_id, amount_dest, amount_home, rate_used)
            VALUES (?, ?, ?, ?, ?)
            """,
            (trip_id, user_id, amount_dest, amount_home, rate_used),
        )
        return int(cur.lastrowid)


def list_expenses(trip_id: int, user_id: int, limit: int = 20) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(
            conn.execute(
                """
                SELECT id, amount_dest, amount_home, rate_used, created_at
                FROM expenses
                WHERE trip_id = ? AND user_id = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
                """,
                (trip_id, user_id, limit),
            ).fetchall()
        )


def delete_trip(trip_id: int, user_id: int) -> None:
    """Удалить путешествие вместе с его расходами (FK CASCADE)."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM trips WHERE id = ? AND user_id = ?",
            (trip_id, user_id),
        )
        conn.execute(
            "UPDATE users SET active_trip = NULL WHERE user_id = ? AND active_trip = ?",
            (user_id, trip_id),
        )


def now_iso() -> str:
    """ISO-время в UTC — для логов/отображения."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
