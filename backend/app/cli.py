"""Tareas administrativas de GameMatch: `python -m app.cli <comando>`."""

import argparse
import datetime as dt
import getpass
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.auth import RegisterRequest, create_account
from app.crypto import pseudonymize
from app.db import upsert_vote, write_transaction
from app.recommender import canonical_title

logger = logging.getLogger("gamematch.cli")


def _parse_vote(raw: bytes) -> tuple[str, str, int, str] | None:
    """Devuelve (user_id, título del catálogo, voto, fecha), o None si la línea no es un voto válido."""
    try:
        record = json.loads(raw.decode("utf-8"))
        user_id, game, vote, ts = record["user_id"], record["game"], record["vote"], record["ts"]
        if not (isinstance(user_id, str) and isinstance(game, str) and isinstance(ts, str)):
            return None
        user_id.encode("utf-8")  # un surrogate suelto no se puede seudonimizar
        voted_on = dt.date.fromisoformat(ts[:10]).isoformat()
    except (ValueError, KeyError, TypeError, RecursionError):
        return None
    title = canonical_title(game)
    if title is None or type(vote) is not int or vote not in (1, -1):
        return None
    return user_id, title, vote, voted_on


def migrate_feedback(path: Path) -> tuple[int, int]:
    """Pasa los votos del JSONL antiguo a la base y borra el archivo. Devuelve (migrados, omitidos).

    Se lee en bytes y línea por línea: una línea dañada se omite sin afectar a las demás.
    Si falla la base o falta una clave, no se guarda nada y el archivo se conserva.
    El contenido de las líneas nunca se registra, porque puede traer datos personales.
    """
    if not path.exists():
        return 0, 0
    migrated = skipped = 0
    with write_transaction() as conn:
        for raw in path.read_bytes().split(b"\n"):
            if not raw.strip():
                continue
            parsed = _parse_vote(raw)
            if parsed is None:
                skipped += 1
                continue
            user_id, title, vote, voted_on = parsed
            upsert_vote(conn, pseudonymize(f"legacy:{user_id}"), title, vote, voted_on)
            migrated += 1
    path.unlink()
    logger.info("Migración de votos: %d migrados, %d omitidos", migrated, skipped)
    return migrated, skipped


def send_newsletter() -> int:
    """Envío simulado de novedades. La lista se calcula en cada envío: una revocación rige en el siguiente."""
    from contextlib import closing

    from app import mailer
    from app.audit import record
    from app.consent import newsletter_recipients
    from app.db import connect

    mailer.ensure_available()
    with closing(connect()) as conn:
        recipients = newsletter_recipients(conn)
    for to in recipients:
        mailer.send(
            to,
            "Novedades de GameMatch",
            "Nuevos juegos indie en el catálogo. Puedes dejar de recibir "
            "estos correos revocando la finalidad novedades_correo en «Mi privacidad».",
        )
    record("newsletter:send", "permitido")
    logger.info("Novedades enviadas: %d", len(recipients))
    return len(recipients)


def create_admin(alias: str, password: str) -> str:
    """Único camino para crear un administrador: la API solo crea cuentas `usuario`."""
    valid = RegisterRequest(alias=alias, password=password)  # lanza ValueError si los datos no son válidos
    return create_account(valid.alias, valid.password, role="admin")


def reapply_deletions() -> int:
    """Tras restaurar una copia: vuelve a borrar las cuentas cuya supresión ya se había ejecutado.

    Recalcula el seudónimo de cada cuenta restaurada y la compara con las supresiones cerradas sin motivo
    (ejecutadas). Devuelve cuántas cuentas borró.
    """
    from app.crypto import pseudonymize
    from app.rights import delete_account

    removed = 0
    with write_transaction() as conn:
        deleted = {row["subject"] for row in conn.execute(
            "SELECT subject FROM rights_requests WHERE type = 'supresion' AND status = 'cerrada' AND reason IS NULL"
        )}
        for row in conn.execute("SELECT id FROM accounts").fetchall():
            if pseudonymize(row["id"]) in deleted:
                delete_account(conn, row["id"])
                removed += 1
    logger.info("Supresiones reaplicadas: %d", removed)
    return removed


# ---------- Contención y recuperación (plan de respuesta a incidentes) ----------


