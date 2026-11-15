"""Base de datos SQLite de GameMatch.

SQLite no tiene usuarios ni permisos propios: el control de acceso está en el servidor, y aquí
quedan las reglas que la base sí puede imponer (valores válidos, unicidad y auditoría de solo inserción).
`feedback` no tiene clave foránea hacia `accounts` a propósito: sin la clave del HMAC no se pueden unir.
"""

import datetime as dt
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "usuario": {"feedback:write", "me:read", "me:update", "me:delete", "consent:manage"},
    "analista": {"me:read", "me:update", "me:delete", "reports:read", "consent:manage"},
    "admin": {
        "consent:manage",
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

# Finalidades que dependen del consentimiento. Cambiar un texto de forma material exige subir su versión:
# los consentimientos dados a la versión anterior dejan de valer hasta que se renueven.
PURPOSES: dict[str, dict] = {
    "correo_recuperacion": {
        "version": "1",
        "text": "Guardamos tu correo cifrado solo para ayudarte a recuperar la cuenta. No lo usamos para nada más.",
        "allowed_for_children": True,
    },
    "votos_analitica": {
        "version": "1",
        "text": (
            "Tus votos se suman, sin identificarte, a reportes de qué juegos gustan "
            "y a futuros reentrenamientos del modelo."
        ),
        "allowed_for_children": True,
    },
    "novedades_correo": {
        "version": "1",
        "text": "Te escribimos con novedades del catálogo.",
        "allowed_for_children": False,  # comunicación comercial: no se ofrece a menores de 14
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
  created_on TEXT NOT NULL,
  is_child INTEGER NOT NULL DEFAULT 0,
  pending_guardian INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS consent_purposes (
  code TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  text TEXT NOT NULL,
  allowed_for_children INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS consent_purpose_versions (
  code TEXT NOT NULL REFERENCES consent_purposes(code),
  version TEXT NOT NULL,
  text TEXT NOT NULL,
  published_on TEXT NOT NULL,
  PRIMARY KEY (code, version)
);
CREATE TABLE IF NOT EXISTS consent_events (
  id INTEGER PRIMARY KEY,
  subject TEXT NOT NULL,
  purpose TEXT NOT NULL REFERENCES consent_purposes(code),
  text_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('otorgado', 'revocado')),
  actor TEXT NOT NULL CHECK (actor IN ('titular', 'representante', 'sistema')),
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS consent_events_subject ON consent_events (subject, purpose, id);
CREATE TRIGGER IF NOT EXISTS consent_no_update BEFORE UPDATE ON consent_events
BEGIN SELECT RAISE(ABORT, 'consent_events es de solo inserción'); END;
CREATE TRIGGER IF NOT EXISTS consent_no_delete BEFORE DELETE ON consent_events
WHEN old.ts > strftime('%Y-%m-%dT%H:%M:%S', 'now', '-365 days')
BEGIN SELECT RAISE(ABORT, 'consent_events se conserva 365 días'); END;
CREATE TABLE IF NOT EXISTS guardian_verifications (
  account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL
);
"""

# Columnas agregadas después del Hito 2: una base existente las recibe sin perder datos.
_ADDED_COLUMNS = {
    "accounts": {
        "is_child": "INTEGER NOT NULL DEFAULT 0",
        "pending_guardian": "INTEGER NOT NULL DEFAULT 0",
    },
}


def _db_path() -> Path:
    return Path(os.environ.get("GM_DB_PATH", "data/gamematch.db"))


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:  # nombres fijos del código, nunca de la petición
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _seed_purposes(conn: sqlite3.Connection) -> None:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    for code, purpose in PURPOSES.items():
        conn.execute(
            """INSERT INTO consent_purposes (code, version, text, allowed_for_children) VALUES (?, ?, ?, ?)
               ON CONFLICT (code) DO UPDATE SET version = excluded.version, text = excluded.text,
                 allowed_for_children = excluded.allowed_for_children""",
            (code, purpose["version"], purpose["text"], int(purpose["allowed_for_children"])),
        )
        conn.execute(
            "INSERT OR IGNORE INTO consent_purpose_versions (code, version, text, published_on) VALUES (?, ?, ?, ?)",
            (code, purpose["version"], purpose["text"], today),
        )


def _create_schema(conn: sqlite3.Connection) -> None:
    # Las columnas nuevas van antes del script: un índice o trigger nuevo podría depender de ellas.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'accounts'").fetchone():
        _add_missing_columns(conn)
    conn.executescript(SCHEMA)
    _seed_purposes(conn)
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


@contextmanager
def write_transaction() -> Iterator[sqlite3.Connection]:
    """Toma el bloqueo de escritura antes de leer: lo que se comprueba adentro no cambia hasta el commit."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


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
