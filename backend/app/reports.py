"""Reportes anónimos: totales por juego, sin seudónimos ni fechas."""

import sqlite3
from contextlib import closing

from fastapi import APIRouter, Depends

from app.auth import require_permission
from app.db import connect

MIN_VOTES = 5  # con menos votos, un total podría señalar a una persona

router = APIRouter(prefix="/reports", tags=["reportes"])


def feedback_report(min_votes: int = MIN_VOTES) -> list[dict]:
    if min_votes < MIN_VOTES:
        raise ValueError(f"El umbral no puede ser menor que {MIN_VOTES}")
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT game, COUNT(*) AS votos, SUM(vote = 1) AS positivos FROM feedback
               GROUP BY game HAVING COUNT(*) >= ? ORDER BY votos DESC, game""",
            (min_votes,),
        ).fetchall()
    return [dict(row) for row in rows]


@router.get("/feedback")
def read_feedback_report(_: sqlite3.Row = Depends(require_permission("reports:read"))) -> list[dict]:
    return feedback_report()
