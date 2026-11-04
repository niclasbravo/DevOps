"""Logging sin datos personales ni secretos.

El código no registra alias, correos ni tokens. Este filtro es la segunda barrera, por si un mensaje
de una biblioteca los incluye.
"""

import logging
import re

REDACTED = "[REDACTADO]"

_PATTERNS = [
    re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"),  # JWT
    re.compile(r"\bBearer\s+[\w.~+/=-]+", re.IGNORECASE),
    re.compile(r"[^@\s\"']+@[^@\s\"']+\.[^@\s\"']+"),  # correo
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE),  # id de cuenta
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in _PATTERNS:
            message = pattern.sub(REDACTED, message)
        record.msg, record.args = message, ()
        record.exc_info = record.exc_text = None  # un traceback repite el mensaje de la excepción
        return True


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO)
    # El log de acceso de uvicorn guarda la IP de cada cliente, que el sistema decidió no registrar.
    logging.getLogger("uvicorn.access").disabled = True
    for logger in (logging.getLogger(), logging.getLogger("uvicorn"), logging.getLogger("uvicorn.error")):
        for handler in logger.handlers:
            if not any(isinstance(f, RedactingFilter) for f in handler.filters):
                handler.addFilter(RedactingFilter())
