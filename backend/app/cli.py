"""Tareas administrativas de GameMatch: `python -m app.cli <comando>`."""

import argparse
import json
import logging
from pathlib import Path

from app.crypto import pseudonymize
from app.db import connect, upsert_vote

logger = logging.getLogger("gamematch.cli")


def _parse_vote(line: str) -> tuple[str, str, int, str] | None:
    """Devuelve (user_id, game, vote, fecha) o None si la línea no es un voto válido."""
    try:
        record = json.loads(line)
        user_id, game, vote, ts = record["user_id"], record["game"], record["vote"], record["ts"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not (isinstance(user_id, str) and isinstance(game, str) and isinstance(ts, str)) or vote not in (1, -1):
        return None
    return user_id, game, vote, ts[:10]


def migrate_feedback(path: Path) -> tuple[int, int]:
    """Pasa los votos del JSONL antiguo a la base y borra el archivo. Devuelve (migrados, omitidos).

    Todo ocurre en una transacción: si algo falla, no se guarda nada y el archivo se conserva.
    El contenido de las líneas nunca se registra, porque puede traer datos personales.
    """
    if not path.exists():
        return 0, 0
    migrated = skipped = 0
    conn = connect()
    try:
        with conn:
            for line in path.read_text(encoding="utf-8").splitlines():
                parsed = _parse_vote(line)
                if parsed is None:
                    skipped += 1
                    continue
                user_id, game, vote, voted_on = parsed
                upsert_vote(conn, pseudonymize(f"legacy:{user_id}"), game, vote, voted_on)
                migrated += 1
    finally:
        conn.close()
    path.unlink()
    logger.info("Migración de votos: %d migrados, %d omitidos", migrated, skipped)
    return migrated, skipped


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate-feedback", help="Migra data/feedback.jsonl a la base")
    migrate.add_argument("--path", type=Path, default=Path("data/feedback.jsonl"))
    args = parser.parse_args()
    migrated, skipped = migrate_feedback(args.path)
    print(f"Migrados: {migrated}. Omitidos: {skipped}.")


if __name__ == "__main__":
    main()
