"""La página no debe guardar identificadores ni escribir HTML dinámico."""

from pathlib import Path

import pytest

import app

HTML = (Path(app.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "prohibido", ["localStorage", "sessionStorage", "document.cookie", "gamematch-uid", "user_id", "innerHTML"]
)
def test_pagina_no_guarda_identificadores_ni_usa_html_dinamico(prohibido):
    assert prohibido not in HTML


def test_pagina_envia_el_token_al_votar():
    assert "Authorization" in HTML and "Bearer" in HTML


@pytest.mark.parametrize("ruta", ["/auth/login", "/auth/register"])
def test_pagina_usa_los_endpoints_de_sesion(ruta):
    assert ruta in HTML


def test_pagina_vuelve_a_pedir_login_si_la_sesion_vence():
    assert "resp.status === 401" in HTML


GUARDIAN = (Path(app.__file__).parent / "static" / "guardian.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("prohibido", ["localStorage", "sessionStorage", "document.cookie", "innerHTML"])
def test_pagina_del_representante_no_guarda_nada_ni_usa_html_dinamico(prohibido):
    assert prohibido not in GUARDIAN


def test_pagina_del_representante_toma_el_token_del_fragmento_y_exige_declaracion():
    assert "location.hash" in GUARDIAN and "declares_legal_representative" in GUARDIAN


def test_registro_no_marca_finalidades_por_defecto():
    assert "checked = true" not in HTML and " checked>" not in HTML and "checked=" not in HTML


@pytest.mark.parametrize("ruta", ["/consents/purposes", "/me/consents", "/me/consents/history"])
def test_pagina_usa_los_endpoints_de_consentimiento(ruta):
    assert ruta in HTML


def test_pagina_del_representante_se_sirve():
    from fastapi.testclient import TestClient

    from app.main import app as api

    resp = TestClient(api).get("/guardian.html")
    assert resp.status_code == 200 and "declares_legal_representative" in resp.text
