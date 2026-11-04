"""Registro, inicio de sesión, tokens y autorización por permisos."""

import datetime as dt
import sqlite3
import uuid
from contextlib import closing

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.audit import record
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
_bearer = HTTPBearer(auto_error=False)  # sin token debe responder 401, no el 403 por defecto


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


def _register_failure(conn: sqlite3.Connection, account_id: str) -> None:
    """Una sola sentencia: los intentos simultáneos no pueden pisarse el contador."""
    until = (_now() + dt.timedelta(minutes=LOCK_MINUTES)).isoformat()
    conn.execute(
        """UPDATE accounts SET
             locked_until = CASE WHEN failed_logins + 1 >= :max THEN :until ELSE locked_until END,
             failed_logins = CASE WHEN failed_logins + 1 >= :max THEN 0 ELSE failed_logins + 1 END
           WHERE id = :id""",
        {"max": MAX_FAILED_LOGINS, "until": until, "id": account_id},
    )


def _authenticate(alias: str, password: str) -> str | None:
    """Devuelve el id de la cuenta si las credenciales son válidas."""
    with closing(connect()) as conn, conn:
        account = conn.execute("SELECT * FROM accounts WHERE alias = ?", (alias,)).fetchone()
        stored = account["password_hash"] if account is not None else _DECOY_HASH
        valid = verify_password(stored, password)
        if account is None or _is_locked(account) or not account["is_active"]:
            return None
        if not valid:
            _register_failure(conn, account["id"])
            return None
        new_hash = hash_password(password) if needs_rehash(stored) else stored
        conn.execute(
            "UPDATE accounts SET failed_logins = 0, locked_until = NULL, password_hash = ? WHERE id = ?",
            (new_hash, account["id"]),
        )
        return account["id"]


def current_account(credentials: HTTPAuthorizationCredentials | None) -> sqlite3.Row | None:
    """La cuenta activa del token. Se lee de la base en cada petición: un cambio de rol rige de inmediato."""
    if credentials is None:
        return None
    try:
        account_id = decode_token(credentials.credentials)
    except jwt.InvalidTokenError:
        return None
    with closing(connect()) as conn:
        return conn.execute(
            """SELECT a.*, r.name AS role FROM accounts a JOIN roles r ON r.id = a.role_id
               WHERE a.id = ? AND a.is_active = 1""",
            (account_id,),
        ).fetchone()


def _has_permission(account: sqlite3.Row, code: str) -> bool:
    with closing(connect()) as conn:
        row = conn.execute(
            """SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
               WHERE rp.role_id = ? AND p.code = ?""",
            (account["role_id"], code),
        ).fetchone()
    return row is not None


def require_permission(code: str):
    """Dependencia de FastAPI: deja pasar solo a cuentas con el permiso y audita cada rechazo."""

    def dependency(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> sqlite3.Row:
        account = current_account(credentials)
        if account is None:
            record(code, "denegado")
            raise HTTPException(status_code=401, detail="Sesión requerida", headers={"WWW-Authenticate": "Bearer"})
        if not _has_permission(account, code):
            record(code, "denegado", actor_id=account["id"])
            raise HTTPException(status_code=403, detail="Permiso denegado")
        return account

    return dependency


def is_last_admin(conn: sqlite3.Connection, account_id: str) -> bool:
    """Debe llamarse dentro de `write_transaction`, para que dos admins no se eliminen a la vez."""
    admins = conn.execute(
        """SELECT a.id FROM accounts a JOIN roles r ON r.id = a.role_id
           WHERE r.name = 'admin' AND a.is_active = 1"""
    ).fetchall()
    return [row["id"] for row in admins] == [account_id]


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
