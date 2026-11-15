"""Flujo para menores de 14 años: la cuenta solo se activa con la confirmación del representante legal."""

import datetime as dt
import re
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.crypto import pseudonymize
from app.db import connect
from app.main import app

client = TestClient(app)
CLAVE = "una-clave-larga-1"
CORREO_REPRESENTANTE = "madre@example.com"


def _registrar_menor(alias: str = "peque_1", **extra):
    cuerpo = {"alias": alias, "password": CLAVE, "is_child": True, "guardian_email": CORREO_REPRESENTANTE, **extra}
    return client.post("/auth/register", json=cuerpo)


def _token(buzon) -> str:
    (mensaje,) = buzon()
    return re.search(r"/guardian\.html#([\w-]+)", mensaje["body"]).group(1)


def _confirmar(token: str, consents: list[str] | None = None, declara: bool = True):
    cuerpo = {"token": token, "declares_legal_representative": declara, "consents": consents or []}
    return client.post("/guardian/confirm", json=cuerpo)


def _cuenta(alias: str = "peque_1"):
    with closing(connect()) as conn:
        return conn.execute("SELECT * FROM accounts WHERE alias = ?", (alias,)).fetchone()


def _login(alias: str = "peque_1"):
    return client.post("/auth/login", json={"alias": alias, "password": CLAVE})


def test_cuenta_de_menor_queda_pendiente_y_sin_login(buzon):
    resp = _registrar_menor()
    assert resp.status_code == 201 and resp.json()["estado"] == "pendiente_representante"
    cuenta = _cuenta()
    assert cuenta["is_child"] == 1 and cuenta["pending_guardian"] == 1 and cuenta["is_active"] == 0
    assert _login().status_code == 401


def test_representante_recibe_enlace_y_su_correo_no_se_guarda(buzon):
    _registrar_menor()
    (mensaje,) = buzon()
    assert mensaje["to"] == CORREO_REPRESENTANTE
    with closing(connect()) as conn:
        volcado = "\n".join(conn.iterdump())
    assert CORREO_REPRESENTANTE not in volcado
    assert _token(buzon) not in volcado  # solo se guarda el hash del token


def test_menor_sin_correo_del_representante_se_rechaza():
    resp = client.post("/auth/register", json={"alias": "peque_2", "password": CLAVE, "is_child": True})
    assert resp.status_code == 422


def test_adulto_no_entrega_correo_de_representante():
    cuerpo = {"alias": "adulto", "password": CLAVE, "guardian_email": CORREO_REPRESENTANTE}
    assert client.post("/auth/register", json=cuerpo).status_code == 422


def test_menor_no_puede_otorgar_finalidades_al_registrarse():
    assert _registrar_menor(consents=["votos_analitica"]).status_code == 400
    assert _cuenta() is None


def test_registro_no_pide_fecha_de_nacimiento():
    assert _registrar_menor(birthdate="2015-01-01").status_code == 422


def test_confirmacion_activa_la_cuenta_y_registra_al_representante(buzon):
    _registrar_menor()
    assert _confirmar(_token(buzon), ["votos_analitica"]).status_code == 200
    assert _login().status_code == 200
    with closing(connect()) as conn:
        eventos = conn.execute(
            "SELECT purpose, actor, status FROM consent_events WHERE subject = ?", (pseudonymize(_cuenta()["id"]),)
        ).fetchall()
    assert [tuple(e) for e in eventos] == [("votos_analitica", "representante", "otorgado")]


def test_sin_declaracion_del_representante_no_se_activa(buzon):
    _registrar_menor()
    assert _confirmar(_token(buzon), declara=False).status_code == 422
    assert _cuenta()["pending_guardian"] == 1


def test_enlace_es_de_un_solo_uso(buzon):
    _registrar_menor()
    token = _token(buzon)
    assert _confirmar(token).status_code == 200
    resp = _confirmar(token)
    assert resp.status_code == 400 and resp.json()["detail"] == "Enlace inválido o vencido"


def test_enlace_vencido_no_activa(buzon):
    _registrar_menor()
    token = _token(buzon)
    vencido = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat()
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE guardian_verifications SET expires_at = ?", (vencido,))
    resp = _confirmar(token)
    assert resp.status_code == 400 and resp.json()["detail"] == "Enlace inválido o vencido"
    assert _cuenta()["is_active"] == 0


def test_token_inventado_responde_igual_que_uno_vencido():
    resp = _confirmar("x" * 43)
    assert resp.status_code == 400 and resp.json()["detail"] == "Enlace inválido o vencido"


def test_novedades_no_estan_disponibles_para_menores(buzon):
    _registrar_menor()
    assert _confirmar(_token(buzon), ["novedades_correo"]).status_code == 400
    assert _cuenta()["pending_guardian"] == 1


def test_menor_no_otorga_pero_si_revoca(buzon):
    _registrar_menor()
    _confirmar(_token(buzon), ["votos_analitica"])
    headers = {"Authorization": f"Bearer {_login().json()['access_token']}"}
    assert client.post("/me/consents/correo_recuperacion", headers=headers).status_code == 403
    assert client.delete("/me/consents/votos_analitica", headers=headers).status_code == 200


def test_admin_no_puede_activar_una_cuenta_pendiente(buzon, nueva_cuenta):
    _registrar_menor()
    _, admin = nueva_cuenta("admin")
    resp = client.patch(f"/admin/accounts/{_cuenta()['id']}/status", headers=admin, json={"is_active": True})
    assert resp.status_code == 409
    assert _login().status_code == 401


def test_sin_buzon_no_se_crea_la_cuenta_del_menor(monkeypatch):
    monkeypatch.delenv("GM_OUTBOX_DIR")
    assert _registrar_menor().status_code == 503
    assert _cuenta() is None


@pytest.mark.parametrize("campo", ["token", "consents"])
def test_confirmacion_rechaza_datos_invalidos(campo, buzon):
    _registrar_menor()
    cuerpo = {"token": _token(buzon), "declares_legal_representative": True, "consents": []}
    cuerpo[campo] = "x" if campo == "token" else ["vender_datos"]
    assert client.post("/guardian/confirm", json=cuerpo).status_code == 422


def test_confirmacion_queda_auditada(buzon):
    _registrar_menor()
    _confirmar(_token(buzon))
    _confirmar("x" * 43)
    with closing(connect()) as conn:
        filas = conn.execute("SELECT * FROM audit_log WHERE action = 'auth:guardian_confirm' ORDER BY id").fetchall()
    assert [f["outcome"] for f in filas] == ["permitido", "denegado"]
    assert filas[0]["target_pseudonym"] == pseudonymize(_cuenta()["id"])
