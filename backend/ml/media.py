"""Portadas, fichas y precios de los juegos del modelo (datos para mostrar, no para entrenar).

Uso:
    python -m ml.media                  # lee models/gamematch.joblib y escribe models/game_media.json
    python -m ml.media --skip-prices    # solo IGDB, sin consultar la tienda de Steam

De IGDB salen la portada (cover.image_id), la ficha (url) y el id del juego en Steam; de la tienda de
Steam, el precio base en Chile. El resultado es un JSON aparte del modelo: no cambia sus vectores ni
su hash, y la API funciona igual (sin portadas) si el archivo falta.
"""

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import requests

from app.media import MEDIA_FILENAME
from app.model import DEFAULT_MODEL_PATH, load_model
from ml.igdb import PAGE_SIZE, IGDBClient

FIELDS = "cover.image_id,url,websites.url,external_games.uid,external_games.external_game_source"
STEAM_SOURCE = 1  # external_game_source de IGDB para Steam
STEAM_APP_URL = re.compile(r"store\.steampowered\.com/app/(\d+)")
STEAM_PRICE_URL = "https://store.steampowered.com/api/appdetails"
STEAM_BATCH = 100
STEAM_INTERVAL = 1.6  # la tienda admite unas 200 consultas cada 5 minutos
COUNTRY = "cl"


def steam_app_id(game: dict) -> int | None:
    """Id del juego en Steam: primero el enlace a la tienda, después la referencia externa de IGDB."""
    for site in game.get("websites") or []:
        match = STEAM_APP_URL.search(site.get("url", ""))
        if match:
            return int(match.group(1))
    for ext in game.get("external_games") or []:
        if ext.get("external_game_source") == STEAM_SOURCE and str(ext.get("uid", "")).isdigit():
            return int(ext["uid"])
    return None


def fetch_igdb_media(client: IGDBClient, ids: list[int]) -> dict[int, dict]:
    media: dict[int, dict] = {}
    for start in range(0, len(ids), PAGE_SIZE):
        chunk = ids[start : start + PAGE_SIZE]
        body = f"fields {FIELDS}; where id = ({','.join(map(str, chunk))}); limit {PAGE_SIZE};"
        for game in client.query("games", body):
            entry = {
                "cover": (game.get("cover") or {}).get("image_id"),
                "url": game.get("url"),
                "steam": steam_app_id(game),
            }
            media[game["id"]] = {k: v for k, v in entry.items() if v}
        print(f"  IGDB: {min(start + PAGE_SIZE, len(ids))} de {len(ids)} juegos")
    return media


def fetch_steam_prices(
    app_ids: list[int], session: requests.Session | None = None
) -> tuple[dict[int, int], str | None]:
    """Precio base (sin ofertas) de cada juego en la tienda de Steam de Chile, en su moneda.

    Los juegos gratuitos o retirados de la tienda no traen precio y quedan fuera.
    """
    session = session or requests.Session()
    prices: dict[int, int] = {}
    currency = None
    for start in range(0, len(app_ids), STEAM_BATCH):
        chunk = app_ids[start : start + STEAM_BATCH]
        params = {"appids": ",".join(map(str, chunk)), "filters": "price_overview", "cc": COUNTRY}
        data = None
        for attempt in range(4):
            resp = session.get(STEAM_PRICE_URL, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                break
            time.sleep(15 * (attempt + 1))  # 429 o 5xx: la tienda pide esperar
        for app_id, item in (data or {}).items():
            overview = item.get("data", {}).get("price_overview") if isinstance(item.get("data"), dict) else None
            if item.get("success") and overview and overview.get("initial"):
                prices[int(app_id)] = round(overview["initial"] / 100)
                currency = currency or overview.get("currency")
        print(f"  Steam: {min(start + STEAM_BATCH, len(app_ids))} de {len(app_ids)} juegos")
        time.sleep(STEAM_INTERVAL)
    return prices, currency


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--out", type=Path, help=f"por defecto, {MEDIA_FILENAME} junto al modelo")
    parser.add_argument("--skip-prices", action="store_true")
    args = parser.parse_args(argv)

    model = load_model(args.model)
    if model is None:
        print("No hay un modelo verificado: entrena primero con python -m ml.train", file=sys.stderr)
        return 1
    ids = sorted({int(g["id"]) for g in model.games})
    print(f"{len(ids)} juegos en el modelo")

    media = fetch_igdb_media(IGDBClient.from_env(), ids)
    currency = None
    if not args.skip_prices:
        steam_ids = sorted({m["steam"] for m in media.values() if "steam" in m})
        prices, currency = fetch_steam_prices(steam_ids)
        for entry in media.values():
            if entry.get("steam") in prices:
                entry["price"] = prices[entry["steam"]]

    out = args.out or args.model.parent / MEDIA_FILENAME
    payload = {
        "fetched_on": dt.date.today().isoformat(),
        "country": COUNTRY,
        "currency": currency,
        "games": {str(game_id): entry for game_id, entry in sorted(media.items())},
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with_cover = sum(1 for m in media.values() if "cover" in m)
    with_price = sum(1 for m in media.values() if "price" in m)
    print(f"{out}: {with_cover} con portada, {with_price} con precio, de {len(ids)} juegos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
