"""El archivo de portadas y precios se trata como dato externo: solo pasa lo que tiene la forma esperada."""

import json

from app.media import GameMedia, load_media


def _write(tmp_path, games, **extra):
    path = tmp_path / "game_media.json"
    path.write_text(json.dumps({"fetched_on": "2026-11-11", "currency": "CLP", "games": games, **extra}))
    return path


def test_carga_portada_ficha_tienda_y_precio(tmp_path):
    media = load_media(
        _write(tmp_path, {"7": {"cover": "co1abc", "url": "https://www.igdb.com/games/x", "steam": 42, "price": 8300}})
    )
    assert media.fetched_on == "2026-11-11" and media.currency == "CLP"
    assert media.get(7) == {
        "cover_id": "co1abc",
        "url": "https://www.igdb.com/games/x",
        "store_url": "https://store.steampowered.com/app/42/",
        "price": 8300,
    }


def test_descarta_valores_con_forma_inesperada(tmp_path):
    media = load_media(
        _write(
            tmp_path,
            {
                "1": {"cover": "../../x.jpg?a=<script>", "url": "javascript:alert(1)", "steam": "42", "price": -5},
                "2": {"cover": "co2", "url": "https://sitio-ajeno.example/games/x", "price": 100},
                "3": "no es un objeto",
            },
        )
    )
    assert media.get(1) == {"cover_id": None, "url": None, "store_url": None, "price": None}
    # Sin id de Steam no hay tienda, y un precio sin tienda no se muestra.
    assert media.get(2) == {"cover_id": "co2", "url": None, "store_url": None, "price": None}
    assert media.get(3)["cover_id"] is None


def test_archivo_ausente_o_danado_no_rompe_la_api(tmp_path):
    assert load_media(tmp_path / "no-existe.json").games == {}
    roto = tmp_path / "game_media.json"
    roto.write_text("{esto no es json")
    assert load_media(roto).games == {}
    roto.write_text(json.dumps({"games": ["lista", "en vez de objeto"]}))
    assert load_media(roto).games == {}


def test_juego_desconocido_devuelve_campos_vacios():
    assert GameMedia().get(None) == {"cover_id": None, "url": None, "store_url": None, "price": None}
    assert GameMedia().get("abc")["cover_id"] is None
