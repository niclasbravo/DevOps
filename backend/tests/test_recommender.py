"""Pruebas unitarias del recomendador base (Jaccard sobre el catálogo manual)."""

import pytest

from app.recommender import jaccard, recommend_jaccard


def test_jaccard_calcula_similitud_correcta():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b"}, {"c"}) == 0.0
    assert jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)


def test_no_recomienda_juegos_que_el_usuario_ya_conoce():
    recs = recommend_jaccard(["Hollow Knight", "Celeste"], limit=20)
    titles = {r["title"] for r in recs}
    assert "Hollow Knight" not in titles
    assert "Celeste" not in titles


def test_recomendaciones_ordenadas_por_score_descendente():
    recs = recommend_jaccard(["Hades"], limit=20)
    scores = [r["score"] for r in recs]
    assert scores == sorted(scores, reverse=True)
    # Dead Cells comparte roguelike, accion, dificil con Hades: debe salir primero
    assert recs[0]["title"] == "Dead Cells"


def test_respeta_el_limite_de_resultados():
    assert len(recommend_jaccard(["Terraria"], limit=2)) == 2


def test_titulos_no_distinguen_mayusculas():
    assert recommend_jaccard(["  hollow KNIGHT "]) == recommend_jaccard(["Hollow Knight"])


def test_perfil_vacio_lanza_error():
    with pytest.raises(ValueError):
        recommend_jaccard(["Juego Que No Existe"])


def test_sin_modelo_recommend_usa_jaccard(monkeypatch):
    import app.recommender as rec

    monkeypatch.setattr(rec, "get_model", lambda: None)
    assert rec.recommend(["Hades"], limit=3) == recommend_jaccard(["Hades"], limit=3)
    assert rec.active_recommender() == "jaccard"


def test_con_modelo_recommend_usa_el_modelo(monkeypatch, small_model):
    import app.recommender as rec

    monkeypatch.setattr(rec, "get_model", lambda: small_model)
    recs = rec.recommend(["Rogue Game 1"], limit=3)
    assert all(r["title"].startswith("Rogue") for r in recs)
    assert rec.active_recommender() == "ml"
