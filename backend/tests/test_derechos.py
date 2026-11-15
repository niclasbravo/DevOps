"""Ejercicio de derechos: acceso y portabilidad, rectificación y supresión, con su flujo de solicitudes."""

import datetime as dt
import json
from contextlib import closing

from fastapi.testclient import TestClient

from app.cli import reapply_deletions
from app.crypto import decrypt_email, pseudonymize
from app.db import connect
from app.main import app

client = TestClient(app)
CLAVE = "una-clave-larga-1"


def _juego() -> str:
    return client.get("/games", params={"limit": 1}).json()[0]["title"]


def _solicitar(headers: dict, tipo: str, clave: str = CLAVE, **extra):
    return client.post("/me/requests", headers=headers, json={"type": tipo, "current_password": clave, **extra})


def _cuenta(account_id: str):
    with closing(connect()) as conn:
        return conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


def _solicitudes() -> list:
    with closing(connect()) as conn:
        return conn.execute("SELECT * FROM rights_requests ORDER BY received_at").fetchall()


def _auditoria(accion: str) -> list:
    with closing(connect()) as conn:
        return conn.execute("SELECT * FROM audit_log WHERE action = ? ORDER BY id", (accion,)).fetchall()


# ---------- Acceso y portabilidad ----------


def test_exportacion_entrega_json_estructurado_y_descargable(nueva_cuenta):
    _, headers = nueva_cuenta()
    client.post("/me/consents/votos_analitica", headers=headers)
    client.post("/feedback", headers=headers, json={"game": _juego(), "vote": 1})
    resp = _solicitar(headers, "acceso")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    datos = resp.json()
    assert datos["formato"] == "gamematch-export/1"
    assert set(datos) == {"formato", "generado", "cuenta", "consentimientos", "votos", "solicitudes"}
    assert datos["cuenta"]["alias"].startswith("usuario_")
    assert datos["votos"][0]["voto"] == 1
    assert datos["consentimientos"][0]["finalidad"] == "votos_analitica"


