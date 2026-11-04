"""Pruebas de registro, login y tokens."""

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth import create_token, decode_token
from app.crypto import decrypt_email, get_key, pseudonymize
from app.db import connect
from app.main import app

client = TestClient(app)
CLAVE = "una-clave-larga-1"


def _registrar(alias: str = "nico", password: str = CLAVE, **extra):
    return client.post("/auth/register", json={"alias": alias, "password": password, **extra})


def _login(alias: str = "nico", password: str = CLAVE):
    return client.post("/auth/login", json={"alias": alias, "password": password})


def _cuenta(alias: str = "nico"):
    with closing(connect()) as conn:
        return conn.execute(
            "SELECT a.*, r.name AS role FROM accounts a JOIN roles r ON r.id = a.role_id WHERE a.alias = ?", (alias,)
        ).fetchone()


def test_registro_crea_usuario_con_hash_y_solo_fecha():
    resp = _registrar()
    assert resp.status_code == 201
    assert resp.json() == {"alias": "nico"}
    cuenta = _cuenta()
    assert cuenta["role"] == "usuario"
    assert cuenta["password_hash"].startswith("$argon2id$")
    assert len(cuenta["created_on"]) == 10


def test_registro_no_acepta_rol():
    assert _registrar(role="admin").status_code == 422
    assert _cuenta() is None


def test_registro_rechaza_campos_personales_extra():
    assert _registrar(birthdate="2000-01-01").status_code == 422


def test_correo_opcional_queda_cifrado():
    assert _registrar(email="persona@example.com").status_code == 201
    guardado = _cuenta()["email_encrypted"]
    assert b"persona" not in guardado
    assert decrypt_email(guardado) == "persona@example.com"


def test_correo_invalido_se_rechaza():
    assert _registrar(email="no-es-correo").status_code == 422


def test_alias_no_distingue_mayusculas():
    assert _registrar("Nico").status_code == 201
    assert _registrar("nico").status_code == 409


@pytest.mark.parametrize("alias", ["persona@example.com", "ab", "con espacio", "x" * 31])
def test_alias_invalido_se_rechaza(alias):
    assert _registrar(alias).status_code == 422


@pytest.mark.parametrize("largo", [11, 129, 1_000_000])
def test_largo_de_contrasena_invalido_da_422(largo):
    assert _registrar(password="x" * largo).status_code == 422


def test_login_entrega_un_token_valido():
    _registrar()
    resp = _login()
    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"
    assert decode_token(resp.json()["access_token"]) == _cuenta()["id"]


def test_token_dura_treinta_minutos():
    claims = jwt.decode(create_token("abc"), options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 30 * 60


def test_login_mismo_error_si_el_alias_no_existe():
    _registrar()
    mala_clave = _login(password="otra-clave-larga-2")
    sin_cuenta = _login(alias="fantasma")
    assert mala_clave.status_code == sin_cuenta.status_code == 401
    assert mala_clave.json() == sin_cuenta.json()


def test_login_con_contrasena_gigante_da_422():
    assert _login(password="x" * 1_000_000).status_code == 422


def test_bloqueo_tras_cinco_fallos():
    _registrar()
    for _ in range(5):
        assert _login(password="otra-clave-larga-2").status_code == 401
    assert _login().status_code == 401


def test_el_bloqueo_expira():
    _registrar()
    for _ in range(5):
        _login(password="otra-clave-larga-2")
    pasado = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat()
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE accounts SET locked_until = ?", (pasado,))
    assert _login().status_code == 200


def test_login_correcto_reinicia_los_fallos():
    _registrar()
    for _ in range(4):
        _login(password="otra-clave-larga-2")
    assert _login().status_code == 200
    assert _cuenta()["failed_logins"] == 0


def test_cuenta_suspendida_no_inicia_sesion():
    _registrar()
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE accounts SET is_active = 0")
    assert _login().status_code == 401


def test_sin_clave_jwt_login_responde_503(monkeypatch):
    _registrar()
    monkeypatch.delenv("GM_JWT_SECRET")
    assert _login().status_code == 503


def test_token_vencido_es_invalido():
    ahora = dt.datetime.now(dt.timezone.utc)
    token = jwt.encode(
        {"sub": "abc", "iat": ahora - dt.timedelta(hours=1), "exp": ahora - dt.timedelta(minutes=1)},
        get_key("GM_JWT_SECRET"),
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token)


def test_token_manipulado_es_invalido():
    token = create_token("abc")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token[:-2] + ("aa" if not token.endswith("aa") else "bb"))


def test_token_sin_firma_es_invalido():
    token = jwt.encode({"sub": "abc", "exp": 9_999_999_999}, "", algorithm="none")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token)


def _set(sql: str, *args) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(sql, args)


def _auditoria() -> list:
    with closing(connect()) as conn:
        return conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()


def _juego() -> str:
    return client.get("/games", params={"limit": 1}).json()[0]["title"]


def test_me_devuelve_solo_datos_minimos(nueva_cuenta):
    _, headers = nueva_cuenta()
    body = client.get("/me", headers=headers).json()
    assert set(body) == {"alias", "role", "created_on", "has_email"}
    assert body["role"] == "usuario" and body["has_email"] is False


@pytest.mark.parametrize("cabecera", [None, "Bearer token-falso", "Basic abc"])
def test_me_sin_token_valido_da_401(cabecera):
    headers = {"Authorization": cabecera} if cabecera else {}
    assert client.get("/me", headers=headers).status_code == 401


