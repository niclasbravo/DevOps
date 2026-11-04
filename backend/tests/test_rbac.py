"""Matriz RBAC: cada fila es una celda de las tablas 4.2 y 4.3 de la spec."""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.auth import create_account
from app.cli import create_admin
from app.cli import main as cli_main
from app.crypto import encrypt_email, pseudonymize
from app.db import connect
from app.main import app

client = TestClient(app)
ROLES = ("usuario", "analista", "admin")


def _todos(metodo: str, ruta: str, esperado: int) -> list[tuple]:
    return [(rol, metodo, ruta, esperado) for rol in (None, *ROLES)]


def _protegida(metodo: str, ruta: str, usuario: int, analista: int, admin: int) -> list[tuple]:
    return [
        (None, metodo, ruta, 401),
        ("usuario", metodo, ruta, usuario),
        ("analista", metodo, ruta, analista),
        ("admin", metodo, ruta, admin),
    ]


MATRIZ = [
    *_todos("GET", "/", 200),
    *_todos("GET", "/health", 200),
    *_todos("GET", "/games", 200),
    *_todos("GET", "/stats", 200),
    *_todos("POST", "/recommendations", 200),
    *_protegida("GET", "/me", 200, 200, 200),
    *_protegida("GET", "/me/feedback", 200, 200, 200),
    *_protegida("GET", "/reports/feedback", 403, 200, 200),
    *_protegida("PATCH", "/me", 200, 200, 200),
    *_protegida("DELETE", "/me", 204, 204, 409),  # el admin de la prueba es el último: ver test_auth
    *_protegida("POST", "/feedback", 201, 403, 403),
    *_protegida("GET", "/admin/model", 403, 403, 200),
    *_protegida("GET", "/admin/accounts", 403, 403, 200),
    *_protegida("GET", "/admin/audit", 403, 403, 200),
    *_protegida("PATCH", "/admin/accounts/{id}/role", 403, 403, 200),
    *_protegida("PATCH", "/admin/accounts/{id}/status", 403, 403, 200),
    *_protegida("POST", "/admin/accounts/{id}/reveal-email", 403, 403, 200),
]


def _cuerpo(ruta: str) -> dict | None:
    if ruta == "/feedback":
        return {"game": client.get("/games", params={"limit": 1}).json()[0]["title"], "vote": 1}
    if ruta == "/recommendations":
        return {"tags": ["roguelike"]}
    if ruta == "/me":
        return {"current_password": "una-clave-larga-1"}
    if ruta.endswith("/role"):
        return {"role": "analista"}
    if ruta.endswith("/status"):
        return {"is_active": True}
    return None


@pytest.fixture
def cuentas(nueva_cuenta) -> dict:
    return {rol: nueva_cuenta(rol) for rol in ROLES}


@pytest.mark.parametrize("rol,metodo,ruta,esperado", MATRIZ)
def test_matriz_rbac(cuentas, nueva_cuenta, rol, metodo, ruta, esperado):
    objetivo, _ = nueva_cuenta()
    headers = cuentas[rol][1] if rol else {}
    resp = client.request(metodo, ruta.replace("{id}", objetivo), headers=headers, json=_cuerpo(ruta))
    assert resp.status_code == esperado


def test_todo_endpoint_figura_en_la_matriz():
    """Un método o una ruta nueva sin fila en la matriz hace fallar esta prueba."""
    publicas = {("POST", "/auth/register"), ("POST", "/auth/login")}
    cubiertas = {(fila[1], fila[2]) for fila in MATRIZ} | publicas
    rutas = {
        (metodo.upper(), ruta.replace("{account_id}", "{id}"))
        for ruta, operaciones in app.openapi()["paths"].items()
        for metodo in operaciones
    }
    assert rutas <= cubiertas


def test_lista_de_cuentas_no_expone_correo_ni_hash(cuentas):
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE accounts SET email_encrypted = ?", (encrypt_email("persona@example.com"),))
    resp = client.get("/admin/accounts", headers=cuentas["admin"][1])
    assert all(set(fila) == {"id", "alias", "role", "is_active"} for fila in resp.json())
    assert "argon2" not in resp.text


def test_cambio_de_rol_se_aplica_y_se_audita(cuentas, nueva_cuenta):
    objetivo, headers = nueva_cuenta()
    resp = client.patch(f"/admin/accounts/{objetivo}/role", headers=cuentas["admin"][1], json={"role": "analista"})
    assert resp.status_code == 200
    assert client.get("/me", headers=headers).json()["role"] == "analista"
    with closing(connect()) as conn:
        fila = conn.execute("SELECT * FROM audit_log WHERE action = 'accounts:manage'").fetchone()
    assert fila["outcome"] == "permitido" and fila["target_pseudonym"] == pseudonymize(objetivo)


def test_rol_inexistente_se_rechaza(cuentas, nueva_cuenta):
    objetivo, _ = nueva_cuenta()
    resp = client.patch(f"/admin/accounts/{objetivo}/role", headers=cuentas["admin"][1], json={"role": "root"})
    assert resp.status_code == 422


def test_suspender_cuenta_corta_su_sesion(cuentas, nueva_cuenta):
    objetivo, headers = nueva_cuenta()
    client.patch(f"/admin/accounts/{objetivo}/status", headers=cuentas["admin"][1], json={"is_active": False})
    assert client.get("/me", headers=headers).status_code == 401


@pytest.mark.parametrize("metodo,sufijo", [("PATCH", "role"), ("POST", "reveal-email")])
def test_cuenta_inexistente_da_404(cuentas, metodo, sufijo):
    resp = client.request(
        metodo, f"/admin/accounts/no-existe/{sufijo}", headers=cuentas["admin"][1], json=_cuerpo(f"/{sufijo}")
    )
    assert resp.status_code == 404


