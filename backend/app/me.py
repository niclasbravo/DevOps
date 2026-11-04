"""Datos de la propia cuenta."""

import sqlite3
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.audit import record
from app.auth import EMAIL_PATTERN, PASSWORD_MAX, PASSWORD_MIN, is_last_admin, require_permission
from app.crypto import encrypt_email, hash_password, pseudonymize, verify_password
from app.db import connect

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
    with closing(connect()) as conn, conn:
        if req.new_password is not None:
            conn.execute(
                "UPDATE accounts SET password_hash = ? WHERE id = ?", (hash_password(req.new_password), account["id"])
            )
        if "email" in req.model_fields_set:  # enviar null borra el correo
            encrypted = encrypt_email(req.email) if req.email else None
            conn.execute("UPDATE accounts SET email_encrypted = ? WHERE id = ?", (encrypted, account["id"]))
    record("me:update", "permitido", actor_id=account["id"])
    return {"status": "ok"}


@router.delete("", status_code=204)
def delete_me(account: sqlite3.Row = Depends(require_permission("me:delete"))) -> Response:
    """Borra la cuenta. Sin su id ya no se puede recalcular el seudónimo: los votos quedan sin vínculo."""
    if is_last_admin(account["id"]):
        raise HTTPException(status_code=409, detail="No se puede dejar el sistema sin administradores")
    record("me:delete", "permitido", actor_id=account["id"])
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM accounts WHERE id = ?", (account["id"],))
    return Response(status_code=204)
