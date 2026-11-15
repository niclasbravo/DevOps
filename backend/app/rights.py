"""Ejercicio de derechos: acceso y portabilidad, rectificación y supresión.

Cada solicitud recorre recepción, verificación, evaluación, ejecución o respuesta fundada, y cierre. La tabla
`rights_requests` guarda estados, fechas y un motivo de una lista cerrada, nunca los datos pedidos: así probar que
se atendió en plazo no obliga a conservar datos personales.
"""

import datetime as dt
import sqlite3
import uuid
from contextlib import closing
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.audit import record
from app.auth import (
    ALIAS_PATTERN,
    EMAIL_PATTERN,
    PASSWORD_MAX,
    _is_locked,
    _register_failure,
    is_last_admin,
    require_permission,
)
from app.consent import has_consent, history, revoke_all
from app.crypto import decrypt_email, encrypt_email, pseudonymize, verify_password
from app.db import connect, write_transaction

DUE_DAYS = 30  # Ley 21.719, art. 11: 30 días corridos, prorrogables una vez por otros 30
EXPORT_FORMAT = "gamematch-export/1"

RequestType = Literal["acceso", "rectificacion", "supresion"]
Reason = Literal["ultimo_admin", "conservacion_por_incidente", "dato_invalido", "alias_no_disponible",
                 "identidad_no_verificada"]

router = APIRouter(tags=["derechos"])


class Rectification(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no se puede rectificar el rol, el id ni nada no previsto

    alias: str | None = Field(default=None, pattern=ALIAS_PATTERN)
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)
    is_child: bool | None = None


class RightsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: RequestType
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX)
    changes: Rectification | None = None

    @model_validator(mode="after")
    def _changes_only_for_rectification(self):
        has_changes = self.changes is not None and bool(self.changes.model_fields_set)
        if self.type == "rectificacion" and not has_changes:
            raise ValueError("Una rectificación necesita al menos un cambio")
        if self.type != "rectificacion" and self.changes is not None:
            raise ValueError("Solo la rectificación lleva cambios")
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ejecutar", "rechazar", "prorrogar"]
    reason: Reason | None = None

    @model_validator(mode="after")
    def _reject_needs_reason(self):
        if self.action == "rechazar" and self.reason is None:
            raise ValueError("Rechazar exige un motivo de la lista")
        return self


