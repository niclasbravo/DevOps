"""Consentimiento por finalidad.

Cada otorgamiento o revocación es un evento nuevo en `consent_events`, que no se puede reescribir: el estado
vigente es el último evento, y el historial sale de la misma tabla. Un consentimiento vale solo para su
finalidad y para la versión vigente de su texto; `has_consent` es la única función que lo decide.
"""

import datetime as dt
import sqlite3
from contextlib import closing
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from app.audit import record
from app.auth import require_permission
from app.crypto import pseudonymize
from app.db import PURPOSES, connect, write_transaction

Purpose = Literal["correo_recuperacion", "votos_analitica", "novedades_correo"]
Actor = Literal["titular", "representante", "sistema"]

router = APIRouter(tags=["consentimiento"])

# Seudónimos cuyo último evento para la finalidad es "otorgado" y corresponde a la versión vigente.
# Los procesos filtran con esta subconsulta en SQL, así que ningún cliente puede saltarse el filtro.
VALID_SUBJECTS_SQL = """
SELECT e.subject FROM consent_events e JOIN consent_purposes p ON p.code = e.purpose
WHERE e.purpose = :purpose
  AND e.id = (SELECT MAX(id) FROM consent_events WHERE subject = e.subject AND purpose = e.purpose)
  AND e.status = 'otorgado' AND e.text_version = p.version
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _latest(conn: sqlite3.Connection, subject: str, purpose: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT e.status, e.text_version, e.actor, e.ts, p.version AS current_version
           FROM consent_events e JOIN consent_purposes p ON p.code = e.purpose
           WHERE e.subject = ? AND e.purpose = ? ORDER BY e.id DESC LIMIT 1""",
        (subject, purpose),
    ).fetchone()


def has_consent(conn: sqlite3.Connection, subject: str, purpose: str) -> bool:
    latest = _latest(conn, subject, purpose)
    return latest is not None and latest["status"] == "otorgado" and latest["text_version"] == latest["current_version"]


def _append(conn: sqlite3.Connection, subject: str, purpose: str, status: str, actor: str) -> None:
    version = conn.execute("SELECT version FROM consent_purposes WHERE code = ?", (purpose,)).fetchone()["version"]
    conn.execute(
        "INSERT INTO consent_events (subject, purpose, text_version, status, actor, ts) VALUES (?, ?, ?, ?, ?, ?)",
        (subject, purpose, version, status, actor, _now()),
    )


def grant(conn: sqlite3.Connection, subject: str, purpose: str, actor: Actor) -> bool:
    """Otorga la versión vigente. Devuelve False si ya estaba vigente: no se duplica el evento."""
    if has_consent(conn, subject, purpose):
        return False
    _append(conn, subject, purpose, "otorgado", actor)
    return True


def revoke(conn: sqlite3.Connection, subject: str, purpose: str, actor: Actor) -> bool:
    """Revoca. Devuelve False si no había nada otorgado que revocar."""
    latest = _latest(conn, subject, purpose)
    if latest is None or latest["status"] == "revocado":
        return False
    _append(conn, subject, purpose, "revocado", actor)
    return True


def revoke_all(conn: sqlite3.Connection, subject: str, actor: Actor) -> list[str]:
    return [purpose for purpose in PURPOSES if revoke(conn, subject, purpose, actor)]


def apply_revocation_effects(conn: sqlite3.Connection, account_id: str, purpose: str) -> None:
    """Lo que la revocación cambia en otros datos, en la misma transacción que el evento."""
    if purpose == "correo_recuperacion":
        conn.execute("UPDATE accounts SET email_encrypted = NULL WHERE id = ?", (account_id,))


def current_state(conn: sqlite3.Connection, subject: str) -> list[dict]:
    state = []
    for row in conn.execute("SELECT code, version FROM consent_purposes ORDER BY code").fetchall():
        latest = _latest(conn, subject, row["code"])
        state.append(
            {
                "finalidad": row["code"],
                "version_vigente": row["version"],
                "estado": "otorgado" if has_consent(conn, subject, row["code"]) else "no_otorgado",
                "version_aceptada": latest["text_version"] if latest and latest["status"] == "otorgado" else None,
                "desde": latest["ts"] if latest else None,
            }
        )
    return state


def history(conn: sqlite3.Connection, subject: str) -> list[dict]:
    rows = conn.execute(
        """SELECT purpose AS finalidad, text_version AS version, status AS estado, actor, ts AS fecha
           FROM consent_events WHERE subject = ? ORDER BY id""",
        (subject,),
    ).fetchall()
    return [dict(row) for row in rows]


@router.get("/consents/purposes")
def list_purposes() -> list[dict]:
    """Público: el texto que se informa debe poder leerse antes de crear la cuenta."""
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT code, version, text, allowed_for_children FROM consent_purposes ORDER BY code"
        ).fetchall()
    return [{**dict(row), "allowed_for_children": bool(row["allowed_for_children"])} for row in rows]


@router.get("/me/consents")
def read_my_consents(account: sqlite3.Row = Depends(require_permission("consent:manage"))) -> list[dict]:
    with closing(connect()) as conn:
        return current_state(conn, pseudonymize(account["id"]))


@router.get("/me/consents/history")
def read_my_consent_history(account: sqlite3.Row = Depends(require_permission("consent:manage"))) -> list[dict]:
    with closing(connect()) as conn:
        return history(conn, pseudonymize(account["id"]))


@router.post("/me/consents/{purpose}")
def grant_consent(purpose: Purpose, account: sqlite3.Row = Depends(require_permission("consent:manage"))) -> dict:
    if account["is_child"]:
        # Art. 16 quáter, Ley 21.719: bajo 14 años consiente el representante, no la persona.
        record("consent:grant", "denegado", actor_id=account["id"])
        raise HTTPException(status_code=403, detail="Esta finalidad la autoriza tu representante legal")
    subject = pseudonymize(account["id"])
    with write_transaction() as conn:
        changed = grant(conn, subject, purpose, "titular")
        if changed:
            record("consent:grant", "permitido", actor_id=account["id"], conn=conn)
    return {"finalidad": purpose, "estado": "otorgado"}


@router.delete("/me/consents/{purpose}")
def revoke_consent(purpose: Purpose, account: sqlite3.Row = Depends(require_permission("consent:manage"))) -> dict:
    """Revocar es tan fácil como otorgar, y una cuenta de menor también puede hacerlo: retirar siempre protege."""
    subject = pseudonymize(account["id"])
    with write_transaction() as conn:
        changed = revoke(conn, subject, purpose, "titular")
        if changed:
            apply_revocation_effects(conn, account["id"], purpose)
            record("consent:revoke", "permitido", actor_id=account["id"], conn=conn)
    return {"finalidad": purpose, "estado": "revocado"}


def newsletter_recipients(conn: sqlite3.Connection) -> list[str]:
    """Correos que pueden recibir novedades: cuentas activas, no de menores, con `novedades_correo` vigente.

    Tener un correo guardado para recuperar la cuenta no basta: esa es otra finalidad.
    """
    from app.crypto import decrypt_email

    valid = {row["subject"] for row in conn.execute(VALID_SUBJECTS_SQL, {"purpose": "novedades_correo"})}
    rows = conn.execute(
        """SELECT id, email_encrypted FROM accounts
           WHERE email_encrypted IS NOT NULL AND is_active = 1 AND is_child = 0"""
    ).fetchall()
    return [decrypt_email(row["email_encrypted"]) for row in rows if pseudonymize(row["id"]) in valid]
