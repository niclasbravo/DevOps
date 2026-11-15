"""Registro de auditoría: atributos, minimización, integridad y conservación."""

import json
import sqlite3
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.audit import verify_chain
from app.cli import verify_audit
from app.crypto import pseudonymize
from app.db import connect
from app.main import app

client = TestClient(app)
CLAVE = "una-clave-larga-1"


def _filas() -> list:
    with closing(connect()) as conn:
        return conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()


def _registrar(alias: str = "jugador_audit", **extra):
    return client.post("/auth/register", json={"alias": alias, "password": CLAVE, **extra})


def test_cada_evento_tiene_id_recurso_y_cadena(nueva_cuenta):
    _, headers = nueva_cuenta()
    client.post("/me/consents/correo_recuperacion", headers=headers)
    client.post("/me/consents/votos_analitica", headers=headers)
    filas = _filas()
    ultima = filas[-1]
    assert uuid.UUID(ultima["event_id"])
    assert ultima["resource"] == "consent:votos_analitica"
    assert ultima["hash"] and ultima["prev_hash"] == filas[-2]["hash"]


def test_el_login_queda_auditado_permitido_y_denegado():
    _registrar()
    client.post("/auth/login", json={"alias": "jugador_audit", "password": CLAVE})
    client.post("/auth/login", json={"alias": "jugador_audit", "password": "otra-clave-larga-2"})
    resultados = [f["outcome"] for f in _filas() if f["action"] == "auth:login"]
    assert resultados == ["permitido", "denegado"]


def test_ninguna_fila_contiene_datos_personales_tras_un_recorrido_completo():
    correo = "persona@example.com"
    _registrar(consents=["correo_recuperacion"], email=correo)
    token = client.post("/auth/login", json={"alias": "jugador_audit", "password": CLAVE}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/auth/login", json={"alias": "jugador_audit", "password": "clave-equivocada-9"})
    client.post("/me/requests", headers=headers, json={"type": "acceso", "current_password": CLAVE})
    client.post("/me/requests", headers=headers,
                json={"type": "rectificacion", "current_password": CLAVE, "changes": {"alias": "otro_alias"}})
    with closing(connect()) as conn:
        account_id = conn.execute("SELECT id FROM accounts").fetchone()["id"]
    client.post("/me/requests", headers=headers, json={"type": "supresion", "current_password": CLAVE})
    texto = json.dumps([dict(f) for f in _filas()])
    for dato in ("jugador_audit", "otro_alias", correo, CLAVE, "clave-equivocada-9", token, account_id):
        assert dato not in texto
    assert pseudonymize(account_id) in texto  # el actor queda identificado solo por su seudónimo


def test_la_cadena_es_valida_y_detecta_una_alteracion(nueva_cuenta):
    _, headers = nueva_cuenta()
    client.post("/me/consents/votos_analitica", headers=headers)
    assert verify_chain() == (True, None)
    with closing(connect()) as conn, conn:  # alguien con acceso al archivo borra el trigger y edita
        conn.execute("DROP TRIGGER audit_no_update")
        alterada = conn.execute("SELECT id FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()["id"]
        conn.execute("UPDATE audit_log SET outcome = 'denegado' WHERE id = ?", (alterada,))
    assert verify_chain() == (False, alterada)


def test_verificar_auditoria_rota_genera_alerta_critica(nueva_cuenta):
    nueva_cuenta()
    _registrar()
    with closing(connect()) as conn, conn:
        conn.execute("DROP TRIGGER audit_no_update")
        conn.execute("UPDATE audit_log SET action = 'otra' WHERE id = (SELECT MAX(id) FROM audit_log)")
    assert verify_audit() is False
    with closing(connect()) as conn:
        alerta = conn.execute("SELECT * FROM security_alerts WHERE rule = 'integridad_auditoria'").fetchone()
    assert alerta["severity"] == "critica"


def test_no_se_borran_filas_recientes_pero_si_las_vencidas(nueva_cuenta):
    _, headers = nueva_cuenta()
    client.post("/me/consents/votos_analitica", headers=headers)  # deja una fila reciente
    with closing(connect()) as conn, conn:
        with pytest.raises(sqlite3.IntegrityError, match="solo inserción"):
            conn.execute("DELETE FROM audit_log")
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO audit_log (ts, action, outcome) VALUES ('2020-01-01T00:00:00+00:00', 'viejo', 'permitido')"
        )
        conn.execute("DELETE FROM audit_log WHERE action = 'viejo'")
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'viejo'").fetchone()[0] == 0


def test_leer_la_auditoria_queda_registrado(nueva_cuenta):
    admin_id, headers = nueva_cuenta("admin")
    client.get("/admin/audit", headers=headers)
    assert any(f["action"] == "audit:read" and f["actor_pseudonym"] == pseudonymize(admin_id) for f in _filas())


def test_sin_clave_de_auditoria_lo_protegido_responde_503(nueva_cuenta, monkeypatch):
    _, headers = nueva_cuenta()
    monkeypatch.delenv("GM_AUDIT_KEY")
    assert client.post("/me/consents/votos_analitica", headers=headers).status_code == 503
    assert client.get("/health").status_code == 200