def test_exportacion_no_incluye_datos_internos_ni_de_terceros(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    _, otro = nueva_cuenta()
    client.post("/feedback", headers=otro, json={"game": _juego(), "vote": -1})
    texto = json.dumps(_solicitar(headers, "acceso").json())
    for interno in ("password", "argon2", account_id, pseudonymize(account_id), "failed_logins", "audit"):
        assert interno not in texto
    assert '"voto": -1' not in texto


def test_exportacion_incluye_el_correo_descifrado(nueva_cuenta):
    _, headers = nueva_cuenta()
    client.post("/me/consents/correo_recuperacion", headers=headers)
    client.patch("/me", headers=headers, json={"current_password": CLAVE, "email": "persona@example.com"})
    assert _solicitar(headers, "acceso").json()["cuenta"]["correo"] == "persona@example.com"


def test_exportacion_queda_auditada(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    _solicitar(headers, "acceso")
    (fila,) = _auditoria("data:export")
    assert fila["outcome"] == "permitido" and fila["actor_pseudonym"] == pseudonymize(account_id)


# ---------- Verificación de identidad ----------


def test_contrasena_incorrecta_rechaza_y_cuenta_para_el_bloqueo(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    resp = _solicitar(headers, "acceso", clave="otra-clave-larga-2")
    assert resp.status_code == 403
    (solicitud,) = _solicitudes()
    assert solicitud["status"] == "cerrada" and solicitud["reason"] == "identidad_no_verificada"
    assert _cuenta(account_id)["failed_logins"] == 1


def test_cuenta_bloqueada_no_puede_solicitar_aunque_acierte(nueva_cuenta):
    _, headers = nueva_cuenta()
    for _ in range(5):
        _solicitar(headers, "acceso", clave="otra-clave-larga-2")
    assert _solicitar(headers, "acceso").status_code == 403


def test_sin_sesion_no_se_puede_solicitar():
    assert client.post("/me/requests", json={"type": "acceso", "current_password": CLAVE}).status_code == 401


# ---------- Flujo de solicitudes ----------


def test_solicitud_registra_estados_fechas_y_plazo_de_30_dias(nueva_cuenta):
    _, headers = nueva_cuenta()
    _solicitar(headers, "acceso")
    (solicitud,) = client.get("/me/requests", headers=headers).json()
    assert solicitud["tipo"] == "acceso" and solicitud["estado"] == "cerrada"
    recibida = dt.datetime.fromisoformat(solicitud["recibida"]).date()
    assert dt.date.fromisoformat(solicitud["vence"]) - recibida == dt.timedelta(days=30)
    assert solicitud["verificada"] and solicitud["cerrada"]


def test_cada_persona_ve_solo_sus_solicitudes(nueva_cuenta):
    _, uno = nueva_cuenta()
    _, otro = nueva_cuenta()
    _solicitar(uno, "acceso")
    assert client.get("/me/requests", headers=otro).json() == []


def test_solo_el_admin_ve_y_decide_solicitudes(nueva_cuenta):
    _, usuario = nueva_cuenta()
    _, analista = nueva_cuenta("analista")
    for headers in (usuario, analista):
        assert client.get("/admin/requests", headers=headers).status_code == 403


# ---------- Rectificación ----------


def test_rectificar_alias_actualiza_y_audita_solo_los_campos(nueva_cuenta):
    _, headers = nueva_cuenta()
    resp = _solicitar(headers, "rectificacion", changes={"alias": "nuevo_alias"})
    assert resp.status_code == 200
    assert client.post("/auth/login", json={"alias": "nuevo_alias", "password": CLAVE}).status_code == 200
    (fila,) = _auditoria("data:rectify")
    assert fila["detail"] == "campos=alias"
    assert "nuevo_alias" not in json.dumps([dict(r) for r in _auditoria("data:rectify")])


def test_rectificar_alias_ocupado_se_rechaza_con_motivo(nueva_cuenta):
    _, headers = nueva_cuenta()
    nueva_cuenta()
    resp = _solicitar(headers, "rectificacion", changes={"alias": "usuario_2"})
    assert resp.status_code == 409
    assert _solicitudes()[-1]["reason"] == "alias_no_disponible"


def test_rectificar_no_permite_campos_no_previstos(nueva_cuenta):
    _, headers = nueva_cuenta()
    assert _solicitar(headers, "rectificacion", changes={"role": "admin"}).status_code == 422
    assert _solicitar(headers, "rectificacion", changes={"alias": "con@arroba"}).status_code == 422


def test_rectificar_sin_cambios_se_rechaza(nueva_cuenta):
    _, headers = nueva_cuenta()
    assert _solicitar(headers, "rectificacion").status_code == 422


def test_rectificar_correo_exige_consentimiento_y_queda_cifrado(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    assert _solicitar(headers, "rectificacion", changes={"email": "persona@example.com"}).status_code == 409
    client.post("/me/consents/correo_recuperacion", headers=headers)
    assert _solicitar(headers, "rectificacion", changes={"email": "persona@example.com"}).status_code == 200
    guardado = _cuenta(account_id)["email_encrypted"]
    assert b"persona" not in guardado and decrypt_email(guardado) == "persona@example.com"


def test_rectificar_edad_queda_en_evaluacion_sin_guardar_el_cambio(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    resp = _solicitar(headers, "rectificacion", changes={"is_child": True})
    assert resp.status_code == 202
    assert _cuenta(account_id)["is_child"] == 0
    (solicitud,) = _solicitudes()
    assert solicitud["status"] == "en_evaluacion"


# ---------- Supresión ----------


def test_supresion_borra_cuenta_votos_y_revoca_consentimientos(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    client.post("/me/consents/votos_analitica", headers=headers)
    client.post("/feedback", headers=headers, json={"game": _juego(), "vote": 1})
    assert _solicitar(headers, "supresion").status_code == 200
    assert _cuenta(account_id) is None
    with closing(connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0
        ultimo = conn.execute("SELECT * FROM consent_events ORDER BY id DESC LIMIT 1").fetchone()
    assert ultimo["status"] == "revocado" and ultimo["actor"] == "sistema"
    assert client.get("/me", headers=headers).status_code == 401
    assert _auditoria("data:delete")[0]["outcome"] == "permitido"


def test_supresion_conserva_la_solicitud_sin_datos_personales(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    _solicitar(headers, "supresion")
    (solicitud,) = _solicitudes()
    assert solicitud["status"] == "cerrada" and solicitud["subject"] == pseudonymize(account_id)
    assert set(solicitud.keys()) == {
        "id", "subject", "type", "status", "received_at", "verified_at", "decided_at", "closed_at",
        "due_on", "extended", "reason",
    }


def test_supresion_del_ultimo_admin_se_rechaza(nueva_cuenta):
    account_id, headers = nueva_cuenta("admin")
    assert _solicitar(headers, "supresion").status_code == 409
    assert _cuenta(account_id) is not None
    assert _solicitudes()[-1]["reason"] == "ultimo_admin"


def test_supresion_con_retencion_por_incidente_espera_al_admin(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    _, admin = nueva_cuenta("admin")
    client.patch(f"/admin/accounts/{account_id}/legal-hold", headers=admin, json={"legal_hold": True})
    assert _solicitar(headers, "supresion").status_code == 202
    request_id = _solicitudes()[-1]["id"]
    decision = f"/admin/requests/{request_id}/decision"
    assert client.post(decision, headers=admin, json={"action": "ejecutar"}).status_code == 409
    client.patch(f"/admin/accounts/{account_id}/legal-hold", headers=admin, json={"legal_hold": False})
    assert client.post(decision, headers=admin, json={"action": "ejecutar"}).status_code == 200
    assert _cuenta(account_id) is None


def test_ya_no_existe_borrar_cuenta_sin_contrasena(nueva_cuenta):
    _, headers = nueva_cuenta()
    assert client.delete("/me", headers=headers).status_code == 405


def test_reaplicar_supresiones_tras_restaurar_una_copia(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    copia = dict(_cuenta(account_id))
    _solicitar(headers, "supresion")
    with closing(connect()) as conn, conn:  # simula restaurar una copia anterior a la supresión
        columnas = ", ".join(copia)
        conn.execute(f"INSERT INTO accounts ({columnas}) VALUES ({', '.join('?' * len(copia))})", tuple(copia.values()))
    assert reapply_deletions() == 1
    assert _cuenta(account_id) is None


# ---------- Decisiones del admin ----------


def test_admin_prorroga_una_sola_vez(nueva_cuenta):
    _, headers = nueva_cuenta()
    _, admin = nueva_cuenta("admin")
    _solicitar(headers, "rectificacion", changes={"is_child": True})
    solicitud = _solicitudes()[-1]
    decision = f"/admin/requests/{solicitud['id']}/decision"
    assert client.post(decision, headers=admin, json={"action": "prorrogar"}).status_code == 200
    nueva = dt.date.fromisoformat(_solicitudes()[-1]["due_on"])
    assert nueva - dt.date.fromisoformat(solicitud["due_on"]) == dt.timedelta(days=30)
    assert client.post(decision, headers=admin, json={"action": "prorrogar"}).status_code == 409


def test_admin_rechaza_solo_con_motivo_de_la_lista(nueva_cuenta):
    _, headers = nueva_cuenta()
    _, admin = nueva_cuenta("admin")
    _solicitar(headers, "rectificacion", changes={"is_child": True})
    decision = f"/admin/requests/{_solicitudes()[-1]['id']}/decision"
    assert client.post(decision, headers=admin, json={"action": "rechazar"}).status_code == 422
    assert client.post(decision, headers=admin, json={"action": "rechazar", "reason": "texto libre"}).status_code == 422
    resp = client.post(decision, headers=admin, json={"action": "rechazar", "reason": "dato_invalido"})
    assert resp.status_code == 200
    assert _solicitudes()[-1]["status"] == "cerrada"


def test_admin_lista_solicitudes_abiertas_y_marca_vencidas(nueva_cuenta):
    _, headers = nueva_cuenta()
    _, admin = nueva_cuenta("admin")
    _solicitar(headers, "rectificacion", changes={"is_child": True})
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE rights_requests SET due_on = '2000-01-01'")
    (fila,) = client.get("/admin/requests", headers=admin).json()
    assert fila["vencida"] is True and fila["estado"] == "en_evaluacion"
