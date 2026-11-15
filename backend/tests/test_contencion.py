"""Procedimientos técnicos de contención y recuperación del plan de incidentes."""

import hashlib
from contextlib import closing

from fastapi.testclient import TestClient

from app.cli import disable_account, export_evidence, purge_retention, revoke_sessions, set_read_only
from app.db import connect
from app.main import app

client = TestClient(app)
CLAVE = "una-clave-larga-1"


def _auditado(accion: str) -> bool:
    with closing(connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = ?", (accion,)).fetchone()[0] > 0


def test_revocar_sesiones_de_una_cuenta_invalida_sus_tokens_anteriores(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    _, otra = nueva_cuenta()
    revoke_sessions(account_id)
    assert client.get("/me", headers=headers).status_code == 401
    assert client.get("/me", headers=otra).status_code == 200
    token = client.post("/auth/login", json={"alias": "usuario_1", "password": CLAVE}).json()["access_token"]
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert _auditado("admin:revoke_sessions")


def test_revocar_todas_las_sesiones(nueva_cuenta):
    _, uno = nueva_cuenta()
    _, otro = nueva_cuenta("admin")
    revoke_sessions(None)
    assert client.get("/me", headers=uno).status_code == 401
    assert client.get("/me", headers=otro).status_code == 401


def test_deshabilitar_cuenta(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    disable_account(account_id)
    assert client.get("/me", headers=headers).status_code == 401
    assert _auditado("admin:disable_account")


def test_modo_solo_lectura_bloquea_escrituras_salvo_admin_y_login(nueva_cuenta):
    _, headers = nueva_cuenta()
    _, admin = nueva_cuenta("admin")
    set_read_only(True)
    assert client.post("/me/consents/votos_analitica", headers=headers).status_code == 503
    assert client.get("/me", headers=headers).status_code == 200
    assert client.post("/auth/login", json={"alias": "usuario_1", "password": CLAVE}).status_code == 200
    assert client.get("/admin/alerts", headers=admin).status_code == 200
    set_read_only(False)
    assert client.post("/me/consents/votos_analitica", headers=headers).status_code == 200
    assert _auditado("system:read_only")


def test_exportar_evidencia_copia_la_base_con_su_hash(nueva_cuenta, tmp_path):
    nueva_cuenta()
    copia, huella = export_evidence(tmp_path / "evidencia")
    assert hashlib.sha256(copia.read_bytes()).hexdigest() == huella.read_text(encoding="utf-8").split()[0]
    with closing(connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'system:export_evidence'").fetchone()[0]


def test_purgar_retencion_borra_solo_lo_vencido(nueva_cuenta):
    nueva_cuenta()
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO audit_log (ts, action, outcome) VALUES ('2020-01-01T00:00:00+00:00', 'viejo', 'permitido')"
        )
        conn.execute(
            """INSERT INTO rights_requests (id, subject, type, status, received_at, closed_at, due_on)
               VALUES ('vieja', 's', 'acceso', 'cerrada', '2020-01-01', '2020-01-02T00:00:00+00:00', '2020-01-31')"""
        )
    resumen = purge_retention()
    assert resumen["audit_log"] == 1 and resumen["rights_requests"] == 1
    with closing(connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'viejo'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] > 0
    assert _auditado("audit:purge")
