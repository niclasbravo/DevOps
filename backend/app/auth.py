"""Registro, inicio de sesión y tokens."""

import datetime as dt
import sqlite3
import uuid
from contextlib import closing

import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.crypto import encrypt_email, get_key, hash_password, needs_rehash, verify_password
from app.db import connect

TOKEN_MINUTES = 30
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15

ALIAS_PATTERN = r"^[A-Za-z0-9_-]{3,30}$"  # sin @: un correo no sirve como alias
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
PASSWORD_MIN = 12
PASSWORD_MAX = 128  # límite antes de llegar a Argon2: una clave enorme no debe poder saturar el servidor

INVALID_CREDENTIALS = "Credenciales inválidas"

# Con un alias inexistente se verifica contra este hash: la respuesta cuesta lo mismo que con uno real.
_DECOY_HASH = hash_password("cuenta-inexistente")

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(pattern=ALIAS_PATTERN, examples=["jugador_1"])
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(pattern=ALIAS_PATTERN)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def create_token(account_id: str) -> str:
    now = _now()
    claims = {"sub": account_id, "iat": now, "exp": now + dt.timedelta(minutes=TOKEN_MINUTES)}
    return jwt.encode(claims, get_key("GM_JWT_SECRET"), algorithm="HS256")


def decode_token(token: str) -> str:
    """Devuelve el id de la cuenta o lanza jwt.InvalidTokenError."""
    claims = jwt.decode(token, get_key("GM_JWT_SECRET"), algorithms=["HS256"], options={"require": ["exp", "sub"]})
    return claims["sub"]


def create_account(alias: str, password: str, email: str | None = None, role: str = "usuario") -> str:
    """Crea la cuenta y devuelve su id. Lanza sqlite3.IntegrityError si el alias ya existe."""
    account_id = str(uuid.uuid4())
    encrypted = encrypt_email(email) if email else None
    with closing(connect()) as conn, conn:
        conn.execute(
            """INSERT INTO accounts (id, alias, password_hash, email_encrypted, role_id, created_on)
               VALUES (?, ?, ?, ?, (SELECT id FROM roles WHERE name = ?), ?)""",
            (account_id, alias, hash_password(password), encrypted, role, _now().date().isoformat()),
        )
    return account_id


def _is_locked(account: sqlite3.Row) -> bool:
    locked_until = account["locked_until"]
    return locked_until is not None and dt.datetime.fromisoformat(locked_until) > _now()


def _register_failure(conn: sqlite3.Connection, account: sqlite3.Row) -> None:
    failures = account["failed_logins"] + 1
    if failures >= MAX_FAILED_LOGINS:
        until = (_now() + dt.timedelta(minutes=LOCK_MINUTES)).isoformat()
        conn.execute("UPDATE accounts SET failed_logins = 0, locked_until = ? WHERE id = ?", (until, account["id"]))
    else:
        conn.execute("UPDATE accounts SET failed_logins = ? WHERE id = ?", (failures, account["id"]))


def _authenticate(alias: str, password: str) -> str | None:
    """Devuelve el id de la cuenta si las credenciales son válidas."""
    with closing(connect()) as conn, conn:
        account = conn.execute("SELECT * FROM accounts WHERE alias = ?", (alias,)).fetchone()
        stored = account["password_hash"] if account is not None else _DECOY_HASH
        valid = verify_password(stored, password)
        if account is None or _is_locked(account) or not account["is_active"]:
            return None
        if not valid:
            _register_failure(conn, account)
            return None
        new_hash = hash_password(password) if needs_rehash(stored) else stored
        conn.execute(
            "UPDATE accounts SET failed_logins = 0, locked_until = NULL, password_hash = ? WHERE id = ?",
            (new_hash, account["id"]),
        )
        return account["id"]


@router.post("/register", status_code=201)
def register(req: RegisterRequest) -> dict:
    try:
        create_account(req.alias, req.password, req.email)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Ese alias no está disponible") from exc
    return {"alias": req.alias}


@router.post("/login")
def login(req: LoginRequest) -> dict:
    account_id = _authenticate(req.alias, req.password)
    if account_id is None:
        raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS)
    return {"access_token": create_token(account_id), "token_type": "bearer"}
