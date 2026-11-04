"""El reporte solo entrega agregados anónimos."""

from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.db import connect, upsert_vote
from app.main import app
from app.reports import feedback_report

client = TestClient(app)


def _votos(game: str, positivos: int, negativos: int = 0) -> None:
    with closing(connect()) as conn, conn:
        for n in range(positivos + negativos):
            upsert_vote(conn, f"{game}-{n}", game, 1 if n < positivos else -1, "2026-11-04")


def test_juego_con_cuatro_votos_se_omite():
    _votos("Hades", 4)
    assert feedback_report() == []


def test_juego_con_cinco_votos_aparece():
    _votos("Hades", 3, 2)
    assert feedback_report() == [{"game": "Hades", "votos": 5, "positivos": 3}]


def test_reporte_no_tiene_seudonimos_ni_fechas(nueva_cuenta):
    _votos("Hades", 5)
    _, headers = nueva_cuenta("analista")
    resp = client.get("/reports/feedback", headers=headers)
    (fila,) = resp.json()
    assert set(fila) == {"game", "votos", "positivos"}
    assert "Hades-0" not in resp.text and "2026-11-04" not in resp.text


def test_reporte_ordena_por_cantidad_de_votos():
    _votos("Hades", 5)
    _votos("Celeste", 7)
    assert [fila["game"] for fila in feedback_report()] == ["Celeste", "Hades"]


@pytest.mark.parametrize("umbral", [0, -1])
def test_el_umbral_no_se_puede_desactivar(umbral):
    _votos("Hades", 1)
    with pytest.raises(ValueError):
        feedback_report(min_votes=umbral)


def test_el_endpoint_no_permite_bajar_el_umbral(nueva_cuenta):
    _votos("Hades", 1)
    _, headers = nueva_cuenta("analista")
    assert client.get("/reports/feedback", headers=headers, params={"min_votes": 1}).json() == []
