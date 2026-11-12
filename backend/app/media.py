"""Portada, ficha y precio de cada juego, para mostrarlos en la página.

`python -m ml.media` los descarga de IGDB y de la tienda de Steam y los deja en un JSON junto al
modelo. Son datos de presentación: no participan en la recomendación, y si el archivo falta o viene
dañado la API responde igual, solo que sin portadas ni precios.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("gamematch.media")

MEDIA_FILENAME = "game_media.json"
# Lo que llega al navegador se arma con estos valores: se acepta solo la forma esperada.
COVER_ID = re.compile(r"[a-z0-9_]{2,40}")
IGDB_GAME_URL = "https://www.igdb.com/games/"
STEAM_APP_URL = "https://store.steampowered.com/app/{}/"
MAX_PRICE = 10_000_000


@dataclass(frozen=True)
class GameMedia:
    games: dict[int, dict] = field(default_factory=dict)
    fetched_on: str | None = None
    currency: str | None = None

    def get(self, game_id) -> dict:
        """Campos de presentación de un juego; todos None si no hay datos de ese juego."""
        try:
            entry = self.games.get(int(game_id), {})
        except (TypeError, ValueError):
            entry = {}
        return {
            "cover_id": entry.get("cover"),
            "url": entry.get("url"),
            "store_url": STEAM_APP_URL.format(entry["steam"]) if "steam" in entry else None,
            "price": entry.get("price"),
        }


def _clean(entry: dict) -> dict:
    """Deja solo los campos con la forma esperada: el archivo se trata como dato externo."""
    out = {}
    cover = entry.get("cover")
    if isinstance(cover, str) and COVER_ID.fullmatch(cover):
        out["cover"] = cover
    url = entry.get("url")
    if isinstance(url, str) and url.startswith(IGDB_GAME_URL) and len(url) <= 300:
        out["url"] = url
    steam = entry.get("steam")
    if isinstance(steam, int) and not isinstance(steam, bool) and steam > 0:
        out["steam"] = steam
        price = entry.get("price")
        if isinstance(price, int) and not isinstance(price, bool) and 0 < price <= MAX_PRICE:
            out["price"] = price
    return out


def load_media(path: Path) -> GameMedia:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        games = {int(game_id): _clean(entry) for game_id, entry in raw["games"].items() if isinstance(entry, dict)}
    except FileNotFoundError:
        logger.info("Sin %s: la API responde sin portadas ni precios", path.name)
        return GameMedia()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        logger.warning("%s no se pudo leer: la API responde sin portadas ni precios", path.name)
        return GameMedia()
    fetched_on = raw.get("fetched_on") if isinstance(raw.get("fetched_on"), str) else None
    currency = raw.get("currency") if isinstance(raw.get("currency"), str) else None
    return GameMedia(games={k: v for k, v in games.items() if v}, fetched_on=fetched_on, currency=currency)
