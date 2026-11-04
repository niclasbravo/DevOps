"""Registro de auditoría: quién hizo qué, sin datos personales."""

import datetime as dt
from contextlib import closing

from app.crypto import pseudonymize
from app.db import connect


def record(action: str, outcome: str, actor_id: str | None = None, target_id: str | None = None) -> None:
    """Guarda el evento. Los ids se seudonimizan: leer la auditoría no revela quién es quién."""
    actor = pseudonymize(actor_id) if actor_id else None
    target = pseudonymize(target_id) if target_id else None
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO audit_log (ts, actor_pseudonym, action, target_pseudonym, outcome) VALUES (?, ?, ?, ?, ?)",
            (ts, actor, action, target, outcome),
        )
