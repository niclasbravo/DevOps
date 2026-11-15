"""Consentimiento por finalidad: registro, consulta, historial, revocación y efectos en los procesos."""

import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.cli import send_newsletter
from app.consent import grant, has_consent
from app.crypto import encrypt_email, pseudonymize
from app.db import PURPOSES, connect, init_db, upsert_vote
from app.main import app
from app.reports import feedback_report

client = TestClient(app)
CLAVE = "una-clave-larga-1"


def _estado(headers: dict) -> dict:
    return {f["finalidad"]: f for f in client.get("/me/consents", headers=headers).json()}


def _eventos(account_id: str) -> list[sqlite3.Row]:
    with closing(connect()) as conn:
        return conn.execute(
            "SELECT * FROM consent_events WHERE subject = ? ORDER BY id", (pseudonymize(account_id),)
        ).fetchall()


def test_finalidades_publicas_con_texto_y_version():
    finalidades = {f["code"]: f for f in client.get("/consents/purposes").json()}
    assert set(finalidades) == {"correo_recuperacion", "votos_analitica", "novedades_correo"}
    assert all(f["version"] and f["text"] for f in finalidades.values())
    assert finalidades["novedades_correo"]["allowed_for_children"] is False


def test_registro_sin_marcar_no_otorga_nada():
    client.post("/auth/register", json={"alias": "sin_marcar", "password": CLAVE})
    token = client.post("/auth/login", json={"alias": "sin_marcar", "password": CLAVE}).json()["access_token"]
    estado = _estado({"Authorization": f"Bearer {token}"})
    assert {f["estado"] for f in estado.values()} == {"no_otorgado"}


def test_registro_otorga_solo_las_finalidades_marcadas():
    client.post("/auth/register", json={"alias": "marca_una", "password": CLAVE, "consents": ["votos_analitica"]})
    token = client.post("/auth/login", json={"alias": "marca_una", "password": CLAVE}).json()["access_token"]
    estado = _estado({"Authorization": f"Bearer {token}"})
    assert estado["votos_analitica"]["estado"] == "otorgado"
    assert estado["correo_recuperacion"]["estado"] == "no_otorgado"
    assert estado["novedades_correo"]["estado"] == "no_otorgado"


