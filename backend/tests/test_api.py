"""Pruebas de la API HTTP."""

import json

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


def test_header_de_latencia():
    resp = client.post("/recommendations", json={"tags": ["roguelike"], "limit": 3})
    assert float(resp.headers["X-Response-Time-ms"]) >= 0


def test_games_respeta_paginacion():
    resp = client.get("/games", params={"limit": 2, "offset": 1})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_stats_informa_recomendador_y_latencia():
    client.post("/recommendations", json={"tags": ["roguelike"], "limit": 3})
    body = client.get("/stats").json()
    assert body["recommender"] in {"ml", "jaccard"}
    assert body["latency_ms"]["requests"] >= 1
    assert body["latency_ms"]["p95"] is not None


def test_feedback_guarda_voto_anonimo(tmp_path, monkeypatch):
    path = tmp_path / "feedback.jsonl"
    monkeypatch.setenv("FEEDBACK_PATH", str(path))
    game = client.get("/games", params={"limit": 1}).json()[0]["title"]
    resp = client.post("/feedback", json={"user_id": "anon-123", "game": game, "vote": 1})
    assert resp.status_code == 201
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert set(record) == {"ts", "user_id", "game", "vote"}
    assert record["vote"] == 1


def test_feedback_rechaza_juego_desconocido_y_voto_invalido(tmp_path, monkeypatch):
    monkeypatch.setenv("FEEDBACK_PATH", str(tmp_path / "feedback.jsonl"))
    assert client.post("/feedback", json={"user_id": "a", "game": "No Existe 9000", "vote": 1}).status_code == 404
    assert client.post("/feedback", json={"user_id": "a", "game": "Hades", "vote": 5}).status_code == 422
    assert client.post("/feedback", json={"user_id": "a b@c", "game": "Hades", "vote": 1}).status_code == 422
