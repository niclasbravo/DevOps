"""Envío de correos simulado.

El proyecto no tiene servidor de correo. Cada mensaje se escribe como un archivo JSON en `GM_OUTBOX_DIR`,
que las pruebas leen para seguir el enlace. En producción esta función enviaría por SMTP y el destinatario
no quedaría escrito en disco. Sin `GM_OUTBOX_DIR` el envío falla: nada que dependa de un correo se crea a medias.
"""

import datetime as dt
import json
import os
import uuid
from pathlib import Path


class MailerUnavailable(RuntimeError):
    """No hay buzón configurado."""


def outbox_dir() -> Path:
    value = os.environ.get("GM_OUTBOX_DIR")
    if not value:
        raise MailerUnavailable("GM_OUTBOX_DIR no está configurado")
    return Path(value)


def ensure_available() -> None:
    outbox_dir()


def send(to: str, subject: str, body: str) -> None:
    directory = outbox_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    message = {"to": to, "subject": subject, "body": body}
    (directory / f"{stamp}-{uuid.uuid4().hex}.json").write_text(json.dumps(message, ensure_ascii=False), "utf-8")
