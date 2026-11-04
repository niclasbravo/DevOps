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


def create_admin(alias: str, password: str) -> str:
    """Único camino para crear un administrador: la API solo crea cuentas `usuario`."""
    valid = RegisterRequest(alias=alias, password=password)  # lanza ValueError si los datos no son válidos
    return create_account(valid.alias, valid.password, role="admin")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate-feedback", help="Migra data/feedback.jsonl a la base")
    migrate.add_argument("--path", type=Path, default=Path("data/feedback.jsonl"))
    admin = commands.add_parser("create-admin", help="Crea una cuenta de administrador")
    admin.add_argument("--alias", required=True)
    args = parser.parse_args()
    if args.command == "create-admin":
        try:
            create_admin(args.alias, getpass.getpass("Contraseña: "))  # nunca como argumento: quedaría en el historial
        except ValidationError as exc:
            # El mensaje de Pydantic incluye el valor rechazado: aquí sería la contraseña.
            campos = ", ".join(f"{e['loc'][0]}: {e['type']}" for e in exc.errors())
            raise SystemExit(f"Datos inválidos ({campos})") from None
        print("Administrador creado.")
    else:
        migrated, skipped = migrate_feedback(args.path)
        print(f"Migrados: {migrated}. Omitidos: {skipped}.")


if __name__ == "__main__":
    main()
