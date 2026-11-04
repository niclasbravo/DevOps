"""Registro de auditoría: quién hizo qué, sin datos personales."""

import datetime as dt
import sqlite3
from contextlib import closing

from app.crypto import pseudonymize
from app.db import connect


def record(
    action: str,
    outcome: str,
    actor_id: str | None = None,
    target_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Guarda el evento. Los ids se seudonimizan: leer la auditoría no revela quién es quién.

    Con `conn` la fila entra en esa transacción: si no se puede auditar, la operación tampoco se aplica.
    """
    actor = pseudonymize(actor_id) if actor_id else None
    target = pseudonymize(target_id) if target_id else None
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    sql = "INSERT INTO audit_log (ts, actor_pseudonym, action, target_pseudonym, outcome) VALUES (?, ?, ?, ?, ?)"
    if conn is not None:
        conn.execute(sql, (ts, actor, action, target, outcome))
        return
    with closing(connect()) as own, own:
        own.execute(sql, (ts, actor, action, target, outcome))
