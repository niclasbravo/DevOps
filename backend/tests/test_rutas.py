"""Inventario de rutas: toda ruta que no es pública a propósito exige sesión. Una ruta nueva sin permiso rompe el CI."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PUBLICAS = {
    ("GET", "/health"), ("GET", "/games"), ("GET", "/stats"), ("POST", "/recommendations"),
    ("POST", "/auth/register"), ("POST", "/auth/login"), ("GET", "/consents/purposes"),
    ("POST", "/guardian/confirm"),  # la autoriza el token de un solo uso que recibe el representante
}

RUTAS = sorted(
    (metodo.upper(), ruta)
    for ruta, operaciones in app.openapi()["paths"].items()
    for metodo in operaciones
)


@pytest.mark.parametrize("metodo,ruta", [r for r in RUTAS if r not in PUBLICAS])
def test_ruta_no_publica_exige_sesion(metodo, ruta):
    url = ruta.replace("{account_id}", "x").replace("{request_id}", "x").replace("{alert_id}", "1")
    url = url.replace("{purpose}", "votos_analitica")
    assert client.request(metodo, url, json={}).status_code == 401


def test_las_rutas_publicas_existen():
    assert PUBLICAS <= set(RUTAS)
