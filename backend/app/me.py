"""Datos de la propia cuenta."""

import sqlite3
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.audit import record
from app.auth import EMAIL_PATTERN, PASSWORD_MAX, PASSWORD_MIN, is_last_admin, require_permission
from app.crypto import encrypt_email, hash_password, pseudonymize, verify_password
from app.db import connect, write_transaction

router = APIRouter(prefix="/me", tags=["cuenta"])


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX)
    new_password: str | None = Field(default=None, min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)


@router.get("")
def read_me(account: sqlite3.Row = Depends(require_permission("me:read"))) -> dict:
    return {
        "alias": account["alias"],
        "role": account["role"],
        "created_on": account["created_on"],
        "has_email": account["email_encrypted"] is not None,
    }


@router.get("/feedback")
def read_my_feedback(account: sqlite3.Row = Depends(require_permission("me:read"))) -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT game, vote, voted_on FROM feedback WHERE pseudonym = ? ORDER BY id",
            (pseudonymize(account["id"]),),
        ).fetchall()
    return [dict(row) for row in rows]


@router.patch("")
def update_me(req: AccountUpdate, account: sqlite3.Row = Depends(require_permission("me:update"))) -> dict:
    if not verify_password(account["password_hash"], req.current_password):
        record("me:update", "denegado", actor_id=account["id"])
        raise HTTPException(status_code=403, detail="La contraseña actual no es correcta")
    new_hash = hash_password(req.new_password) if req.new_password is not None else None
    new_email = encrypt_email(req.email) if req.email else None
    with write_transaction() as conn:
        record("me:update", "permitido", actor_id=account["id"], conn=conn)
        if new_hash is not None:
            conn.execute("UPDATE accounts SET password_hash = ? WHERE id = ?", (new_hash, account["id"]))
        if "email" in req.model_fields_set:  # enviar null borra el correo
            conn.execute("UPDATE accounts SET email_encrypted = ? WHERE id = ?", (new_email, account["id"]))
    return {"status": "ok"}


@router.delete("", status_code=204)
def delete_me(account: sqlite3.Row = Depends(require_permission("me:delete"))) -> Response:
    """Borra la cuenta. Sin su id ya no se puede recalcular el seudónimo: los votos quedan sin vínculo."""
    with write_transaction() as conn:
        blocked = is_last_admin(conn, account["id"])
        record("me:delete", "denegado" if blocked else "permitido", actor_id=account["id"], conn=conn)
        if not blocked:
            conn.execute("DELETE FROM accounts WHERE id = ?", (account["id"],))
    if blocked:
        raise HTTPException(status_code=409, detail="No se puede dejar el sistema sin administradores")
    return Response(status_code=204)
