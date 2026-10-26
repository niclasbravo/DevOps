"""Pruebas del pipeline de entrenamiento: limpieza, métricas, evaluación y gate."""

import json

import pandas as pd
import pytest

from ml.clean import DataValidationError, clean_games, is_english, normalize_name
from ml.evaluate import (
    JaccardBaseline,
    evaluate_ranker,
    model_ranker,
    ndcg_at_k,
    precision_at_k,
    similar_games_queries,
)

# --- Limpieza (paso 2) ---

def test_falta_una_columna_detiene_el_pipeline(raw_games):
    with pytest.raises(DataValidationError, match="Faltan columnas"):
        clean_games(raw_games.drop(columns=["summary"]))


def test_duplicados_se_unen_por_nombre_normalizado_y_anio(raw_games):
    dup = raw_games.iloc[[0]].copy()
    dup["id"] = 999
    dup["name"] = "Rogue Game 0: Definitive Edition"
    dup["rating_count"] = 1
    dup["similar_games"] = "42"
    df, report = clean_games(pd.concat([raw_games, dup]))
    assert report["duplicates"] == 1
    row = df[df["name"] == "Rogue Game 0"].iloc[0]
    assert 42 in row["similar_games"]  # se conservan los similar_games de ambas copias


def test_texto_en_otro_idioma_queda_solo_con_categorias(raw_games):
    raw_games.loc[0, "summary"] = "Un juego de mazmorras donde cada partida es distinta y los jefes son difíciles."
    df, report = clean_games(raw_games)
    assert report["non_english"] == 1
    row = df[df["id"] == 1].iloc[0]
    assert row["text"] == ""
    assert row["genres"]  # sigue en el catálogo gracias a sus categorías


def test_keywords_poco_usadas_se_eliminan(raw_games):
    raw_games.loc[0, "keywords"] += "|spam-keyword"
    df, _ = clean_games(raw_games)
    assert all("spam-keyword" not in ks for ks in df["keywords"])


def test_demasiados_juegos_sin_texto_detiene_el_pipeline(raw_games):
    raw_games["summary"] = ""
    with pytest.raises(DataValidationError, match="sin texto"):
        clean_games(raw_games)


def test_heuristicas_de_texto():
    assert is_english("A cozy farming game where you grow crops and befriend the village.")
    assert not is_english("ダンジョンを探索するローグライクゲーム")
    assert normalize_name("Hades: Definitive Edition") == "hades"
    assert normalize_name("Terraria (GOTY)") == "terraria"


# --- Métricas (paso 5) ---

def test_precision_y_ndcg_con_valores_conocidos():
    ranked = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert precision_at_k(ranked, {1, 3}) == pytest.approx(0.2)
    assert ndcg_at_k(ranked, {1, 2}) == pytest.approx(1.0)  # relevantes arriba = ideal
    assert ndcg_at_k([9, 1], {1}) == pytest.approx(1 / 1.5849625, rel=1e-6)  # 1/log2(3)
    assert ndcg_at_k(ranked, set()) == 0.0


def test_modelo_recomienda_dentro_del_mismo_grupo(clean_df, small_model):
    queries = similar_games_queries(clean_df)
    result = evaluate_ranker(model_ranker(small_model), queries)
    assert result["queries"] > 0
    assert result["precision@10"] >= 0.75  # cada juego tiene 8 relevantes: el máximo posible es 0,8
    assert result["ndcg@10"] > 0.9


def test_baseline_jaccard_no_repite_los_juegos_que_gustan(clean_df):
    baseline = JaccardBaseline(clean_df)
    top = baseline.top_indices([0], [], 10)
    assert 0 not in top
    assert len(top) == 10


# --- Entrenamiento completo (pasos 2 a 6) ---

def test_train_escribe_metricas_y_modelo_verificable(tmp_path, raw_games):
    from app.model import load_model
    from ml import train

    csv = tmp_path / "igdb_test.csv"
    raw_games.to_csv(csv, index=False)
    model_out = tmp_path / "models" / "gamematch.joblib"
    code = train.main(["--data", str(csv), "--model-out", str(model_out), "--metrics-dir", str(tmp_path),
                       "--queries", str(tmp_path / "no-existe.json")])

    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert {"model", "jaccard", "passed"} <= metrics.keys()
    assert (tmp_path / "metrics.md").exists()
    assert code == (0 if metrics["passed"] else 1)
    if metrics["passed"]:
        assert load_model(model_out) is not None
