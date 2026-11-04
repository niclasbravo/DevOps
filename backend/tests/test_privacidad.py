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


def test_voto_guarda_seudonimo_y_solo_fecha(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    assert client.post("/feedback", headers=headers, json={"game": _juego(), "vote": 1}).status_code == 201
    (fila,) = _votos()
    assert fila["pseudonym"] == pseudonymize(account_id)
    assert account_id not in fila["pseudonym"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", fila["voted_on"])


def test_voto_no_crea_el_archivo_antiguo(tmp_path, monkeypatch, nueva_cuenta):
    monkeypatch.chdir(tmp_path)
    _, headers = nueva_cuenta()
    client.post("/feedback", headers=headers, json={"game": _juego(), "vote": 1})
    assert not (tmp_path / "data" / "feedback.jsonl").exists()


def test_campo_extra_en_feedback_se_rechaza(nueva_cuenta):
    _, headers = nueva_cuenta()
    cuerpo = {"game": "Hades", "vote": 1, "birthdate": "2000-01-01"}
    assert client.post("/feedback", headers=headers, json=cuerpo).status_code == 422


def test_campo_extra_en_recomendaciones_se_rechaza():
    cuerpo = {"tags": ["roguelike"], "email": "persona@example.com"}
    assert client.post("/recommendations", json=cuerpo).status_code == 422


def test_volver_a_votar_actualiza_el_voto(nueva_cuenta):
    _, headers = nueva_cuenta()
    game = _juego()
    assert client.post("/feedback", headers=headers, json={"game": game, "vote": 1}).status_code == 201
    assert client.post("/feedback", headers=headers, json={"game": game, "vote": -1}).status_code == 201
    (fila,) = _votos()
    assert fila["vote"] == -1


def test_sin_clave_feedback_responde_503_y_lo_publico_funciona(monkeypatch, nueva_cuenta):
    _, headers = nueva_cuenta()
    game = _juego()
    monkeypatch.delenv("GM_PSEUDONYM_KEY")
    resp = client.post("/feedback", headers=headers, json={"game": game, "vote": 1})
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


def test_variantes_del_titulo_son_un_solo_voto(nueva_cuenta):
    _, headers = nueva_cuenta()
    game = _juego()
    for variante in (game, game.upper(), f"  {game.lower()} "):
        assert client.post("/feedback", headers=headers, json={"game": variante, "vote": 1}).status_code == 201
    (fila,) = _votos()
    assert fila["game"] == game


def _bytes(path, *lineas: bytes) -> None:
    path.write_bytes(b"\n".join(lineas) + b"\n")


@pytest.mark.parametrize(
    "corrupta",
    [
        b"caf\xe9",
        '{"user_id":"a","game":"Hades","vote":1,"ts":"2026-10-30"}'.encode()[:-2] + "é".encode()[:1],
        b'{"user_id":"\\ud800","game":"Hades","vote":1,"ts":"2026-10-30"}',
        b'{"user_id":"a","game":"\\ud800","vote":1,"ts":"2026-10-30"}',
        b'{"user_id":"a","game":"Hades","vote":1,"ts":"\\ud800"}',
        b"[" * 200_000,
        b"1" * 5000,
    ],
    ids=["byte-invalido", "multibyte-truncado", "surrogate-id", "surrogate-juego", "surrogate-ts", "anidado", "entero"],
)
def test_una_linea_corrupta_no_aborta_la_migracion(tmp_path, corrupta):
    path = tmp_path / "feedback.jsonl"
    _bytes(path, _linea("a", "Hades").encode(), corrupta, _linea("b", "Hades").encode())
    assert migrate_feedback(path) == (2, 1)
    assert not path.exists()


@pytest.mark.parametrize(
    "registro",
    [
        {"ts": "nico@mail.cl extra", "user_id": "a", "game": "Hades", "vote": 1},
        {"ts": "", "user_id": "a", "game": "Hades", "vote": 1},
        {"ts": "2026-10-30", "user_id": "a", "game": "persona@example.com", "vote": 1},
        {"ts": "2026-10-30", "user_id": "a", "game": "Hades", "vote": True},
        {"ts": "2026-10-30", "user_id": "a", "game": "Hades", "vote": 1.0},
    ],
    ids=["fecha-con-correo", "fecha-vacia", "juego-con-correo", "voto-booleano", "voto-decimal"],
)
def test_migracion_omite_datos_invalidos_sin_guardarlos(tmp_path, registro):
    path = tmp_path / "feedback.jsonl"
    _archivo(path, [json.dumps(registro)])
    assert migrate_feedback(path) == (0, 1)
    assert _votos() == []


def test_migracion_guarda_el_titulo_canonico(tmp_path):
    path = tmp_path / "feedback.jsonl"
    _archivo(path, [_linea("a", "hades"), _linea("a", "  HADES ", vote=-1)])
    assert migrate_feedback(path) == (2, 0)
    (fila,) = _votos()
    assert fila["game"] == "Hades" and fila["vote"] == -1
