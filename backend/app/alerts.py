"""Detección de eventos sospechosos.

Las reglas se evalúan al registrar el evento que las dispara, en la misma transacción. Una alerta `abierta` es una
alerta; `en_analisis`, un evento sospechoso; `incidente`, un incidente confirmado que activa el plan de respuesta.
"""

import datetime as dt
import logging
import sqlite3
from contextlib import closing
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.auth import require_permission
from app.db import connect, write_transaction

logger = logging.getLogger("gamematch.alerts")
router = APIRouter(prefix="/admin/alerts", tags=["alertas"])

Status = Literal["abierta", "en_analisis", "descartada", "incidente"]


class AlertUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status


def _count(conn: sqlite3.Connection, where: str, window: str, *args) -> int:
    sql = f"SELECT COUNT(*) FROM audit_log WHERE {where} AND julianday(ts) >= julianday('now', ?)"
    return conn.execute(sql, (*args, window)).fetchone()[0]


def open_alert(conn: sqlite3.Connection, rule: str, severity: str, subject: str | None) -> bool:
    """Abre la alerta si no hay otra igual sin cerrar para el mismo seudónimo. Devuelve si la abrió."""
    duplicate = conn.execute(
        """SELECT 1 FROM security_alerts WHERE rule = ? AND subject IS ? AND status IN ('abierta', 'en_analisis')""",
        (rule, subject),
    ).fetchone()
    if duplicate:
        return False
    conn.execute(
        "INSERT INTO security_alerts (ts, rule, severity, subject) VALUES (?, ?, ?, ?)",
        (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), rule, severity, subject),
    )
    logger.warning("Alerta de seguridad: regla=%s severidad=%s", rule, severity)  # sin datos personales
    return True


def evaluate(conn: sqlite3.Connection, action: str, outcome: str, actor: str | None, target: str | None) -> list[str]:
    """Reglas de la sección 6 del diseño. Devuelve las reglas que abrieron una alerta nueva."""
    candidates: list[tuple[str, str, str | None]] = []
    if action == "auth:login" and outcome == "denegado":
        if target and _count(conn, "action = 'auth:login' AND outcome = 'denegado' AND target_pseudonym = ?",
                             "-15 minutes", target) >= 5:
            candidates.append(("login_fallido_reiterado", "media", target))
        if _count(conn, "action = 'auth:login' AND outcome = 'denegado'", "-5 minutes") >= 20:
            candidates.append(("posible_fuerza_bruta", "alta", None))
    if action == "data:export" and outcome == "permitido" and actor:
        if _count(conn, "action = 'data:export' AND actor_pseudonym = ?", "-24 hours", actor) > 3:
            candidates.append(("exportacion_anomala", "media", actor))
    if outcome == "denegado" and actor:
        if _count(conn, "outcome = 'denegado' AND actor_pseudonym = ?", "-10 minutes", actor) >= 10:
            candidates.append(("acceso_denegado_reiterado", "media", actor))
    if action == "accounts:reveal_email" and outcome == "permitido" and actor:
        if _count(conn, "action = 'accounts:reveal_email' AND actor_pseudonym = ?", "-1 hours", actor) > 5:
            candidates.append(("revelacion_masiva", "alta", actor))
    return [rule for rule, severity, subject in candidates if open_alert(conn, rule, severity, subject)]


@router.get("")
def list_alerts(_: sqlite3.Row = Depends(require_permission("alerts:read"))) -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM security_alerts ORDER BY id DESC").fetchall()
    return [{"id": r["id"], "fecha": r["ts"], "regla": r["rule"], "severidad": r["severity"],
             "sujeto": r["subject"], "estado": r["status"]} for r in rows]


@router.patch("/{alert_id}")
def update_alert(alert_id: int, req: AlertUpdate, admin: sqlite3.Row = Depends(require_permission("alerts:manage"))):
    from app.audit import record

    with write_transaction() as conn:
        if conn.execute("SELECT 1 FROM security_alerts WHERE id = ?", (alert_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Alerta no encontrada")
        conn.execute("UPDATE security_alerts SET status = ? WHERE id = ?", (req.status, alert_id))
        record("alert:update", "permitido", actor_id=admin["id"], conn=conn, resource=f"alert:{alert_id}",
               detail=f"estado={req.status}")
    return {"status": "ok"}