def test_token_de_cuenta_borrada_da_401(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    _set("DELETE FROM accounts WHERE id = ?", account_id)
    assert client.get("/me", headers=headers).status_code == 401


def test_token_de_cuenta_suspendida_da_401(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    _set("UPDATE accounts SET is_active = 0 WHERE id = ?", account_id)
    assert client.get("/me", headers=headers).status_code == 401


def test_cambio_de_rol_rige_de_inmediato(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    juego = {"game": _juego(), "vote": 1}
    assert client.post("/feedback", headers=headers, json=juego).status_code == 201
    _set("UPDATE accounts SET role_id = (SELECT id FROM roles WHERE name = 'analista') WHERE id = ?", account_id)
    assert client.post("/feedback", headers=headers, json=juego).status_code == 403


def test_feedback_sin_token_da_401():
    assert client.post("/feedback", json={"game": _juego(), "vote": 1}).status_code == 401


def test_feedback_ya_no_acepta_user_id(nueva_cuenta):
    _, headers = nueva_cuenta()
    cuerpo = {"user_id": "a", "game": "Hades", "vote": 1}
    assert client.post("/feedback", headers=headers, json=cuerpo).status_code == 422


def test_usuario_solo_ve_sus_votos(nueva_cuenta):
    _, uno = nueva_cuenta()
    _, otro = nueva_cuenta()
    game = _juego()
    client.post("/feedback", headers=uno, json={"game": game, "vote": 1})
    assert client.get("/me/feedback", headers=otro).json() == []
    (voto,) = client.get("/me/feedback", headers=uno).json()
    assert set(voto) == {"game", "vote", "voted_on"} and voto["game"] == game


def test_patch_me_exige_contrasena_actual(nueva_cuenta):
    _, headers = nueva_cuenta()
    cuerpo = {"current_password": "otra-clave-larga-2", "new_password": "clave-nueva-larga-3"}
    assert client.patch("/me", headers=headers, json=cuerpo).status_code == 403


def test_patch_me_cambia_la_contrasena(nueva_cuenta):
    _, headers = nueva_cuenta()
    cuerpo = {"current_password": CLAVE, "new_password": "clave-nueva-larga-3"}
    assert client.patch("/me", headers=headers, json=cuerpo).status_code == 200
    alias = client.get("/me", headers=headers).json()["alias"]
    assert _login(alias, CLAVE).status_code == 401
    assert _login(alias, "clave-nueva-larga-3").status_code == 200


def test_patch_me_guarda_y_borra_el_correo(nueva_cuenta):
    _, headers = nueva_cuenta()
    client.patch("/me", headers=headers, json={"current_password": CLAVE, "email": "persona@example.com"})
    assert client.get("/me", headers=headers).json()["has_email"] is True
    client.patch("/me", headers=headers, json={"current_password": CLAVE, "email": None})
    assert client.get("/me", headers=headers).json()["has_email"] is False


def test_patch_me_no_permite_cambiar_el_rol(nueva_cuenta):
    _, headers = nueva_cuenta()
    assert client.patch("/me", headers=headers, json={"current_password": CLAVE, "role": "admin"}).status_code == 422


def test_borrar_cuenta_conserva_votos_sin_vinculo(nueva_cuenta):
    account_id, headers = nueva_cuenta()
    client.post("/feedback", headers=headers, json={"game": _juego(), "vote": 1})
    assert client.delete("/me", headers=headers).status_code == 204
    with closing(connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM accounts WHERE id = ?", (account_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1


def test_el_ultimo_admin_no_puede_borrarse(nueva_cuenta):
    _, headers = nueva_cuenta("admin")
    assert client.delete("/me", headers=headers).status_code == 409


def test_un_admin_puede_borrarse_si_queda_otro(nueva_cuenta):
    _, headers = nueva_cuenta("admin")
    nueva_cuenta("admin")
    assert client.delete("/me", headers=headers).status_code == 204


def test_denegacion_queda_auditada_con_seudonimo(nueva_cuenta):
    account_id, headers = nueva_cuenta("analista")
    assert client.post("/feedback", headers=headers, json={"game": "Hades", "vote": 1}).status_code == 403
    (fila,) = _auditoria()
    assert fila["outcome"] == "denegado" and fila["action"] == "feedback:write"
    assert fila["actor_pseudonym"] == pseudonymize(account_id)


def test_acceso_sin_sesion_queda_auditado_sin_actor():
    client.get("/me")
    (fila,) = _auditoria()
    assert fila["outcome"] == "denegado" and fila["actor_pseudonym"] is None


def test_stats_no_expone_datos_del_modelo():
    assert "model" not in client.get("/stats").json()


def test_bloqueo_resiste_intentos_concurrentes():
    _registrar()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: _login(password="otra-clave-larga-2"), range(8)))
    assert _cuenta()["locked_until"] is not None


def test_sin_clave_de_seudonimos_patch_me_no_se_aplica(nueva_cuenta, monkeypatch):
    _, headers = nueva_cuenta()
    alias = client.get("/me", headers=headers).json()["alias"]
    monkeypatch.delenv("GM_PSEUDONYM_KEY")
    cuerpo = {"current_password": CLAVE, "new_password": "clave-nueva-larga-3"}
    assert client.patch("/me", headers=headers, json=cuerpo).status_code == 503
    assert _login(alias, CLAVE).status_code == 200


def test_sin_clave_de_seudonimos_delete_me_no_se_aplica(nueva_cuenta, monkeypatch):
    _, headers = nueva_cuenta()
    monkeypatch.delenv("GM_PSEUDONYM_KEY")
    assert client.delete("/me", headers=headers).status_code == 503
    with closing(connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
