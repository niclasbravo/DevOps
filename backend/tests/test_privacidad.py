"""Pruebas de minimización: qué se guarda y qué se rechaza."""

import json
import re
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.cli import migrate_feedback
from app.crypto import pseudonymize
from app.db import connect
from app.main import app

client = TestClient(app)


def _juego() -> str:
    return client.get("/games", params={"limit": 1}).json()[0]["title"]


def _votos() -> list:
    with closing(connect()) as conn:
        return conn.execute("SELECT pseudonym, game, vote, voted_on FROM feedback ORDER BY id").fetchall()


def test_voto_guarda_seudonimo_y_solo_fecha():
    game = _juego()
    assert client.post("/feedback", json={"user_id": "anon-123", "game": game, "vote": 1}).status_code == 201
    (fila,) = _votos()
    assert fila["pseudonym"] == pseudonymize("anon-123")
    assert "anon-123" not in fila["pseudonym"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", fila["voted_on"])


def test_voto_no_crea_el_archivo_antiguo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client.post("/feedback", json={"user_id": "a", "game": _juego(), "vote": 1})
    assert not (tmp_path / "data" / "feedback.jsonl").exists()


@pytest.mark.parametrize(
    "ruta,cuerpo",
    [
        ("/feedback", {"user_id": "a", "game": "Hades", "vote": 1, "birthdate": "2000-01-01"}),
        ("/recommendations", {"tags": ["roguelike"], "email": "persona@example.com"}),
    ],
)
def test_campo_extra_se_rechaza(ruta, cuerpo):
    assert client.post(ruta, json=cuerpo).status_code == 422


def test_volver_a_votar_actualiza_el_voto():
    game = _juego()
    assert client.post("/feedback", json={"user_id": "a", "game": game, "vote": 1}).status_code == 201
    assert client.post("/feedback", json={"user_id": "a", "game": game, "vote": -1}).status_code == 201
    (fila,) = _votos()
    assert fila["vote"] == -1


def test_sin_clave_feedback_responde_503_y_lo_publico_funciona(monkeypatch):
    game = _juego()
    monkeypatch.delenv("GM_PSEUDONYM_KEY")
    resp = client.post("/feedback", json={"user_id": "a", "game": game, "vote": 1})
    assert resp.status_code == 503
    assert "GM_PSEUDONYM_KEY" not in resp.text
    assert client.get("/health").status_code == 200
    assert client.post("/recommendations", json={"tags": ["roguelike"]}).status_code == 200


def _archivo(path, lineas: list[str]) -> None:
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def _linea(user_id: str, game: str, vote: int = 1) -> str:
    return json.dumps({"ts": "2026-10-30T14:03:22+00:00", "user_id": user_id, "game": game, "vote": vote})


def test_migracion_seudonimiza_y_reduce_la_fecha(tmp_path):
    path = tmp_path / "feedback.jsonl"
    _archivo(path, [_linea("nico-b", "Hades")])
    assert migrate_feedback(path) == (1, 0)
    (fila,) = _votos()
    assert fila["pseudonym"] == pseudonymize("legacy:nico-b")
    assert fila["voted_on"] == "2026-10-30"


def test_migracion_omite_linea_corrupta_y_borra_archivo(tmp_path, caplog):
    path = tmp_path / "feedback.jsonl"
    _archivo(path, [_linea("a", "Hades"), "secreto-corrupto{", _linea("b", "Hades"), _linea("c", "Hades", vote=5)])
    assert migrate_feedback(path) == (2, 2)
    assert not path.exists()
    assert "secreto-corrupto" not in caplog.text


def test_migrar_dos_veces_no_duplica(tmp_path):
    path = tmp_path / "feedback.jsonl"
    _archivo(path, [_linea("a", "Hades")])
    migrate_feedback(path)
    _archivo(path, [_linea("a", "Hades")])
    migrate_feedback(path)
    assert len(_votos()) == 1


def test_migrar_sin_archivo_no_falla(tmp_path):
    assert migrate_feedback(tmp_path / "no-existe.jsonl") == (0, 0)


def test_si_la_migracion_falla_no_borra_el_archivo(tmp_path, monkeypatch):
    path = tmp_path / "feedback.jsonl"
    _archivo(path, [_linea("a", "Hades")])
    monkeypatch.delenv("GM_PSEUDONYM_KEY")
    with pytest.raises(RuntimeError):
        migrate_feedback(path)
    assert path.exists()