def revoke_sessions(account_id: str | None) -> None:
    """Los tokens emitidos antes de ahora dejan de valer: de una cuenta, o de todas con None."""
    import time

    from app.audit import record
    from app.db import set_setting

    now = time.time()
    with write_transaction() as conn:
        if account_id:
            conn.execute("UPDATE accounts SET tokens_not_before = ? WHERE id = ?", (now, account_id))
        else:
            set_setting(conn, "tokens_not_before", repr(now))
        record("admin:revoke_sessions", "permitido", target_id=account_id, conn=conn, resource="account",
               detail="una cuenta" if account_id else "todas")


def disable_account(account_id: str) -> None:
    from app.audit import record

    with write_transaction() as conn:
        conn.execute("UPDATE accounts SET is_active = 0 WHERE id = ?", (account_id,))
        record("admin:disable_account", "permitido", target_id=account_id, conn=conn, resource="account")


def set_legal_hold(account_id: str, on: bool) -> None:
    from app.audit import record

    with write_transaction() as conn:
        conn.execute("UPDATE accounts SET legal_hold = ? WHERE id = ?", (int(on), account_id))
        record("admin:legal_hold", "permitido", target_id=account_id, conn=conn, resource="account",
               detail="activada" if on else "levantada")


def set_read_only(on: bool) -> None:
    from app.audit import record
    from app.db import set_setting

    with write_transaction() as conn:
        set_setting(conn, "read_only", "1" if on else None)
        record("system:read_only", "permitido", conn=conn, detail="activado" if on else "desactivado")


def export_evidence(out_dir: Path) -> tuple[Path, Path]:
    """Copia la base con la API de respaldo de SQLite (consistente aunque la API esté en uso) y escribe su SHA-256."""
    import datetime as dt
    import hashlib
    import sqlite3

    from app.audit import record
    from app.db import connect

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    copy = out_dir / f"evidencia-{stamp}.db"
    source = connect()
    target = sqlite3.connect(copy)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    digest = hashlib.sha256(copy.read_bytes()).hexdigest()
    checksum = out_dir / f"{copy.name}.sha256"
    checksum.write_text(f"{digest}  {copy.name}\n", encoding="utf-8")
    record("system:export_evidence", "permitido", resource="database", detail=f"sha256={digest[:16]}")
    return copy, checksum


def verify_audit() -> bool:
    """Verifica la cadena de la auditoría. Si está rota abre la alerta crítica integridad_auditoria."""
    from app.alerts import open_alert
    from app.audit import record, verify_chain

    ok, bad_id = verify_chain()
    with write_transaction() as conn:
        if not ok:
            open_alert(conn, "integridad_auditoria", "critica", None)
        record("audit:verify", "permitido" if ok else "denegado", conn=conn, resource="audit_log",
               detail=None if ok else f"fila={bad_id}")
    return ok


