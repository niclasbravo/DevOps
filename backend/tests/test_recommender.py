"""Pruebas unitarias del recomendador."""

import pytest

from app.recommender import jaccard, recommend


def test_jaccard_calcula_similitud_correcta():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b"}, {"c"}) == 0.0
    assert jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)


def test_no_recomienda_juegos_que_el_usuario_ya_conoce():
    recs = recommend(["Hollow Knight", "Celeste"], limit=20)
    titles = {r["title"] for r in recs}
    assert "Hollow Knight" not in titles
    assert "Celeste" not in titles


def test_recomendaciones_ordenadas_por_score_descendente():
    recs = recommend(["Hades"], limit=20)
    scores = [r["score"] for r in recs]
    assert scores == sorted(scores, reverse=True)
    # Dead Cells comparte roguelike, accion, dificil con Hades: debe salir primero
    assert recs[0]["title"] == "Dead Cells"


def test_respeta_el_limite_de_resultados():
    assert len(recommend(["Terraria"], limit=2)) == 2


def test_titulos_no_distinguen_mayusculas():
    assert recommend(["  hollow KNIGHT "]) == recommend(["Hollow Knight"])


def test_perfil_vacio_lanza_error():
    with pytest.raises(ValueError):
        recommend(["Juego Que No Existe"])
