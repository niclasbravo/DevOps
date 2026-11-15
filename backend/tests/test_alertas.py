"""Reglas de detección: cada una se dispara en su umbral, no antes, y sin duplicados abiertos."""

from contextlib import closing

from fastapi.testclient import TestClient

from app.crypto import pseudonymize
from app.db import connect
from app.main import app

client = TestClient(app)
CLAVE = "una-clave-larga-1"


def _alertas(regla: str) -> list:
    with closing(connect()) as conn:
        return conn.execute("SELECT * FROM security_alerts WHERE rule = ?", (regla,)).fetchall()


def _login_fallido(alias: str):
    return client.post("/auth/login", json={"alias": alias, "password": "otra-clave-larga-2"})


def test_login_fallido_reiterado_se_dispara_en_el_quinto(nueva_cuenta):
    account_id, _ = nueva_cuenta()
    for _ in range(4):
        _login_fallido("usuario_1")
    assert _alertas("login_fallido_reiterado") == []
    _login_fallido("usuario_1")
    (alerta,) = _alertas("login_fallido_reiterado")
    assert alerta["severity"] == "media" and alerta["subject"] == pseudonymize(account_id)


def test_no_se_duplica_una_alerta_abierta(nueva_cuenta):
    nueva_cuenta()
    for _ in range(11):
        _login_fallido("usuario_1")
    assert len(_alertas("login_fallido_reiterado")) == 1


def test_posible_fuerza_bruta_con_veinte_fallos_en_total():
    for n in range(19):
        _login_fallido(f"no_existe_{n}")
    assert _alertas("posible_fuerza_bruta") == []
    _login_fallido("no_existe_20")
    (alerta,) = _alertas("posible_fuerza_bruta")
    assert alerta["severity"] == "alta"


def test_exportacion_anomala_despues_de_tres_en_un_dia(nueva_cuenta):
    _, headers = nueva_cuenta()
    cuerpo = {"type": "acceso", "current_password": CLAVE}
    for _ in range(3):
        client.post("/me/requests", headers=headers, json=cuerpo)
    assert _alertas("exportacion_anomala") == []
    client.post("/me/requests", headers=headers, json=cuerpo)
    assert len(_alertas("exportacion_anomala")) == 1


def test_acceso_denegado_reiterado(nueva_cuenta):
    _, headers = nueva_cuenta("analista")
    for _ in range(9):
        client.get("/admin/accounts", headers=headers)
    assert _alertas("acceso_denegado_reiterado") == []
    client.get("/admin/accounts", headers=headers)
    assert len(_alertas("acceso_denegado_reiterado")) == 1


def test_revelacion_masiva_de_correos(nueva_cuenta):
    objetivo, _ = nueva_cuenta()
    _, admin = nueva_cuenta("admin")
    for _ in range(5):
        client.post(f"/admin/accounts/{objetivo}/reveal-email", headers=admin)
    assert _alertas("revelacion_masiva") == []
    client.post(f"/admin/accounts/{objetivo}/reveal-email", headers=admin)
    (alerta,) = _alertas("revelacion_masiva")
    assert alerta["severity"] == "alta"


def test_el_admin_lista_y_clasifica_alertas_y_queda_auditado(nueva_cuenta):
    nueva_cuenta()
    for _ in range(5):
        _login_fallido("usuario_1")
    _, admin = nueva_cuenta("admin")
    (alerta,) = client.get("/admin/alerts", headers=admin).json()
    assert alerta["regla"] == "login_fallido_reiterado" and alerta["estado"] == "abierta"
    resp = client.patch(f"/admin/alerts/{alerta['id']}", headers=admin, json={"status": "en_analisis"})
    assert resp.status_code == 200
    with closing(connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'alert:update'").fetchone()[0] == 1
    assert client.patch(f"/admin/alerts/{alerta['id']}", headers=admin, json={"status": "otro"}).status_code == 422


def test_solo_el_admin_ve_las_alertas(nueva_cuenta):
    for rol in ("usuario", "analista"):
        _, headers = nueva_cuenta(rol)
        assert client.get("/admin/alerts", headers=headers).status_code == 403
