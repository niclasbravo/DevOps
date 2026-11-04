"""Base de datos SQLite de GameMatch.

SQLite no tiene usuarios ni permisos propios: el control de acceso está en el servidor, y aquí
quedan las reglas que la base sí puede imponer (valores válidos, unicidad y auditoría de solo inserción).
`feedback` no tiene clave foránea hacia `accounts` a propósito: sin la clave del HMAC no se pueden unir.
"""

import datetime as dt
import os
import sqlite3
from pathlib import Path

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "usuario": {"feedback:write", "me:read", "me:update", "me:delete"},
    "analista": {"me:read", "me:update", "me:delete", "reports:read"},
    "admin": {
        "me:read",
        "me:update",
        "me:delete",
        "reports:read",
        "model:read",
        "accounts:read",
        "accounts:manage",
        "accounts:reveal_email",
        "audit:read",
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE CHECK (name IN ('admin', 'analista', 'usuario'))
);
CREATE TABLE IF NOT EXISTS permissions (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS role_permissions (
  role_id INTEGER NOT NULL REFERENCES roles(id),
  permission_id INTEGER NOT NULL REFERENCES permissions(id),
  PRIMARY KEY (role_id, permission_id)
);
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,
  alias TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  email_encrypted BLOB,
  role_id INTEGER NOT NULL REFERENCES roles(id),
  is_active INTEGER NOT NULL DEFAULT 1,
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  created_on TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY,
  pseudonym TEXT NOT NULL,
  game TEXT NOT NULL,
  vote INTEGER NOT NULL CHECK (vote IN (1, -1)),
  voted_on TEXT NOT NULL,
  UNIQUE (pseudonym, game)
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  actor_pseudonym TEXT,
  action TEXT NOT NULL,
  target_pseudonym TEXT,
  outcome TEXT NOT NULL CHECK (outcome IN ('permitido', 'denegado'))
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log es de solo inserción'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log es de solo inserción'); END;
"""


def _db_path() -> Path:
    return Path(os.environ.get("GM_DB_PATH", "data/gamematch.db"))


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for role, permissions in ROLE_PERMISSIONS.items():
        conn.execute("INSERT OR IGNORE INTO roles (name) VALUES (?)", (role,))
        for code in permissions:
            conn.execute("INSERT OR IGNORE INTO permissions (code) VALUES (?)", (code,))
            conn.execute(
                """INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
                   SELECT r.id, p.id FROM roles r, permissions p WHERE r.name = ? AND p.code = ?""",
                (role, code),
            )
    conn.commit()


def connect() -> sqlite3.Connection:
    """Una conexión por operación: `sqlite3` no se comparte entre hilos. El esquema se crea si la base es nueva."""
    path = _db_path()
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if is_new:
        _create_schema(conn)
    return conn


def init_db() -> None:
    conn = connect()
    try:
        _create_schema(conn)
    finally:
        conn.close()


def upsert_vote(conn: sqlite3.Connection, pseudonym: str, game: str, vote: int, voted_on: str) -> None:
    """Un voto por persona y juego: votar de nuevo reemplaza el anterior."""
    conn.execute(
        """INSERT INTO feedback (pseudonym, game, vote, voted_on) VALUES (?, ?, ?, ?)
           ON CONFLICT (pseudonym, game) DO UPDATE SET vote = excluded.vote, voted_on = excluded.voted_on""",
        (pseudonym, game, vote, voted_on),
    )


def save_vote(pseudonym: str, game: str, vote: int) -> None:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()  # solo la fecha: la hora exacta no se necesita
    conn = connect()
    try:
        with conn:
            upsert_vote(conn, pseudonym, game, vote, today)
    finally:
        conn.close()