def purge_retention() -> dict[str, int]:
    """Borra lo vencido según la política de conservación (365 días) y deja constancia en la auditoría."""
    import datetime as dt

    from app.audit import record
    from app.crypto import pseudonymize
    from app.db import set_setting

    old = "julianday(ts) < julianday('now', '-365 days')"
    with write_transaction() as conn:
        anchor = conn.execute(f"SELECT hash FROM audit_log WHERE hash IS NOT NULL AND {old} ORDER BY id DESC LIMIT 1"
                              ).fetchone()
        audit_rows = conn.execute(f"SELECT COUNT(*) FROM audit_log WHERE {old}").fetchone()[0]
        # Antes de purgar queda registrado cuánto se borra y el último hash eliminado, que pasa a ser el ancla.
        record("audit:purge", "permitido", conn=conn, resource="audit_log", detail=f"filas={audit_rows}")
        if anchor:
            set_setting(conn, "audit_anchor", anchor["hash"])
        conn.execute(f"DELETE FROM audit_log WHERE {old}")
        # De consent_events se conserva el último evento de cada finalidad de las cuentas que siguen existiendo.
        alive = {pseudonymize(row["id"]) for row in conn.execute("SELECT id FROM accounts")}
        latest = {row["id"] for row in conn.execute(
            "SELECT MAX(id) AS id, subject FROM consent_events GROUP BY subject, purpose") if row["subject"] in alive}
        stale = [row["id"] for row in conn.execute(f"SELECT id FROM consent_events WHERE {old}")
                 if row["id"] not in latest]
        conn.executemany("DELETE FROM consent_events WHERE id = ?", [(i,) for i in stale])
        requests = conn.execute(
            "DELETE FROM rights_requests WHERE status = 'cerrada'"
            " AND julianday(closed_at) < julianday('now', '-365 days')"
        ).rowcount
        alerts = conn.execute(f"DELETE FROM security_alerts WHERE {old}").rowcount
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        pending = conn.execute(
            """DELETE FROM accounts WHERE pending_guardian = 1 AND id IN
               (SELECT account_id FROM guardian_verifications WHERE expires_at < ?)""", (now,)
        ).rowcount
    return {"audit_log": audit_rows, "consent_events": len(stale), "rights_requests": requests,
            "security_alerts": alerts, "cuentas_pendientes": pending}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate-feedback", help="Migra data/feedback.jsonl a la base")
    migrate.add_argument("--path", type=Path, default=Path("data/feedback.jsonl"))
    admin = commands.add_parser("create-admin", help="Crea una cuenta de administrador")
    admin.add_argument("--alias", required=True)
    commands.add_parser("newsletter", help="Envío simulado de novedades a quienes lo autorizaron")
    commands.add_parser("reapply-deletions", help="Tras restaurar una copia, vuelve a aplicar las supresiones")
    revoke = commands.add_parser("revoke-sessions", help="Invalida los tokens de una cuenta o de todas")
    group = revoke.add_mutually_exclusive_group(required=True)
    group.add_argument("--account")
    group.add_argument("--all", action="store_true")
    disable = commands.add_parser("disable-account", help="Suspende una cuenta comprometida")
    disable.add_argument("account")
    hold = commands.add_parser("legal-hold", help="Activa o levanta la retención por incidente")
    hold.add_argument("account")
    hold.add_argument("--off", action="store_true")
    read_only = commands.add_parser("read-only", help="Modo solo lectura: las escrituras responden 503")
    read_only.add_argument("--off", action="store_true")
    evidence = commands.add_parser("export-evidence", help="Copia la base y su SHA-256 para la investigación")
    evidence.add_argument("--out", type=Path, required=True)
    commands.add_parser("verify-audit", help="Verifica la cadena de integridad de la auditoría")
    commands.add_parser("purge-retention", help="Aplica la política de conservación de 365 días")
    args = parser.parse_args()
    if args.command == "create-admin":
        try:
            create_admin(args.alias, getpass.getpass("Contraseña: "))  # nunca como argumento: quedaría en el historial
        except ValidationError as exc:
            # El mensaje de Pydantic incluye el valor rechazado: aquí sería la contraseña.
            campos = ", ".join(f"{e['loc'][0]}: {e['type']}" for e in exc.errors())
            raise SystemExit(f"Datos inválidos ({campos})") from None
        print("Administrador creado.")
    elif args.command == "newsletter":
        print(f"Novedades enviadas: {send_newsletter()}.")
    elif args.command == "reapply-deletions":
        print(f"Supresiones reaplicadas: {reapply_deletions()}.")
    elif args.command == "revoke-sessions":
        revoke_sessions(None if args.all else args.account)
        print("Sesiones revocadas.")
    elif args.command == "disable-account":
        disable_account(args.account)
        print("Cuenta suspendida.")
    elif args.command == "legal-hold":
        set_legal_hold(args.account, not args.off)
        print("Retención actualizada.")
    elif args.command == "read-only":
        set_read_only(not args.off)
        print("Modo solo lectura " + ("desactivado." if args.off else "activado."))
    elif args.command == "export-evidence":
        copy, checksum = export_evidence(args.out)
        print(f"Evidencia: {copy} ({checksum.read_text(encoding='utf-8').split()[0]})")
    elif args.command == "verify-audit":
        ok = verify_audit()
        print("Auditoría íntegra." if ok else "La cadena de auditoría está rota: se abrió una alerta crítica.")
        raise SystemExit(0 if ok else 1)
    elif args.command == "purge-retention":
        print(f"Purgado: {purge_retention()}")
    else:
        migrated, skipped = migrate_feedback(args.path)
        print(f"Migrados: {migrated}. Omitidos: {skipped}.")


if __name__ == "__main__":
    main()