class LegalHold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_hold: bool


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _ts(moment: dt.datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _open_request(conn: sqlite3.Connection, subject: str, kind: str) -> tuple[str, dt.datetime]:
    received = _now()
    request_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO rights_requests (id, subject, type, status, received_at, due_on)
           VALUES (?, ?, ?, 'recibida', ?, ?)""",
        (request_id, subject, kind, _ts(received), (received.date() + dt.timedelta(days=DUE_DAYS)).isoformat()),
    )
    return request_id, received


def _set(conn: sqlite3.Connection, request_id: str, status: str, reason: str | None = None) -> None:
    """Avanza el estado y fija la fecha que corresponde a ese paso."""
    now = _ts(_now())
    column = {"verificada": "verified_at", "en_evaluacion": "verified_at", "cerrada": "closed_at"}.get(status)
    conn.execute("UPDATE rights_requests SET status = ?, reason = COALESCE(?, reason) WHERE id = ?",
                 (status, reason, request_id))
    if status in ("ejecutada", "rechazada"):
        conn.execute("UPDATE rights_requests SET decided_at = ? WHERE id = ?", (now, request_id))
    elif column:
        conn.execute(f"UPDATE rights_requests SET {column} = COALESCE({column}, ?) WHERE id = ?", (now, request_id))


def _finish(conn: sqlite3.Connection, request_id: str, reason: str | None = None) -> None:
    """Ejecutada (sin motivo) o rechazada (con motivo), y luego cerrada."""
    _set(conn, request_id, "rechazada" if reason else "ejecutada", reason)
    _set(conn, request_id, "cerrada")


def _request_view(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "tipo": row["type"],
        "estado": row["status"],
        "motivo": row["reason"],
        "recibida": row["received_at"],
        "verificada": row["verified_at"],
        "decidida": row["decided_at"],
        "cerrada": row["closed_at"],
        "vence": row["due_on"],
        "prorrogada": bool(row["extended"]),
    }


def build_export(conn: sqlite3.Connection, account: sqlite3.Row) -> dict:
    """Solo datos del titular: sin hash, id, seudónimos, contadores ni auditoría (registro interno con terceros)."""
    subject = pseudonymize(account["id"])
    email = account["email_encrypted"]
    votes = conn.execute(
        "SELECT game, vote, voted_on FROM feedback WHERE pseudonym = ? ORDER BY id", (subject,)
    ).fetchall()
    requests = conn.execute(
        "SELECT type, status, received_at FROM rights_requests WHERE subject = ? ORDER BY received_at", (subject,)
    ).fetchall()
    return {
        "formato": EXPORT_FORMAT,
        "generado": _ts(_now()),
        "cuenta": {
            "alias": account["alias"],
            "rol": account["role"],
            "creada": account["created_on"],
            "correo": decrypt_email(email) if email else None,
            "menor_de_14": bool(account["is_child"]),
        },
        "consentimientos": history(conn, subject),
        "votos": [{"juego": v["game"], "voto": v["vote"], "fecha": v["voted_on"]} for v in votes],
        "solicitudes": [{"tipo": r["type"], "estado": r["status"], "recibida": r["received_at"]} for r in requests],
    }


def delete_account(conn: sqlite3.Connection, account_id: str) -> None:
    """Supresión en una transacción: votos, consentimientos vigentes y la cuenta (con su correo cifrado)."""
    subject = pseudonymize(account_id)
    conn.execute("DELETE FROM feedback WHERE pseudonym = ?", (subject,))
    revoke_all(conn, subject, "sistema")
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def _respond(status: int, request_id: str, state: str, detail: str | None = None) -> JSONResponse:
    content = {"solicitud": request_id, "estado": state}
    if detail:
        content["detail"] = detail
    return JSONResponse(status_code=status, content=content)


@router.post("/me/requests")
def submit_request(req: RightsRequest, account: sqlite3.Row = Depends(require_permission("rights:request"))):
    subject = pseudonymize(account["id"])
    verified = not _is_locked(account) and verify_password(account["password_hash"], req.current_password)

    if not verified:
        with write_transaction() as conn:
            request_id, _ = _open_request(conn, subject, req.type)
            if not _is_locked(account):
                _register_failure(conn, account["id"])  # cuenta para el bloqueo de 5 intentos
            _finish(conn, request_id, "identidad_no_verificada")
            record(f"rights:{req.type}", "denegado", actor_id=account["id"], conn=conn)
        return _respond(403, request_id, "cerrada", "No se pudo verificar tu identidad")

    if req.type == "acceso":
        with write_transaction() as conn:
            request_id, _ = _open_request(conn, subject, "acceso")
            _set(conn, request_id, "verificada")
            _finish(conn, request_id)
            record("data:export", "permitido", actor_id=account["id"], conn=conn)
            export = build_export(conn, account)
        return JSONResponse(export, headers={"Content-Disposition": 'attachment; filename="gamematch-mis-datos.json"',
                                             "X-Request-Id": request_id})

    if req.type == "rectificacion":
        return _rectify(account, subject, req.changes)
    return _delete(account, subject)


def _rectify(account: sqlite3.Row, subject: str, changes: Rectification):
    fields = sorted(changes.model_fields_set)
    new_email = encrypt_email(changes.email) if changes.email else None
    with write_transaction() as conn:
        request_id, _ = _open_request(conn, subject, "rectificacion")
        _set(conn, request_id, "verificada")
        if "is_child" in fields:
            if changes.is_child == bool(account["is_child"]):
                _finish(conn, request_id, "dato_invalido")
                record("data:rectify", "denegado", actor_id=account["id"], conn=conn, detail="campos=is_child")
                return _respond(409, request_id, "cerrada", "El dato ya tiene ese valor")
            # Pasar de menor a mayor saltaría el control del representante: lo decide un admin. El valor no se guarda.
            _set(conn, request_id, "en_evaluacion")
            record("data:rectify", "permitido", actor_id=account["id"], conn=conn, detail="campos=is_child pendiente")
            return _respond(202, request_id, "en_evaluacion")
        if "email" in fields and changes.email and not has_consent(conn, subject, "correo_recuperacion"):
            _finish(conn, request_id, "dato_invalido")
            record("data:rectify", "denegado", actor_id=account["id"], conn=conn, detail="campos=email")
            return _respond(409, request_id, "cerrada", "Primero acepta la finalidad correo_recuperacion")
        if changes.alias is not None:
            taken = conn.execute("SELECT 1 FROM accounts WHERE alias = ? AND id != ?",
                                 (changes.alias, account["id"])).fetchone()
            if taken:
                _finish(conn, request_id, "alias_no_disponible")
                record("data:rectify", "denegado", actor_id=account["id"], conn=conn, detail="campos=alias")
                return _respond(409, request_id, "cerrada", "Ese alias no está disponible")
            conn.execute("UPDATE accounts SET alias = ? WHERE id = ?", (changes.alias, account["id"]))
        if "email" in fields:
            conn.execute("UPDATE accounts SET email_encrypted = ? WHERE id = ?", (new_email, account["id"]))
        _finish(conn, request_id)
        # Qué campos cambiaron, nunca sus valores anterior ni nuevo.
        record("data:rectify", "permitido", actor_id=account["id"], conn=conn, detail="campos=" + ",".join(fields))
    return _respond(200, request_id, "cerrada")


def _delete(account: sqlite3.Row, subject: str):
    with write_transaction() as conn:
        request_id, _ = _open_request(conn, subject, "supresion")
        _set(conn, request_id, "verificada")
        if is_last_admin(conn, account["id"]):
            _finish(conn, request_id, "ultimo_admin")
            record("data:delete", "denegado", actor_id=account["id"], conn=conn)
            return _respond(409, request_id, "cerrada", "No se puede dejar el sistema sin administradores")
        if account["legal_hold"]:
            _set(conn, request_id, "en_evaluacion", "conservacion_por_incidente")
            record("data:delete", "denegado", actor_id=account["id"], conn=conn, detail="retencion por incidente")
            return _respond(202, request_id, "en_evaluacion")
        record("data:delete", "permitido", actor_id=account["id"], conn=conn)
        delete_account(conn, account["id"])
        _finish(conn, request_id)
    return _respond(200, request_id, "cerrada")


@router.get("/me/requests")
def my_requests(account: sqlite3.Row = Depends(require_permission("rights:request"))) -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM rights_requests WHERE subject = ? ORDER BY received_at",
                            (pseudonymize(account["id"]),)).fetchall()
    return [_request_view(row) for row in rows]


# ---------- Administración ----------


def _account_by_subject(conn: sqlite3.Connection, subject: str) -> sqlite3.Row | None:
    """El seudónimo no se puede revertir: se busca la cuenta cuyo id da ese HMAC."""
    for row in conn.execute("SELECT * FROM accounts").fetchall():
        if pseudonymize(row["id"]) == subject:
            return row
    return None


@router.get("/admin/requests")
def list_requests(
    all_: bool = Query(default=False, alias="todas"),
    admin: sqlite3.Row = Depends(require_permission("requests:read")),
) -> list[dict]:
    today = _now().date().isoformat()
    with closing(connect()) as conn:
        sql = "SELECT * FROM rights_requests"
        if not all_:
            sql += " WHERE status NOT IN ('cerrada')"
        rows = conn.execute(sql + " ORDER BY due_on").fetchall()
    record("requests:read", "permitido", actor_id=admin["id"])
    return [{**_request_view(r), "vencida": r["status"] != "cerrada" and r["due_on"] < today} for r in rows]


@router.post("/admin/requests/{request_id}/decision")
def decide(request_id: str, req: Decision, admin: sqlite3.Row = Depends(require_permission("requests:manage"))):
    with write_transaction() as conn:
        row = conn.execute("SELECT * FROM rights_requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        if row["status"] != "en_evaluacion":
            raise HTTPException(status_code=409, detail="Solo se deciden solicitudes en evaluación")

        if req.action == "prorrogar":
            if row["extended"]:
                raise HTTPException(status_code=409, detail="La prórroga se puede usar una sola vez")
            due = (dt.date.fromisoformat(row["due_on"]) + dt.timedelta(days=DUE_DAYS)).isoformat()
            conn.execute("UPDATE rights_requests SET due_on = ?, extended = 1 WHERE id = ?", (due, request_id))
            record("requests:extend", "permitido", actor_id=admin["id"], conn=conn)
            return {"solicitud": request_id, "estado": "en_evaluacion", "vence": due}

        if req.action == "rechazar":
            _finish(conn, request_id, req.reason)
            record("requests:reject", "permitido", actor_id=admin["id"], conn=conn, detail=f"motivo={req.reason}")
            return {"solicitud": request_id, "estado": "cerrada"}

        target = _account_by_subject(conn, row["subject"])
        if target is None:
            raise HTTPException(status_code=409, detail="La cuenta ya no existe")
        if row["type"] == "supresion":
            if target["legal_hold"]:
                raise HTTPException(status_code=409, detail="Primero hay que levantar la retención por incidente")
            record("data:delete", "permitido", actor_id=admin["id"], target_id=target["id"], conn=conn)
            delete_account(conn, target["id"])
        elif row["type"] == "rectificacion":
            # La única rectificación que espera evaluación es el cambio de edad: aprobarla invierte is_child.
            conn.execute("UPDATE accounts SET is_child = 1 - is_child WHERE id = ?", (target["id"],))
            record("data:rectify", "permitido", actor_id=admin["id"], target_id=target["id"], conn=conn,
                   detail="campos=is_child")
        _finish(conn, request_id)
    return {"solicitud": request_id, "estado": "cerrada"}


@router.patch("/admin/accounts/{account_id}/legal-hold")
def set_legal_hold(
    account_id: str, req: LegalHold, admin: sqlite3.Row = Depends(require_permission("accounts:manage"))
):
    """Retención por incidente: mientras dure, la supresión de esa cuenta espera a un admin."""
    with write_transaction() as conn:
        if conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Cuenta no encontrada")
        conn.execute("UPDATE accounts SET legal_hold = ? WHERE id = ?", (int(req.legal_hold), account_id))
        record("accounts:legal_hold", "permitido", actor_id=admin["id"], target_id=account_id, conn=conn,
               detail="activada" if req.legal_hold else "levantada")
    return {"status": "ok"}
