"""Recomendador por similitud de tags (índice de Jaccard).

Es la versión base; en la Entrega 2 se reemplaza por un modelo de ML.
"""

from app.catalog import CATALOG, find_game


def jaccard(a: set[str], b: set[str]) -> float:
    """Similitud de Jaccard entre dos conjuntos: |A ∩ B| / |A ∪ B|."""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def recommend(liked_games: list[str], tags: list[str] | None = None, limit: int = 5) -> list[dict]:
    """Recomienda juegos del catálogo según los juegos que gustan y/o tags de interés.

    - Construye el perfil del usuario uniendo los tags de sus juegos favoritos
      y los tags que indicó explícitamente.
    - Nunca recomienda un juego que el usuario ya indicó.
    - Lanza ValueError si el perfil queda vacío (sin juegos conocidos ni tags).
    """
    if limit < 1:
        raise ValueError("limit debe ser al menos 1")

    liked = [g for g in (find_game(t) for t in liked_games) if g is not None]
    profile: set[str] = set()
    for game in liked:
        profile |= game["tags"]
    profile |= {t.strip().lower() for t in (tags or []) if t.strip()}

    if not profile:
        raise ValueError("Se necesita al menos un juego conocido o un tag de interés")

    liked_titles = {g["title"] for g in liked}
    scored = [
        {"title": g["title"], "score": round(jaccard(profile, g["tags"]), 3)}
        for g in CATALOG
        if g["title"] not in liked_titles
    ]
    scored = [s for s in scored if s["score"] > 0]
    scored.sort(key=lambda s: (-s["score"], s["title"]))
    return scored[:limit]
