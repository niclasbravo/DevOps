"""Catálogo inicial de juegos indie.

Versión en memoria para el MVP. En entregas futuras se reemplaza por datos
obtenidos desde la API de IGDB.
"""

CATALOG: list[dict] = [
    {"title": "Hollow Knight", "tags": {"metroidvania", "plataformas", "dificil", "exploracion", "atmosferico"}},
    {"title": "Celeste", "tags": {"plataformas", "dificil", "pixel-art", "narrativo", "precision"}},
    {"title": "Stardew Valley", "tags": {"simulacion", "granja", "relajado", "pixel-art", "cooperativo"}},
    {"title": "Hades", "tags": {"roguelike", "accion", "narrativo", "mitologia", "dificil"}},
    {"title": "Dead Cells", "tags": {"roguelike", "metroidvania", "accion", "dificil", "pixel-art"}},
    {"title": "Undertale", "tags": {"rpg", "narrativo", "pixel-art", "humor", "decisiones"}},
    {"title": "Slay the Spire", "tags": {"roguelike", "cartas", "estrategia", "dificil"}},
    {"title": "Outer Wilds", "tags": {"exploracion", "misterio", "espacio", "narrativo", "atmosferico"}},
    {"title": "Disco Elysium", "tags": {"rpg", "narrativo", "decisiones", "misterio", "detective"}},
    {"title": "Inscryption", "tags": {"cartas", "terror", "misterio", "estrategia", "narrativo"}},
    {"title": "Cuphead", "tags": {"accion", "dificil", "plataformas", "cooperativo", "animacion-clasica"}},
    {"title": "Terraria", "tags": {"sandbox", "exploracion", "cooperativo", "pixel-art", "crafteo"}},
]


def find_game(title: str) -> dict | None:
    """Busca un juego por título, sin distinguir mayúsculas ni espacios extra."""
    key = title.strip().lower()
    for game in CATALOG:
        if game["title"].lower() == key:
            return game
    return None
