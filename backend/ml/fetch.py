"""Paso 1: descarga de juegos indie y copia en CSV con la fecha.

Uso:
    python -m ml.fetch                        # IGDB (requiere TWITCH_CLIENT_ID/SECRET)
    python -m ml.fetch --max-games 3000
    python -m ml.fetch --source kaggle --csv games.csv   # respaldo: dataset de Steam en Kaggle

Escribe data/raw/<fuente>_<AAAA-MM-DD>.csv con las columnas que espera ml.clean.
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

from ml.clean import REQUIRED_COLUMNS, SEPARATOR
from ml.igdb import PAGE_SIZE, IGDBClient

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
INDIE_GENRE_ID = 32
# game_type de IGDB: 0 juego principal, 4 expansión independiente, 8 remake, 9 remaster, 10 juego ampliado.
# Quedan fuera DLC, bundles, mods, episodios, ports y packs.
GAME_TYPES = (0, 4, 8, 9, 10)
FIELDS = (
    "name,summary,storyline,genres.name,themes.name,game_modes.name,keywords.name,"
    "first_release_date,rating_count,total_rating_count,similar_games,game_type"
)


def _names(items) -> str:
    return SEPARATOR.join(i["name"] for i in items or [] if isinstance(i, dict) and i.get("name"))


def igdb_row(game: dict) -> dict:
    return {
        "id": game["id"],
        "name": game.get("name", ""),
        "summary": game.get("summary", ""),
        "storyline": game.get("storyline", ""),
        "genres": _names(game.get("genres")),
        "themes": _names(game.get("themes")),
        "game_modes": _names(game.get("game_modes")),
        "keywords": _names(game.get("keywords")),
        "first_release_date": game.get("first_release_date"),
        "rating_count": game.get("rating_count", 0),
        "similar_games": SEPARATOR.join(str(s) for s in game.get("similar_games") or []),
    }


def fetch_igdb(client: IGDBClient, max_games: int) -> pd.DataFrame:
    """Juegos indie con resumen, ordenados por cantidad de valoraciones (los más conocidos primero)."""
    where = (
        f"genres = ({INDIE_GENRE_ID}) & summary != null & version_parent = null "
        f"& game_type = ({','.join(map(str, GAME_TYPES))})"
    )
    total = client.query("games/count", f"where {where};")["count"]
    print(f"IGDB tiene {total} juegos indie que cumplen el filtro; se descargan hasta {max_games}")
    rows = []
    for offset in range(0, min(total, max_games), PAGE_SIZE):
        limit = min(PAGE_SIZE, max_games - offset)
        body = f"fields {FIELDS}; where {where}; sort total_rating_count desc; limit {limit}; offset {offset};"
        page = client.query("games", body)
        rows.extend(igdb_row(g) for g in page)
        print(f"  {len(rows)} juegos descargados")
        if len(page) < limit:
            break
    return pd.DataFrame(rows, columns=list(REQUIRED_COLUMNS))


def kaggle_frame(path: Path, max_games: int) -> pd.DataFrame:
    """Adapta el dataset 'Steam Games' de Kaggle (fronkongames) al mismo esquema que IGDB."""
    src = pd.read_csv(path, index_col=False)
    src = src[src["Genres"].fillna("").str.contains("Indie")]
    src = src.assign(rating_count=src["Positive"].fillna(0) + src["Negative"].fillna(0))
    src = src.sort_values("rating_count", ascending=False).head(max_games)

    def modes(categories: str) -> str:
        cats = str(categories)
        found = [m for m, key in (("Single player", "Single-player"), ("Multiplayer", "Multi-player"),
                                  ("Co-operative", "Co-op")) if key in cats]
        return SEPARATOR.join(found)

    release = pd.to_datetime(src["Release date"], errors="coerce", format="mixed")
    return pd.DataFrame(
        {
            "id": src["AppID"],
            "name": src["Name"],
            "summary": src["About the game"].fillna(""),
            "storyline": "",
            "genres": src["Genres"].fillna("").str.replace(",", SEPARATOR),
            "themes": "",
            "game_modes": src["Categories"].map(modes),
            "keywords": src["Tags"].fillna("").str.replace(",", SEPARATOR),
            "first_release_date": (release.astype("int64") // 10**9).where(release.notna()),
            "rating_count": src["rating_count"].astype(int),
            "similar_games": "",
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["igdb", "kaggle"], default="igdb")
    parser.add_argument("--csv", type=Path, help="CSV del dataset de Kaggle (solo con --source kaggle)")
    parser.add_argument("--max-games", type=int, default=8000)
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)

    if args.source == "kaggle":
        if not args.csv:
            parser.error("--source kaggle necesita --csv")
        df = kaggle_frame(args.csv, args.max_games)
    else:
        df = fetch_igdb(IGDBClient.from_env(), args.max_games)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"{args.source}_{dt.date.today().isoformat()}.csv"
    df.to_csv(out, index=False)
    print(f"Guardado {out} ({len(df)} juegos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
