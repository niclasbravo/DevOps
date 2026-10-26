"""Paso 2: limpieza y validación de los juegos descargados.

Trata los problemas de calidad del informe: juegos sin resumen, duplicados
(ediciones y remasters), textos en otros idiomas y keywords ruidosas. Si los
datos no pasan la validación se lanza DataValidationError y el pipeline se detiene.
"""

import re

import pandas as pd

REQUIRED_COLUMNS = (
    "id",
    "name",
    "summary",
    "storyline",
    "genres",
    "themes",
    "game_modes",
    "keywords",
    "first_release_date",
    "rating_count",
    "similar_games",
)
LIST_COLUMNS = ("genres", "themes", "game_modes", "keywords")
SEPARATOR = "|"

MIN_WORDS = 5  # un resumen con menos palabras no aporta al TF-IDF
MIN_KEYWORD_GAMES = 3  # keywords en menos juegos se consideran ruido
MAX_EMPTY_TEXT_RATIO = 0.5
MIN_GAMES = 50

# Palabras muy frecuentes del inglés: si un texto casi no las tiene, probablemente está en otro idioma.
_EN_STOPWORDS = frozenset(
    "the a an and or of to in on for with is are be as by it its this that you your from at "
    "their they his her into through can will has have who which while".split()
)
_EDITION_RE = re.compile(
    r"\b(definitive|deluxe|complete|goty|game of the year|anniversary|enhanced|special|ultimate|collector'?s)"
    r"( edition)?\b|\b(remastered|remaster|hd|edition)\b"
)


class DataValidationError(ValueError):
    """Los datos no cumplen las condiciones mínimas para entrenar."""


def split_list(value) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    return [v.strip() for v in value.split(SEPARATOR) if v.strip()]


def is_english(text: str) -> bool:
    """Heurística liviana: texto casi todo ASCII y con palabras comunes del inglés."""
    if not text:
        return False
    ascii_ratio = sum(c.isascii() for c in text) / len(text)
    if ascii_ratio < 0.9:
        return False
    words = re.findall(r"[a-z']+", text.lower())
    if len(words) < 8:
        return True  # muy corto para decidir; el filtro de largo se encarga
    return sum(w in _EN_STOPWORDS for w in words) / len(words) >= 0.12


def normalize_name(name: str) -> str:
    """Nombre sin ediciones ni puntuación: "Hades: Definitive Edition" → "hades"."""
    name = name.lower()
    name = _EDITION_RE.sub(" ", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return " ".join(name.split())


def validate_columns(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(f"Faltan columnas en los datos: {missing}")


def clean_games(raw: pd.DataFrame, min_keyword_games: int = MIN_KEYWORD_GAMES) -> tuple[pd.DataFrame, dict]:
    """Limpia los juegos crudos. Devuelve (DataFrame limpio, reporte con los conteos de cada paso)."""
    validate_columns(raw)
    report = {"raw": len(raw)}
    df = raw.copy()

    for col in LIST_COLUMNS:
        df[col] = df[col].map(split_list)
    df["similar_games"] = df["similar_games"].map(lambda v: [int(x) for x in split_list(str(v)) if x.isdigit()])
    df["rating_count"] = pd.to_numeric(df["rating_count"], errors="coerce").fillna(0).astype(int)
    df["year"] = pd.to_datetime(pd.to_numeric(df["first_release_date"], errors="coerce"), unit="s").dt.year
    df["name"] = df["name"].fillna("").astype(str).str.strip()
    df = df[df["name"] != ""]

    # Texto: resumen + historia. Fuera si no es inglés o es muy corto (queda solo con categorías).
    text = (df["summary"].fillna("").astype(str) + " " + df["storyline"].fillna("").astype(str)).str.strip()
    english = text.map(is_english)
    long_enough = text.str.split().str.len() >= MIN_WORDS
    report["non_english"] = int((~english & (text != "")).sum())
    df["text"] = text.where(english & long_enough, "")

    has_categories = df[["genres", "themes", "game_modes"]].map(len).sum(axis=1) > 0
    keep = (df["text"] != "") | has_categories
    report["dropped_empty"] = int((~keep).sum())
    df = df[keep]

    # Duplicados: mismo nombre normalizado y año. Se queda el más valorado y se unen sus similar_games.
    df["name_key"] = df["name"].map(normalize_name)
    df["year_key"] = df["year"].fillna(-1).astype(int)
    df = df.sort_values("rating_count", ascending=False)
    merged_similar = df.groupby(["name_key", "year_key"])["similar_games"].agg(
        lambda s: sorted({x for v in s for x in v})
    )
    before = len(df)
    df = df.drop_duplicates(["name_key", "year_key"], keep="first").copy()
    df["similar_games"] = [merged_similar[(k, y)] for k, y in zip(df["name_key"], df["year_key"])]
    report["duplicates"] = before - len(df)

    # Keywords ruidosas: cualquiera puede agregarlas en IGDB; se quitan las que casi nadie usa.
    counts = df["keywords"].explode().value_counts()
    common = set(counts[counts >= min_keyword_games].index)
    df["keywords"] = df["keywords"].map(lambda ks: [k for k in ks if k in common])
    report["keywords_kept"] = len(common)
    report["keywords_dropped"] = int((counts < min_keyword_games).sum())

    df = df.sort_values("id").reset_index(drop=True)
    report["clean"] = len(df)
    empty_ratio = float((df["text"] == "").mean()) if len(df) else 1.0
    report["empty_text_ratio"] = round(empty_ratio, 3)

    if len(df) < MIN_GAMES:
        raise DataValidationError(f"Quedan solo {len(df)} juegos después de limpiar (mínimo {MIN_GAMES})")
    if empty_ratio > MAX_EMPTY_TEXT_RATIO:
        raise DataValidationError(
            f"{empty_ratio:.0%} de los juegos quedó sin texto (máximo {MAX_EMPTY_TEXT_RATIO:.0%})"
        )

    columns = ["id", "name", "year", "text", *LIST_COLUMNS, "rating_count", "similar_games"]
    return df[columns], report
