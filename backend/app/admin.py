"""Operaciones administrativas. Cada una exige un permiso propio y queda en la auditoría."""

import sqlite3
from contextlib import closing
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.audit import record
from app.auth import is_last_admin, require_permission
from app.crypto import decrypt_email
from app.db import connect, write_transaction
from app.model import get_model
from app.recommender import active_recommender

router = APIRouter(prefix="/admin", tags=["administración"])

NO_ADMINS = "No se puede dejar el sistema sin administradores"


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["usuario", "analista", "admin"]


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


def _target_or_404(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row:
    target = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if target is None:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    return target


def _audit_change(conn: sqlite3.Connection, blocked: bool, actor_id: str, target_id: str) -> None:
    outcome = "denegado" if blocked else "permitido"
    record("accounts:manage", outcome, actor_id=actor_id, target_id=target_id, conn=conn)


@router.get("/model")
def read_model(_: sqlite3.Row = Depends(require_permission("model:read"))) -> dict:
    model = get_model()
    details = {k: model.meta.get(k) for k in ("commit", "sha256", "trained_at", "data_file")} if model else None
    return {"recommender": active_recommender(), "model": details}


@router.get("/accounts")
def list_accounts(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    admin: sqlite3.Row = Depends(require_permission("accounts:read")),
) -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT a.id, a.alias, r.name AS role, a.is_active FROM accounts a
               JOIN roles r ON r.id = a.role_id ORDER BY a.alias LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
    record("accounts:read", "permitido", actor_id=admin["id"])
    return [{**dict(row), "is_active": bool(row["is_active"])} for row in rows]


@router.patch("/accounts/{account_id}/role")
def update_role(
    account_id: str, req: RoleUpdate, admin: sqlite3.Row = Depends(require_permission("accounts:manage"))
) -> dict:
    if account_id == admin["id"]:
        record("accounts:manage", "denegado", actor_id=admin["id"], target_id=account_id)
        raise HTTPException(status_code=403, detail="No puedes cambiar tu propio rol")
    with write_transaction() as conn:
        _target_or_404(conn, account_id)
        blocked = req.role != "admin" and is_last_admin(conn, account_id)
        _audit_change(conn, blocked, admin["id"], account_id)
        if not blocked:
            conn.execute(
                "UPDATE accounts SET role_id = (SELECT id FROM roles WHERE name = ?) WHERE id = ?",
                (req.role, account_id),
            )
    if blocked:
        raise HTTPException(status_code=409, detail=NO_ADMINS)
    return {"status": "ok"}


@router.patch("/accounts/{account_id}/status")
def update_status(
    account_id: str, req: StatusUpdate, admin: sqlite3.Row = Depends(require_permission("accounts:manage"))
) -> dict:
    with write_transaction() as conn:
        target = _target_or_404(conn, account_id)
        # Activar una cuenta de menor sin el representante saltaría el art. 16 quáter.
        pending = req.is_active and bool(target["pending_guardian"])
        blocked = pending or (not req.is_active and is_last_admin(conn, account_id))
        _audit_change(conn, blocked, admin["id"], account_id)
        if not blocked:
            conn.execute("UPDATE accounts SET is_active = ? WHERE id = ?", (int(req.is_active), account_id))
    if pending:
        raise HTTPException(status_code=409, detail="La cuenta espera la confirmación de su representante legal")
    if blocked:
        raise HTTPException(status_code=409, detail=NO_ADMINS)
    return {"status": "ok"}


@router.post("/accounts/{account_id}/reveal-email")
def reveal_email(account_id: str, admin: sqlite3.Row = Depends(require_permission("accounts:reveal_email"))) -> dict:
    with closing(connect()) as conn:
        target = _target_or_404(conn, account_id)
    record("accounts:reveal_email", "permitido", actor_id=admin["id"], target_id=account_id)
    encrypted = target["email_encrypted"]
    return {"email": decrypt_email(encrypted) if encrypted else None}


@router.get("/audit")
def read_audit(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: sqlite3.Row = Depends(require_permission("audit:read")),
) -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT ts, actor_pseudonym, action, target_pseudonym, outcome FROM audit_log
               ORDER BY id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]
