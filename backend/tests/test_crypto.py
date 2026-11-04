"""Pruebas de seudónimos, contraseñas y cifrado."""

import pytest

from app.crypto import (
    MissingKeyError,
    decrypt_email,
    encrypt_email,
    hash_password,
    needs_rehash,
    pseudonymize,
    verify_password,
)


def test_seudonimo_es_estable_y_no_contiene_el_id():
    assert pseudonymize("abc") == pseudonymize("abc")
    assert "abc" not in pseudonymize("abc")
    assert len(pseudonymize("abc")) == 64


def test_seudonimo_cambia_con_la_clave(monkeypatch):
    antes = pseudonymize("abc")
    monkeypatch.setenv("GM_PSEUDONYM_KEY", "x" * 40)
    assert pseudonymize("abc") != antes


def test_hash_es_argon2id_y_verifica():
    stored = hash_password("una-clave-larga-1")
    assert stored.startswith("$argon2id$")
    assert verify_password(stored, "una-clave-larga-1")
    assert not verify_password(stored, "otra-clave-larga-2")


def test_hash_invalido_no_verifica_ni_lanza_error():
    assert not verify_password("esto-no-es-un-hash", "una-clave-larga-1")


def test_hash_actual_no_necesita_rehash():
    assert not needs_rehash(hash_password("una-clave-larga-1"))


def test_correo_cifrado_no_es_legible():
    token = encrypt_email("persona@example.com")
    assert b"persona" not in token
    assert decrypt_email(token) == "persona@example.com"


def test_rotacion_de_clave_permite_leer_correos_antiguos(monkeypatch):
    vieja = "v" * 40
    monkeypatch.setenv("GM_EMAIL_KEY", vieja)
    token = encrypt_email("persona@example.com")
    monkeypatch.setenv("GM_EMAIL_KEY", f"{'n' * 40},{vieja}")
    assert decrypt_email(token) == "persona@example.com"


@pytest.mark.parametrize("valor", [None, "corta"])
def test_clave_ausente_o_debil_falla(monkeypatch, valor):
    monkeypatch.delenv("GM_PSEUDONYM_KEY", raising=False)
    if valor:
        monkeypatch.setenv("GM_PSEUDONYM_KEY", valor)
    with pytest.raises(MissingKeyError):
        pseudonymize("abc")


def test_el_error_de_clave_no_revela_su_valor(monkeypatch):
    monkeypatch.setenv("GM_PSEUDONYM_KEY", "corta")
    with pytest.raises(MissingKeyError) as exc:
        pseudonymize("abc")
    assert "corta" not in str(exc.value)