def test_correo_sin_su_finalidad_se_rechaza():
    resp = client.post("/auth/register", json={"alias": "con_correo", "password": CLAVE, "email": "p@example.com"})
    assert resp.status_code == 400
    with closing(connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_otorgar_guarda_fecha_version_estado_y_actor(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    assert client.post("/me/consents/votos_analitica", headers=headers).status_code == 200
    (evento,) = _eventos(account_id)
    assert evento["purpose"] == "votos_analitica"
    assert evento["text_version"] == PURPOSES["votos_analitica"]["version"]
    assert evento["status"] == "otorgado" and evento["actor"] == "titular"
    assert len(evento["ts"]) == len("2026-11-11T12:00:00+00:00")  # fecha y hora, en segundos


def test_cada_finalidad_es_independiente(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    client.post("/me/consents/correo_recuperacion", headers=headers)
    estado = _estado(headers)
    assert estado["correo_recuperacion"]["estado"] == "otorgado"
    assert estado["votos_analitica"]["estado"] == "no_otorgado"
    assert estado["novedades_correo"]["estado"] == "no_otorgado"


def test_otorgar_dos_veces_no_duplica_el_evento(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    client.post("/me/consents/votos_analitica", headers=headers)
    client.post("/me/consents/votos_analitica", headers=headers)
    assert len(_eventos(account_id)) == 1


def test_revocar_registra_fecha_y_conserva_el_historial(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    client.post("/me/consents/votos_analitica", headers=headers)
    assert client.delete("/me/consents/votos_analitica", headers=headers).status_code == 200
    historial = client.get("/me/consents/history", headers=headers).json()
    assert [h["estado"] for h in historial] == ["otorgado", "revocado"]
    assert all(h["fecha"] for h in historial)
    assert _estado(headers)["votos_analitica"]["estado"] == "no_otorgado"


def test_finalidad_inexistente_se_rechaza(nueva_cuenta):
    _, headers = nueva_cuenta()
    assert client.post("/me/consents/vender_datos", headers=headers).status_code == 422


def test_version_nueva_invalida_el_consentimiento_anterior(nueva_cuenta, monkeypatch):
    account_id, headers = nueva_cuenta()
    client.post("/me/consents/votos_analitica", headers=headers)
    nueva = {**PURPOSES["votos_analitica"], "version": "2", "text": "Texto cambiado de forma material."}
    monkeypatch.setitem(PURPOSES, "votos_analitica", nueva)
    init_db()  # al arrancar, la app publica la versión nueva
    estado = _estado(headers)["votos_analitica"]
    assert estado["estado"] == "no_otorgado" and estado["version_aceptada"] == "1"
    client.post("/me/consents/votos_analitica", headers=headers)
    assert _estado(headers)["votos_analitica"]["estado"] == "otorgado"
    with closing(connect()) as conn:  # el texto de la versión 1 sigue disponible
        versiones = conn.execute("SELECT version FROM consent_purpose_versions WHERE code = 'votos_analitica'")
        assert {v[0] for v in versiones} == {"1", "2"}


def test_revocar_correo_lo_borra_en_la_misma_operacion(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    client.post("/me/consents/correo_recuperacion", headers=headers)
    client.patch("/me", headers=headers, json={"current_password": CLAVE, "email": "p@example.com"})
    assert client.get("/me", headers=headers).json()["has_email"] is True
    client.delete("/me/consents/correo_recuperacion", headers=headers)
    assert client.get("/me", headers=headers).json()["has_email"] is False


def test_correo_por_patch_exige_su_finalidad(nueva_cuenta):
    _, headers = nueva_cuenta()
    resp = client.patch("/me", headers=headers, json={"current_password": CLAVE, "email": "p@example.com"})
    assert resp.status_code == 409
    assert client.get("/me", headers=headers).json()["has_email"] is False


def _votar(account_id: str, juego: str = "Hades") -> None:
    with closing(connect()) as conn, conn:
        upsert_vote(conn, pseudonymize(account_id), juego, 1, "2026-11-11")


def test_reporte_solo_cuenta_votos_con_consentimiento(nueva_cuenta):
    cuentas = [nueva_cuenta() for _ in range(6)]
    for account_id, headers in cuentas:
        _votar(account_id)
    for _, headers in cuentas[:5]:
        client.post("/me/consents/votos_analitica", headers=headers)
    assert feedback_report() == [{"game": "Hades", "votos": 5, "positivos": 5}]


def test_revocar_saca_los_votos_del_reporte_desde_ese_momento(nueva_cuenta):
    cuentas = [nueva_cuenta() for _ in range(5)]
    for account_id, headers in cuentas:
        _votar(account_id)
        client.post("/me/consents/votos_analitica", headers=headers)
    assert feedback_report()[0]["votos"] == 5
    client.delete("/me/consents/votos_analitica", headers=cuentas[0][1])
    assert feedback_report() == []  # quedan 4: bajo el umbral de 5
    # El voto sigue guardado para su dueño: guardar es parte del servicio.
    assert client.get("/me/feedback", headers=cuentas[0][1]).json()[0]["game"] == "Hades"


def test_consentimiento_para_votos_no_habilita_novedades(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    client.post("/me/consents/votos_analitica", headers=headers)
    with closing(connect()) as conn:
        assert has_consent(conn, pseudonymize(account_id), "votos_analitica")
        assert not has_consent(conn, pseudonymize(account_id), "novedades_correo")
        assert not has_consent(conn, pseudonymize(account_id), "correo_recuperacion")


def test_correo_de_recuperacion_no_habilita_novedades(nueva_cuenta, buzon):
    """El correo guardado para recuperar la cuenta no se usa para otra finalidad."""
    solo_recuperacion, h1 = nueva_cuenta()
    con_novedades, h2 = nueva_cuenta()
    for headers, correo in ((h1, "uno@example.com"), (h2, "dos@example.com")):
        client.post("/me/consents/correo_recuperacion", headers=headers)
        client.patch("/me", headers=headers, json={"current_password": CLAVE, "email": correo})
    client.post("/me/consents/novedades_correo", headers=h2)
    assert send_newsletter() == 1
    assert [m["to"] for m in buzon()] == ["dos@example.com"]


def test_revocar_novedades_rige_en_el_siguiente_envio(nueva_cuenta, buzon):
    _, headers = nueva_cuenta()
    client.post("/me/consents/correo_recuperacion", headers=headers)
    client.patch("/me", headers=headers, json={"current_password": CLAVE, "email": "uno@example.com"})
    client.post("/me/consents/novedades_correo", headers=headers)
    assert send_newsletter() == 1
    client.delete("/me/consents/novedades_correo", headers=headers)
    assert send_newsletter() == 0


def test_consentimientos_se_auditan_sin_datos_personales(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    client.post("/me/consents/votos_analitica", headers=headers)
    client.delete("/me/consents/votos_analitica", headers=headers)
    with closing(connect()) as conn:
        filas = conn.execute("SELECT * FROM audit_log WHERE action LIKE 'consent:%' ORDER BY id").fetchall()
    assert [f["action"] for f in filas] == ["consent:grant", "consent:revoke"]
    assert all(f["actor_pseudonym"] == pseudonymize(account_id) for f in filas)
    assert account_id not in str([dict(f) for f in filas])


@pytest.mark.parametrize("sentencia", ["UPDATE consent_events SET status = 'otorgado'", "DELETE FROM consent_events"])
def test_historial_no_se_puede_reescribir(nueva_cuenta, sentencia):
    account_id, headers = nueva_cuenta()
    client.post("/me/consents/votos_analitica", headers=headers)
    client.delete("/me/consents/votos_analitica", headers=headers)
    with closing(connect()) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(sentencia)


def test_eventos_no_guardan_datos_innecesarios():
    with closing(connect()) as conn:
        columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(consent_events)")}
    assert columnas == {"id", "subject", "purpose", "text_version", "status", "actor", "ts"}


def test_evento_guarda_seudonimo_y_no_el_id(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    client.post("/me/consents/votos_analitica", headers=headers)
    (evento,) = _eventos(account_id)
    assert evento["subject"] == pseudonymize(account_id) != account_id


def test_votos_antiguos_sin_consentimiento_no_entran_al_reporte():
    with closing(connect()) as conn, conn:
        for n in range(5):
            upsert_vote(conn, pseudonymize(f"legacy:{n}"), "Hades", 1, "2026-10-01")
    assert feedback_report() == []
    with closing(connect()) as conn, conn:
        for n in range(5):
            grant(conn, pseudonymize(f"legacy:{n}"), "votos_analitica", "titular")
    assert feedback_report()[0]["votos"] == 5


def test_base_del_hito_2_se_migra_sin_perder_cuentas(tmp_path, monkeypatch):
    ruta = tmp_path / "hito2.db"
    with closing(sqlite3.connect(ruta)) as conn, conn:
        conn.executescript(
            """CREATE TABLE roles (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
               CREATE TABLE accounts (id TEXT PRIMARY KEY, alias TEXT NOT NULL UNIQUE COLLATE NOCASE,
                 password_hash TEXT NOT NULL, email_encrypted BLOB, role_id INTEGER NOT NULL REFERENCES roles(id),
                 is_active INTEGER NOT NULL DEFAULT 1, failed_logins INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
                 created_on TEXT NOT NULL);
               INSERT INTO roles (name) VALUES ('usuario');"""
        )
        conn.execute(
            """INSERT INTO accounts (id, alias, password_hash, email_encrypted, role_id, created_on)
               VALUES (?, ?, ?, ?, 1, ?)""",
            ("id-1", "antigua", "hash", encrypt_email("p@example.com"), "2026-11-04"),
        )
    monkeypatch.setenv("GM_DB_PATH", str(ruta))
    init_db()
    with closing(connect()) as conn:
        cuenta = conn.execute("SELECT * FROM accounts WHERE alias = 'antigua'").fetchone()
    assert cuenta["is_child"] == 0 and cuenta["pending_guardian"] == 0 and cuenta["is_active"] == 1
