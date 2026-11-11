"""La página no debe guardar identificadores ni escribir HTML dinámico."""

from pathlib import Path

import pytest

import app

STATIC = Path(app.__file__).parent / "static"
# La página es index.html más sus scripts: las reglas valen para todo el código que corre en el navegador.
HTML = "\n".join((STATIC / name).read_text(encoding="utf-8") for name in ("index.html", "app.js", "hero.js"))


def test_pagina_carga_su_script_y_estilos_locales():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<script src="/static/app.js"' in index
    assert 'href="/static/app.css"' in index
    assert "fonts.googleapis.com" not in HTML + (STATIC / "app.css").read_text(encoding="utf-8")


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
