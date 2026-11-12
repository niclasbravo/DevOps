"""Pruebas del modelo por contenido y de la verificación de integridad."""

import json

import joblib
import pytest

from app.model import load_model, sha256_file
from ml.features import fit_bundle


def test_recomienda_juegos_parecidos(small_model):
    recs = small_model.recommend(["Rogue Game 3"], [], 5)
    assert len(recs) == 5
    assert all(r["title"].startswith("Rogue") for r in recs)


def test_no_recomienda_los_juegos_que_el_usuario_ya_indico(small_model):
    liked = ["Farm Game 0", "Farm Game 1"]
    titles = {r["title"] for r in small_model.recommend(liked, [], 20)}
    assert not titles & set(liked)


def test_resultados_ordenados_por_score(small_model):
    scores = [r["score"] for r in small_model.recommend(["Horror Game 2"], [], 15)]
    assert scores == sorted(scores, reverse=True)


def test_tags_sin_juegos(small_model):
    recs = small_model.recommend([], ["farming"], 3)
    assert recs and all(r["title"].startswith("Farm") for r in recs)


def test_tags_reconocen_alias_de_categorias(small_model):
    # "rpg" coincide con el género "Role-playing (RPG)"
    recs = small_model.recommend([], ["RPG"], 3)
    assert recs and all(r["title"].startswith("Rogue") for r in recs)


def test_titulos_no_distinguen_mayusculas(small_model):
    assert small_model.recommend(["  rogue GAME 3 "], [], 5) == small_model.recommend(["Rogue Game 3"], [], 5)


def test_perfil_vacio_lanza_error(small_model):
    with pytest.raises(ValueError):
        small_model.recommend(["Juego Que No Existe"], ["etiqueta-desconocida-xyz"], 5)


def _save(tmp_path, clean_df):
    path = tmp_path / "gamematch.joblib"
    joblib.dump(fit_bundle(clean_df), path)
    (tmp_path / "model_meta.json").write_text(json.dumps({"sha256": sha256_file(path), "commit": "abc"}))
    return path


def test_carga_modelo_con_hash_valido(tmp_path, clean_df):
    model = load_model(_save(tmp_path, clean_df))
    assert model is not None
    assert model.meta["commit"] == "abc"


def test_rechaza_modelo_alterado(tmp_path, clean_df):
    path = _save(tmp_path, clean_df)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF  # se altera 1 byte
    path.write_bytes(bytes(data))
    assert load_model(path) is None


def test_sin_modelo_devuelve_none(tmp_path):
    assert load_model(tmp_path / "no-existe.joblib") is None


def test_tanimoto_con_vectores_binarios_es_jaccard():
    import numpy as np
    from scipy import sparse

    from app.model import tanimoto
    from app.recommender import jaccard

    games = sparse.csr_matrix(np.array([[1, 1, 0, 0], [1, 0, 1, 1], [0, 0, 1, 1]], dtype=np.float32))
    profile = sparse.csr_matrix(np.array([[1, 1, 1, 0]], dtype=np.float32))
    sims = tanimoto(games, np.asarray(games.sum(axis=1)).ravel(), profile)[:, 0]
    sets = [{"a", "b"}, {"a", "c", "d"}, {"c", "d"}]
    expected = [jaccard(s, {"a", "b", "c"}) for s in sets]
    assert sims == pytest.approx(expected)


def test_recomendacion_trae_ficha_y_razones(small_model):
    recs = small_model.recommend(["Rogue Game 3"], ["permadeath"], 5)
    for rec in recs:
        assert {"title", "score", "year", "genres", "themes", "modes", "reasons"} <= rec.keys()
        assert isinstance(rec["year"], int)
        assert 0 < len(rec["reasons"]) <= 3
        etiquetas = set(rec["genres"]) | set(rec["themes"]) | set(rec["modes"])
        game = small_model.games[small_model.find(rec["title"])]
        assert set(rec["reasons"]) <= etiquetas | set(game["keywords"])
        assert "Indie" not in rec["reasons"] and "Indie" not in rec["genres"]


def test_razones_salen_de_los_tags_escritos(small_model):
    recs = small_model.recommend([], ["farming"], 3)
    assert all("farming" in rec["reasons"] for rec in recs)


def test_recomendacion_incluye_portada_ficha_y_precio(clean_df):
    from app.media import GameMedia
    from ml.features import fit_bundle

    bundle = fit_bundle(clean_df)
    game = next(g for g in bundle["games"] if g["title"] == "Rogue Game 4")
    media = GameMedia(
        games={
            int(game["id"]): {
                "cover": "co1abc",
                "url": "https://www.igdb.com/games/rogue-4",
                "steam": 42,
                "price": 8300,
            }
        }
    )
    from app.model import ContentModel

    model = ContentModel(bundle, media=media)
    rec = next(r for r in model.recommend(["Rogue Game 3"], [], 19) if r["title"] == "Rogue Game 4")
    assert rec["cover_id"] == "co1abc"
    assert rec["url"] == "https://www.igdb.com/games/rogue-4"
    assert rec["store_url"] == "https://store.steampowered.com/app/42/"
    assert rec["price"] == 8300
    listado = {g["title"]: g for g in model.list_games(500, 0)}
    assert listado["Rogue Game 4"]["cover_id"] == "co1abc"
    assert isinstance(listado["Rogue Game 4"]["year"], int)


def test_sin_datos_de_presentacion_los_campos_van_vacios(small_model):
    rec = small_model.recommend(["Rogue Game 3"], [], 1)[0]
    assert rec["cover_id"] is None and rec["url"] is None and rec["store_url"] is None and rec["price"] is None
