"""Respuestas de error que no repiten lo que envió el cliente ni detalles internos."""

import logging
import traceback
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.crypto import MissingKeyError
from app.mailer import MailerUnavailable

logger = logging.getLogger("gamematch.errors")


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # El 422 por defecto incluye `input`, que devolvería la contraseña enviada.
    detail = [{"campo": ".".join(str(part) for part in e["loc"]), "error": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})


async def _missing_key(_: Request, __: MissingKeyError) -> JSONResponse:
    # Sin claves no se guarda nada; la respuesta no dice cuál falta.
    return JSONResponse(status_code=503, content={"detail": "Servicio no disponible por falta de configuración"})


async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    # Se registra el tipo y el lugar, no el mensaje: puede traer datos de la petición.
    error_id = str(uuid.uuid4())
    frames = traceback.extract_tb(exc.__traceback__)
    place = f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "?"
    logger.error("Error interno %s: %s en %s", error_id, type(exc).__name__, place)
    return JSONResponse(status_code=500, content={"detail": "Error interno", "error_id": error_id})


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(MissingKeyError, _missing_key)
    app.add_exception_handler(MailerUnavailable, _missing_key)
    app.add_exception_handler(Exception, _unhandled)
