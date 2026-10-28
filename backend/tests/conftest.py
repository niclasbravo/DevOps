"""Datos sintéticos compartidos por las pruebas del modelo."""

import pandas as pd
import pytest

# Tres grupos bien separados: el modelo debería recomendar dentro del mismo grupo.
GROUPS = {
    "rogue": {
        "genres": "Role-playing (RPG)|Indie",
        "themes": "Action|Fantasy",
        "keywords": "roguelike|permadeath|dungeon",
        "words": "dungeon roguelike permadeath runs procedural weapons boss fights",
    },
    "farm": {
        "genres": "Simulator|Indie",
        "themes": "Sandbox",
        "keywords": "farming|crops|relaxing",
        "words": "farm crops harvest village animals cozy seasons relaxing",
    },
    "horror": {
        "genres": "Adventure|Indie",
        "themes": "Horror|Mystery",
        "keywords": "psychological horror|darkness|puzzle",
        "words": "horror darkness haunted mansion puzzles fear survival mystery",
    },
}
PER_GROUP = 20


def make_raw_games() -> pd.DataFrame:
    rows = []
    gid = 1
    for group, spec in GROUPS.items():
        ids = list(range(gid, gid + PER_GROUP))
        for n, game_id in enumerate(ids):
            rows.append(
                {
                    "id": game_id,
                    "name": f"{group.title()} Game {n}",
                    "summary": f"This is a game about the {spec['words']} and it is played with {n} friends.",
                    "storyline": "",
                    "genres": spec["genres"],
                    "themes": spec["themes"],
                    "game_modes": "Single player",
                    "keywords": spec["keywords"],
                    "first_release_date": 1_600_000_000 + n * 86400 * 30,
                    "rating_count": (n + 1) * 10,
                    "similar_games": "|".join(str(i) for i in [i for i in ids if i != game_id][:8]),
                }
            )
        gid += PER_GROUP
    return pd.DataFrame(rows)


@pytest.fixture
def raw_games() -> pd.DataFrame:
    return make_raw_games()


@pytest.fixture
def clean_df(raw_games):
    from ml.clean import clean_games

    df, _ = clean_games(raw_games)
    return df


@pytest.fixture
def small_model(clean_df):
    from ml.features import fit_model

    return fit_model(clean_df)
