"""Los errores y los logs no deben revelar datos sensibles."""

import logging
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import register_error_handlers
from app.logging_safe import RedactingFilter, configure_logging
from app.main import app

client = TestClient(app)
SECRETO = "mi-clave-secreta-1"


def _app_que_falla() -> TestClient:
    fallida = FastAPI()
    register_error_handlers(fallida)

    @fallida.get("/falla")
    def falla():
        raise RuntimeError(f"fallo con dato {SECRETO}")

    return TestClient(fallida, raise_server_exceptions=False)


def _filtrar(mensaje: str, *args) -> str:
    record = logging.LogRecord("gamematch", logging.INFO, __file__, 1, mensaje, args, None)
    assert RedactingFilter().filter(record)
    return record.getMessage()


def test_422_no_devuelve_la_contrasena():
    resp = client.post("/auth/register", json={"alias": "x", "password": SECRETO})
    assert resp.status_code == 422
    assert SECRETO not in resp.text


def test_422_indica_el_campo_y_el_tipo_de_error():
    resp = client.post("/auth/register", json={"alias": "x", "password": SECRETO})
    assert resp.json() == {"detail": [{"campo": "body.alias", "error": "string_pattern_mismatch"}]}


def test_422_de_campo_extra_no_devuelve_su_valor():
    cuerpo = {"alias": "jugador", "password": "una-clave-larga-1", "birthdate": SECRETO}
    resp = client.post("/auth/register", json=cuerpo)
    assert resp.status_code == 422
    assert SECRETO not in resp.text


def test_500_es_generico_y_no_filtra_la_excepcion():
    resp = _app_que_falla().get("/falla")
    assert resp.status_code == 500
    assert SECRETO not in resp.text
    assert resp.json()["detail"] == "Error interno"
    assert len(resp.json()["error_id"]) == 36


def test_500_registra_el_id_sin_el_mensaje_de_la_excepcion(caplog):
    with caplog.at_level(logging.ERROR):
        error_id = _app_que_falla().get("/falla").json()["error_id"]
    assert error_id in caplog.text
    assert "RuntimeError" in caplog.text
    assert SECRETO not in caplog.text


@pytest.mark.parametrize(
    "dato",
    [
        "persona@example.com",
        "Bearer abc.def-123",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhYmMifQ.c2lnbmF0dXJh",
        "6f1c1a9e-3f0b-4a55-9a51-2a3b1c7d9e10",
    ],
)
def test_filtro_redacta_datos_sensibles(dato):
    salida = _filtrar("Evento con %s al final", dato)
    assert dato not in salida
    assert "[REDACTADO]" in salida


def test_filtro_quita_el_traceback_que_puede_traer_datos():
    try:
        raise RuntimeError(SECRETO)
    except RuntimeError:
        record = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, "Fallo", (), sys.exc_info())
    assert RedactingFilter().filter(record)
    assert record.exc_info is None and record.exc_text is None
    assert SECRETO not in logging.Formatter().format(record)


def test_filtro_deja_pasar_mensajes_normales():
    assert _filtrar("Modelo cargado: %d juegos", 7996) == "Modelo cargado: 7996 juegos"


def test_login_no_deja_alias_ni_clave_en_logs(caplog):
    with caplog.at_level(logging.DEBUG):
        client.post("/auth/register", json={"alias": "jugador_log", "password": SECRETO})
        client.post("/auth/login", json={"alias": "jugador_log", "password": SECRETO})
    assert SECRETO not in caplog.text
    assert "jugador_log" not in caplog.text


def test_configure_logging_filtra_los_handlers_y_apaga_el_log_de_acceso():
    configure_logging()
    root = logging.getLogger()
    assert all(any(isinstance(f, RedactingFilter) for f in h.filters) for h in root.handlers)
    assert logging.getLogger("uvicorn.access").disabled  # registra la IP de cada cliente


def test_sql_injection_en_login_no_autentica():
    resp = client.post("/auth/login", json={"alias": "' OR 1=1 --", "password": SECRETO})
    assert resp.status_code == 422


def test_sql_injection_en_voto_se_guarda_como_texto_o_se_rechaza(nueva_cuenta):
    _, headers = nueva_cuenta()
    resp = client.post("/feedback", headers=headers, json={"game": "x'; DROP TABLE feedback; --", "vote": 1})
    assert resp.status_code == 404
    assert client.get("/me/feedback", headers=headers).status_code == 200
