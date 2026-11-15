"""Registro de auditoría: quién hizo qué, sin datos personales y con una cadena de integridad.

Cada fila guarda `hash = HMAC-SHA256(GM_AUDIT_KEY, prev_hash | campos)`. Quien edite el archivo SQLite saltándose los
triggers no puede recalcular la cadena sin la clave, y `verify_chain` lo detecta.
"""

import datetime as dt
import hashlib
import hmac
import sqlite3
import uuid
from contextlib import nullcontext

from app.crypto import get_key, pseudonymize
from app.db import get_setting, write_transaction

ANCHOR_SETTING = "audit_anchor"  # último hash purgado: la verificación parte desde ahí


def _digest(prev_hash: str, row: dict) -> str:
    fields = [prev_hash, row["ts"], row["event_id"], row["actor_pseudonym"] or "", row["action"],
              row["target_pseudonym"] or "", row["resource"] or "", row["outcome"], row["detail"] or ""]
    return hmac.new(get_key("GM_AUDIT_KEY"), "|".join(fields).encode("utf-8"), hashlib.sha256).hexdigest()


def _last_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT hash FROM audit_log WHERE hash IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return row["hash"] if row else (get_setting(conn, ANCHOR_SETTING) or "")


def _append(conn: sqlite3.Connection, action: str, outcome: str, actor: str | None, target: str | None,
            resource: str | None, detail: str | None) -> None:
    row = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "event_id": str(uuid.uuid4()),
        "actor_pseudonym": actor,
        "action": action,
        "target_pseudonym": target,
        "resource": resource,
        "outcome": outcome,
        "detail": detail,
    }
    row["prev_hash"] = _last_hash(conn)
    row["hash"] = _digest(row["prev_hash"], row)
    columns = ", ".join(row)
    conn.execute(f"INSERT INTO audit_log ({columns}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))


def record(
    action: str,
    outcome: str,
    actor_id: str | None = None,
    target_id: str | None = None,
    conn: sqlite3.Connection | None = None,
    detail: str | None = None,
    resource: str | None = None,
) -> None:
    """Guarda el evento y evalúa las reglas de alerta. Los ids se seudonimizan.

    Con `conn` la fila entra en esa transacción: si no se puede auditar, la operación tampoco se aplica. Sin `conn`
    se abre una transacción `BEGIN IMMEDIATE`, para que dos eventos simultáneos no usen el mismo `prev_hash`.
    """
    from app.alerts import evaluate

    actor = pseudonymize(actor_id) if actor_id else None
    target = pseudonymize(target_id) if target_id else None
    get_key("GM_AUDIT_KEY")  # sin clave no se audita, y lo que audita responde 503
    with (nullcontext(conn) if conn is not None else write_transaction()) as tx:
        _append(tx, action, outcome, actor, target, resource, detail)
        for rule in evaluate(tx, action, outcome, actor, target):
            _append(tx, f"alert:{rule}", "permitido", None, None, "security_alerts", None)


def verify_chain() -> tuple[bool, int | None]:
    """Recorre la cadena. Devuelve (True, None) si está íntegra, o (False, id de la primera fila alterada)."""
    with write_transaction() as conn:
        expected_prev = get_setting(conn, ANCHOR_SETTING) or ""
        first = True
        for row in conn.execute("SELECT * FROM audit_log WHERE hash IS NOT NULL ORDER BY id").fetchall():
            data = dict(row)
            if (not first or expected_prev) and data["prev_hash"] != expected_prev:
                return False, data["id"]
            if not hmac.compare_digest(_digest(data["prev_hash"], data), data["hash"]):
                return False, data["id"]
            expected_prev, first = data["hash"], False
    return True, None
