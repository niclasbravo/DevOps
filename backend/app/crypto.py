"""Seudónimos, contraseñas y cifrado de campos sensibles.

Las claves solo se leen de variables de entorno, sin valores por defecto: si falta una,
la operación falla en vez de continuar con una clave conocida. Cada clave tiene un solo
propósito, así que filtrar una no compromete las demás.
"""

import base64
import hashlib
import hmac
import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, MultiFernet

MIN_KEY_BYTES = 32

_hasher = PasswordHasher()  # Argon2id con los parámetros recomendados por la biblioteca


class MissingKeyError(RuntimeError):
    """Falta una clave o es demasiado corta. El mensaje nunca incluye su valor."""


def _check_key(name: str, value: str | None) -> bytes:
    key = (value or "").encode("utf-8")
    if len(key) < MIN_KEY_BYTES:
        raise MissingKeyError(f"{name} no está configurada o tiene menos de {MIN_KEY_BYTES} bytes")
    return key


def get_key(name: str) -> bytes:
    return _check_key(name, os.environ.get(name))


def pseudonymize(value: str) -> str:
    """HMAC-SHA256: sin la clave no se puede recalcular ni comprobar a quién corresponde."""
    return hmac.new(get_key("GM_PSEUDONYM_KEY"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    return _hasher.check_needs_rehash(stored)


def _email_cipher() -> MultiFernet:
    """GM_EMAIL_KEY admite varias claves separadas por coma: la primera cifra y todas descifran."""
    names = os.environ.get("GM_EMAIL_KEY", "").split(",")
    keys = [_check_key("GM_EMAIL_KEY", name.strip()) for name in names]
    return MultiFernet([Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest())) for key in keys])


def encrypt_email(email: str) -> bytes:
    return _email_cipher().encrypt(email.encode("utf-8"))


def decrypt_email(token: bytes) -> str:
    return _email_cipher().decrypt(token).decode("utf-8")