def test_admin_no_cambia_su_propio_rol(cuentas):
    admin_id, headers = cuentas["admin"]
    resp = client.patch(f"/admin/accounts/{admin_id}/role", headers=headers, json={"role": "usuario"})
    assert resp.status_code == 403


def test_no_se_puede_suspender_al_ultimo_admin(cuentas):
    admin_id, headers = cuentas["admin"]
    resp = client.patch(f"/admin/accounts/{admin_id}/status", headers=headers, json={"is_active": False})
    assert resp.status_code == 409


def test_ver_correo_queda_auditado(cuentas):
    objetivo = create_account("con_correo", "una-clave-larga-1", email="persona@example.com")
    admin_id, headers = cuentas["admin"]
    resp = client.post(f"/admin/accounts/{objetivo}/reveal-email", headers=headers)
    assert resp.json() == {"email": "persona@example.com"}
    with closing(connect()) as conn:
        fila = conn.execute("SELECT * FROM audit_log WHERE action = 'accounts:reveal_email'").fetchone()
    assert fila["actor_pseudonym"] == pseudonymize(admin_id)
    assert fila["target_pseudonym"] == pseudonymize(objetivo)


def test_auditoria_no_contiene_alias_ni_ids(cuentas):
    admin_id, headers = cuentas["admin"]
    client.get("/admin/accounts", headers=headers)
    resp = client.get("/admin/audit", headers=headers)
    assert resp.json()
    assert admin_id not in resp.text and "admin_" not in resp.text


def test_ninguna_respuesta_contiene_claves(cuentas):
    headers = cuentas["admin"][1]
    secretos = [os.environ[n] for n in ("GM_PSEUDONYM_KEY", "GM_EMAIL_KEY", "GM_JWT_SECRET")]
    for ruta in ("/me", "/admin/model", "/admin/accounts", "/admin/audit", "/stats", "/openapi.json"):
        texto = client.get(ruta, headers=headers).text
        assert not [s for s in secretos if s in texto]


def test_admin_model_entrega_los_datos_retirados_de_stats(cuentas):
    assert {"recommender", "model"} <= client.get("/admin/model", headers=cuentas["admin"][1]).json().keys()


def test_create_admin_crea_la_cuenta():
    account_id = create_admin("jefa", "una-clave-larga-1")
    with closing(connect()) as conn:
        fila = conn.execute(
            "SELECT r.name FROM accounts a JOIN roles r ON r.id = a.role_id WHERE a.id = ?", (account_id,)
        ).fetchone()
    assert fila["name"] == "admin"


@pytest.mark.parametrize("alias,clave", [("con@arroba", "una-clave-larga-1"), ("jefa", "corta")])
def test_create_admin_valida_los_datos(alias, clave):
    with pytest.raises(ValueError):
        create_admin(alias, clave)


def _admins_activos() -> int:
    with closing(connect()) as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM accounts a JOIN roles r ON r.id = a.role_id
               WHERE r.name = 'admin' AND a.is_active = 1"""
        ).fetchone()[0]


def _borrar(_, headers):
    return client.delete("/me", headers=headers)


def _suspender(objetivo, headers):
    return client.patch(f"/admin/accounts/{objetivo}/status", headers=headers, json={"is_active": False})


def _degradar(objetivo, headers):
    return client.patch(f"/admin/accounts/{objetivo}/role", headers=headers, json={"role": "usuario"})


@pytest.mark.parametrize("accion", [_borrar, _suspender, _degradar], ids=["borrar", "suspender", "degradar"])
def test_dos_admins_a_la_vez_no_dejan_el_sistema_sin_admin(nueva_cuenta, accion):
    for _ in range(8):
        (uno, cabeceras_uno), (otro, cabeceras_otro) = nueva_cuenta("admin"), nueva_cuenta("admin")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda args: accion(*args), [(otro, cabeceras_uno), (uno, cabeceras_otro)]))
        assert _admins_activos() >= 1
        with closing(connect()) as conn, conn:
            conn.execute("DELETE FROM accounts")


def test_sin_clave_de_seudonimos_cambiar_rol_no_se_aplica(cuentas, nueva_cuenta, monkeypatch):
    objetivo, headers_objetivo = nueva_cuenta()
    monkeypatch.delenv("GM_PSEUDONYM_KEY")
    resp = client.patch(f"/admin/accounts/{objetivo}/role", headers=cuentas["admin"][1], json={"role": "analista"})
    assert resp.status_code == 503
    assert client.get("/me", headers=headers_objetivo).json()["role"] == "usuario"


def test_sin_clave_de_seudonimos_suspender_no_se_aplica(cuentas, nueva_cuenta, monkeypatch):
    objetivo, headers_objetivo = nueva_cuenta()
    monkeypatch.delenv("GM_PSEUDONYM_KEY")
    resp = client.patch(f"/admin/accounts/{objetivo}/status", headers=cuentas["admin"][1], json={"is_active": False})
    assert resp.status_code == 503
    assert client.get("/me", headers=headers_objetivo).status_code == 200


def test_create_admin_no_imprime_la_clave_rechazada(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["app.cli", "create-admin", "--alias", "jefa"])
    monkeypatch.setattr("getpass.getpass", lambda *_: "MiClaveCort")
    with pytest.raises(SystemExit) as salida:
        cli_main()
    capturado = capsys.readouterr()
    assert salida.value.code != 0
    assert "MiClaveCort" not in capturado.out + capturado.err
