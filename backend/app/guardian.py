"""Confirmación del representante legal para cuentas de menores de 14 años (Ley 21.719, art. 16 quáter).

La cuenta se crea inactiva. El representante recibe un enlace de un solo uso, que vence en 72 horas; solo se
guarda el SHA-256 del token y nunca el correo del representante. Al confirmar, la cuenta se activa y cada
finalidad que el representante elige queda registrada con `actor = 'representante'`.
"""

import datetime as dt
import hashlib
import os
import secrets
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.audit import record
from app.consent import Purpose, grant
from app.crypto import pseudonymize
from app.db import PURPOSES, write_transaction

TOKEN_HOURS = 72
INVALID_LINK = "Enlace inválido o vencido"

router = APIRouter(prefix="/guardian", tags=["representante"])


class GuardianConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=200)
    declares_legal_representative: Literal[True]  # sin la declaración no se activa nada
    consents: list[Purpose] = Field(default_factory=list)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def create_verification(conn: sqlite3.Connection, account_id: str) -> str:
    """Guarda solo el hash del token y devuelve el token, que viaja únicamente en el correo."""
    token = secrets.token_urlsafe(32)
    expires = (_now() + dt.timedelta(hours=TOKEN_HOURS)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO guardian_verifications (account_id, token_hash, expires_at) VALUES (?, ?, ?)",
        (account_id, _hash(token), expires),
    )
    return token


def confirmation_link(token: str) -> str:
    # El token va después de `#`: el navegador no lo envía al servidor, así que no aparece en logs de acceso.
    base = os.environ.get("GM_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/guardian.html#{token}"


def guardian_email_body(alias: str, link: str) -> str:
    return (
        f"Alguien creó la cuenta «{alias}» en GameMatch e indicó que es menor de 14 años y que usted es su "
        "representante legal.\n\nGameMatch guarda el alias, una contraseña protegida y los votos sobre juegos. "
        f"La cuenta no se activará sin su confirmación:\n\n{link}\n\n"
        f"El enlace vence en {TOKEN_HOURS} horas. Si no reconoce esta solicitud, ignore este mensaje y la cuenta "
        "se eliminará."
    )


@router.post("/confirm")
def confirm(req: GuardianConfirmation) -> dict:
    not_allowed = [p for p in req.consents if not PURPOSES[p]["allowed_for_children"]]
    if not_allowed:
        raise HTTPException(status_code=400, detail="Esa finalidad no está disponible para menores de 14 años")
    with write_transaction() as conn:
        row = conn.execute(
            """SELECT v.account_id, v.expires_at FROM guardian_verifications v
               JOIN accounts a ON a.id = v.account_id
               WHERE v.token_hash = ? AND a.pending_guardian = 1""",
            (_hash(req.token),),
        ).fetchone()
        valid = row is not None and dt.datetime.fromisoformat(row["expires_at"]) > _now()
        if not valid:
            record("auth:guardian_confirm", "denegado", conn=conn)
        else:
            account_id = row["account_id"]
            conn.execute("UPDATE accounts SET is_active = 1, pending_guardian = 0 WHERE id = ?", (account_id,))
            conn.execute("DELETE FROM guardian_verifications WHERE account_id = ?", (account_id,))
            subject = pseudonymize(account_id)
            for purpose in dict.fromkeys(req.consents):
                grant(conn, subject, purpose, "representante")
            record("auth:guardian_confirm", "permitido", target_id=account_id, conn=conn)
    if not valid:
        # El mismo mensaje para un token vencido, usado o inventado: no revela cuál de los tres es.
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    return {"status": "cuenta activada"}
