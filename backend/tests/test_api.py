"""Pruebas de la API HTTP."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_responde_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_listado_de_juegos_no_vacio():
    resp = client.get("/games")
    assert resp.status_code == 200
    assert len(resp.json()) > 0


def test_recomendaciones_por_tags():
    resp = client.post("/recommendations", json={"tags": ["roguelike"], "limit": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert 1 <= len(body) <= 3
    assert {"title", "score"} <= body[0].keys()


def test_recomendaciones_sin_datos_devuelve_400():
    resp = client.post("/recommendations", json={"liked_games": [], "tags": []})
    assert resp.status_code == 400


def test_limite_invalido_devuelve_422():
    resp = client.post("/recommendations", json={"tags": ["rpg"], "limit": 0})
    assert resp.status_code == 422
